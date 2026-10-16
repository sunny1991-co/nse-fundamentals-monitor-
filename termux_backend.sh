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

start_backend() {
  bootstrap_data
  if pid_running "$LEGACY_PID_FILE"; then
    echo "Legacy repo-local backend is still running. Run bash ./termux_backend.sh stop first."
    return 1
  fi
  if is_running; then
    echo "NSE swing backend already running. PID $(cat "$PID_FILE")."
    return 0
  fi
  rm -f "$PID_FILE"
  nohup "$PYTHON_BIN" "$PROJECT_DIR/termux_supervisor.py" >>"$LAUNCH_LOG" 2>&1 </dev/null &
  launcher_pid=$!
  sleep 2
  if is_running; then
    echo "NSE swing backend started. PID $(cat "$PID_FILE")."
    echo "Data directory: $DATA_DIR"
    echo "Status: bash ./termux_backend.sh status"
    return 0
  fi
  if kill -0 "$launcher_pid" 2>/dev/null; then
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
}

run_once() {
  bootstrap_data
  "$PYTHON_BIN" "$PROJECT_DIR/swing_worker.py" --once
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
  once) run_once ;;
  maintenance) run_maintenance ;;
  init-data) bootstrap_data ;;
  install-boot) install_boot ;;
  uninstall-boot) uninstall_boot ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|once|maintenance|init-data|install-boot|uninstall-boot}"
    exit 2
    ;;
esac
