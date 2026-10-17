#!/data/data/com.termux/files/usr/bin/bash
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python}"
DATA_DIR="${NSE_DATA_DIR:-$HOME/nse-fundamentals-data}"
export NSE_DATA_DIR="$DATA_DIR"
RUNTIME_DIR="$DATA_DIR/runtime"
mkdir -p "$RUNTIME_DIR"
chmod 700 "$DATA_DIR" "$RUNTIME_DIR" 2>/dev/null || true

PID_FILE="$RUNTIME_DIR/termux_supervisor.pid"
LAUNCH_LOG="$RUNTIME_DIR/termux_launcher.log"
LEGACY_PID_FILE="$PROJECT_DIR/runtime/termux_supervisor.pid"
WATCHDOG_PID_FILE="$RUNTIME_DIR/termux_watchdog.pid"
WATCHDOG_ENABLE_FILE="$RUNTIME_DIR/termux_watchdog.enabled"
WATCHDOG_LOG="$RUNTIME_DIR/termux_watchdog.log"

bootstrap_data() {
  "$PYTHON_BIN" "$PROJECT_DIR/termux_bootstrap_data.py" \
    --code-root "$PROJECT_DIR" --data-root "$DATA_DIR"
}

pid_running() {
  file="$1"
  [ -f "$file" ] || return 1
  pid="$(cat "$file" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

is_running() {
  pid_running "$PID_FILE"
}

watchdog_running() {
  pid_running "$WATCHDOG_PID_FILE" || return 1
  pid="$(cat "$WATCHDOG_PID_FILE")"
  [ -r "/proc/$pid/cmdline" ] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq "$PROJECT_DIR/termux_watchdog.py"
}

start_watchdog() {
  printf 'enabled\n' > "$WATCHDOG_ENABLE_FILE"
  chmod 600 "$WATCHDOG_ENABLE_FILE"
  if watchdog_running; then
    return 0
  fi
  rm -f "$WATCHDOG_PID_FILE"
  nohup "$PYTHON_BIN" "$PROJECT_DIR/termux_watchdog.py" >>"$WATCHDOG_LOG" 2>&1 </dev/null &
  echo "Independent watchdog started (RAM-aware, 2-minute heartbeat checks)."
}

stop_watchdog() {
  # Exit naturally on the next <=5-second flag check. Never kill unknown PID.
  rm -f "$WATCHDOG_ENABLE_FILE"
}

start_backend() {
  bootstrap_data
  if pid_running "$LEGACY_PID_FILE"; then
    echo "Legacy repo-local backend is still running. Run bash ./termux_backend.sh stop first."
    return 1
  fi
  if is_running; then
    echo "NSE swing backend already running. PID $(cat "$PID_FILE")."
    start_watchdog
    return 0
  fi
  rm -f "$PID_FILE"
  nohup "$PYTHON_BIN" "$PROJECT_DIR/termux_supervisor.py" >>"$LAUNCH_LOG" 2>&1 </dev/null &
  launcher_pid=$!
  sleep 2
  if is_running; then
    echo "NSE swing backend started. PID $(cat "$PID_FILE")."
    start_watchdog
    echo "Data directory: $DATA_DIR"
    echo "Status: bash ./termux_backend.sh status"
    return 0
  fi
  if kill -0 "$launcher_pid" 2>/dev/null; then
    start_watchdog
    echo "Supervisor is starting but PID file is not ready yet. Check again in a few seconds."
    return 0
  fi
  echo "Backend did not start. Last log lines:"
  tail -n 30 "$LAUNCH_LOG" 2>/dev/null || true
  return 1
}

stop_one() {
  file="$1"
  label="$2"
  if ! pid_running "$file"; then
    rm -f "$file"
    return 1
  fi
  pid="$(cat "$file")"
  kill "$pid"
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "$label stopped."
      rm -f "$file" 2>/dev/null || true
      return 0
    fi
    sleep 1
  done
  echo "$label did not stop in time; forcing shutdown."
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$file"
  return 0
}

stop_backend() {
  stop_watchdog
  stopped=0
  if stop_one "$PID_FILE" "NSE swing backend"; then stopped=1; fi
  if [ "$LEGACY_PID_FILE" != "$PID_FILE" ] && stop_one "$LEGACY_PID_FILE" "Legacy repo-local backend"; then stopped=1; fi
  if [ "$stopped" -eq 0 ]; then
    echo "NSE swing backend is not running."
  fi
}

show_status() {
  if is_running; then
    echo "Backend: RUNNING (PID $(cat "$PID_FILE"))"
  elif pid_running "$LEGACY_PID_FILE"; then
    echo "Backend: LEGACY_REPO_LOCAL_RUNNING (PID $(cat "$LEGACY_PID_FILE"))"
  else
    echo "Backend: STOPPED"
  fi
  echo "Data directory: $DATA_DIR"
  if [ -f "$RUNTIME_DIR/termux_watchdog_status.json" ]; then
    echo "Independent watchdog status:"
    cat "$RUNTIME_DIR/termux_watchdog_status.json"
    echo
  fi
  if [ -f "$RUNTIME_DIR/termux_supervisor_status.json" ]; then
    echo "Supervisor status:"
    cat "$RUNTIME_DIR/termux_supervisor_status.json"
    echo
  fi
  if [ -f "$RUNTIME_DIR/worker_status.json" ]; then
    echo "Worker status:"
    cat "$RUNTIME_DIR/worker_status.json"
    echo
  fi
  if [ -f "$RUNTIME_DIR/history_worker_status.json" ]; then
    echo "Official NSE history worker status:"
    cat "$RUNTIME_DIR/history_worker_status.json"
    echo
  fi
  if [ -f "$RUNTIME_DIR/research_worker_status.json" ]; then
    echo "Daily research maintenance status:"
    cat "$RUNTIME_DIR/research_worker_status.json"
    echo
  fi
  if [ -f "$RUNTIME_DIR/upstox_reference_worker_status.json" ]; then
    echo "Automatic Upstox NSE_EQ vendor-reference status (read-only):"
    cat "$RUNTIME_DIR/upstox_reference_worker_status.json"
    echo
  fi
  echo "Project progress summary:"
  "$PYTHON_BIN" "$PROJECT_DIR/project_status.py" 2>/dev/null || echo '{"stage":"STATUS_UNAVAILABLE"}'
}

run_once() {
  bootstrap_data
  "$PYTHON_BIN" "$PROJECT_DIR/swing_worker.py" --once
}

run_fast_fundamentals() {
  bootstrap_data
  "$PYTHON_BIN" "$PROJECT_DIR/fast_fundamental_screen.py"
  "$PYTHON_BIN" "$PROJECT_DIR/project_status.py"
}

run_backup() {
  bootstrap_data
  was_running=0
  if is_running || pid_running "$LEGACY_PID_FILE"; then
    was_running=1
    stop_backend
  fi
  if "$PYTHON_BIN" "$PROJECT_DIR/termux_backup.py" backup \
      --code-root "$PROJECT_DIR" --data-root "$DATA_DIR"; then
    code=0
  else
    code=$?
  fi
  if [ "$was_running" -eq 1 ]; then
    start_backend
  fi
  return "$code"
}

restore_backup() {
  backup_file="${2:-}"
  if [ -z "$backup_file" ]; then
    echo "Usage: $0 restore-backup /full/path/to/backup.nsebak"
    return 2
  fi
  stop_backend
  "$PYTHON_BIN" "$PROJECT_DIR/termux_backup.py" restore \
    --code-root "$PROJECT_DIR" --data-root "$DATA_DIR" --input "$backup_file"
  bootstrap_data
  install_boot
  start_backend
  show_status
}

recover_project() {
  echo "Repairing Git-tracked code from origin/main while preserving private data and .env."
  git fetch origin main
  stop_backend
  git reset --hard origin/main
  if ! "$PYTHON_BIN" -m pip install -r "$PROJECT_DIR/requirements.txt"; then
    echo "Dependency repair failed. Attempting to restart the backend with the available environment."
    start_backend || true
    return 1
  fi
  bootstrap_data
  install_boot
  start_backend
  sleep 3
  show_status
}

full_restore() {
  backup_file="${2:-}"
  if [ -z "$backup_file" ]; then
    echo "Usage: $0 full-restore /full/path/to/backup.nsebak"
    return 2
  fi
  echo "Full restore: Git code + dependencies + encrypted private state + boot startup."
  git fetch origin main
  stop_backend
  git reset --hard origin/main
  "$PYTHON_BIN" -m pip install -r "$PROJECT_DIR/requirements.txt"
  "$PYTHON_BIN" "$PROJECT_DIR/termux_backup.py" restore \
    --code-root "$PROJECT_DIR" --data-root "$DATA_DIR" --input "$backup_file"
  bootstrap_data
  install_boot
  start_backend
  sleep 3
  show_status
}

run_maintenance() {
  bootstrap_data
  was_running=0
  if is_running || pid_running "$LEGACY_PID_FILE"; then
    was_running=1
    stop_backend
  fi
  echo "Running bounded non-Upstox maintenance. This can take a while."
  if "$PYTHON_BIN" "$PROJECT_DIR/termux_maintenance.py"; then
    code=0
  else
    code=$?
  fi
  if [ "$was_running" -eq 1 ]; then
    start_backend
  fi
  echo "Maintenance status: $RUNTIME_DIR/maintenance_status.json"
  return "$code"
}

install_boot() {
  bootstrap_data
  boot_dir="$HOME/.termux/boot"
  mkdir -p "$boot_dir"
  boot_file="$boot_dir/nse-swing.sh"
  escaped_dir="$(printf '%q' "$PROJECT_DIR")"
  escaped_data="$(printf '%q' "$DATA_DIR")"
  cat >"$boot_file" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
sleep 20
export NSE_DATA_DIR=$escaped_data
cd $escaped_dir
bash ./termux_backend.sh start >> $escaped_data/runtime/boot.log 2>&1
EOF
  chmod 700 "$boot_file"
  echo "Boot script installed: $boot_file"
  echo "Persistent data directory: $DATA_DIR"
  echo "Termux:Boot Android app must also be installed and opened once."
}

uninstall_boot() {
  rm -f "$HOME/.termux/boot/nse-swing.sh"
  echo "Boot script removed."
}

case "${1:-status}" in
  start) start_backend ;;
  stop) stop_backend ;;
  restart) stop_backend; start_backend ;;
  status) show_status ;;
  resources) "$PYTHON_BIN" -m research_monitor.termux_resources ;;
  once) run_once ;;
  fast-fundamentals) run_fast_fundamentals ;;
  backup) run_backup ;;
  restore-backup) restore_backup "$@" ;;
  recover) recover_project ;;
  full-restore) full_restore "$@" ;;
  maintenance) run_maintenance ;;
  init-data) bootstrap_data ;;
  install-boot) install_boot ;;
  uninstall-boot) uninstall_boot ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|resources|once|fast-fundamentals|backup|restore-backup|recover|full-restore|maintenance|init-data|install-boot|uninstall-boot}"
    exit 2
    ;;
esac
