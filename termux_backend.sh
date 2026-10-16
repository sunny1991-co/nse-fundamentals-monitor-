#!/data/data/com.termux/files/usr/bin/bash
set -eu

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
mkdir -p runtime
PID_FILE="$PROJECT_DIR/runtime/termux_supervisor.pid"
LAUNCH_LOG="$PROJECT_DIR/runtime/termux_launcher.log"
PYTHON_BIN="${PYTHON_BIN:-python}"

is_running() {
  [ -f "$PID_FILE" ] || return 1
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

start_backend() {
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
    echo "Status: ./termux_backend.sh status"
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

stop_backend() {
  if ! is_running; then
    rm -f "$PID_FILE"
    echo "NSE swing backend is not running."
    return 0
  fi
  pid="$(cat "$PID_FILE")"
  kill "$pid"
  for _ in $(seq 1 20); do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "NSE swing backend stopped."
      return 0
    fi
    sleep 1
  done
  echo "Supervisor did not stop in time; forcing shutdown."
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
}

show_status() {
  if is_running; then
    echo "Backend: RUNNING (PID $(cat "$PID_FILE"))"
  else
    echo "Backend: STOPPED"
  fi
  if [ -f "$PROJECT_DIR/runtime/termux_supervisor_status.json" ]; then
    echo "Supervisor status:"
    cat "$PROJECT_DIR/runtime/termux_supervisor_status.json"
    echo
  fi
  if [ -f "$PROJECT_DIR/runtime/worker_status.json" ]; then
    echo "Worker status:"
    cat "$PROJECT_DIR/runtime/worker_status.json"
    echo
  fi
}

run_maintenance() {
  was_running=0
  if is_running; then
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
  echo "Maintenance status: $PROJECT_DIR/runtime/maintenance_status.json"
  return "$code"
}

install_boot() {
  boot_dir="$HOME/.termux/boot"
  mkdir -p "$boot_dir"
  boot_file="$boot_dir/nse-swing.sh"
  escaped_dir="$(printf '%q' "$PROJECT_DIR")"
  cat >"$boot_file" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
sleep 20
cd $escaped_dir
./termux_backend.sh start >> runtime/boot.log 2>&1
EOF
  chmod 700 "$boot_file"
  echo "Boot script installed: $boot_file"
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
  once) "$PYTHON_BIN" "$PROJECT_DIR/swing_worker.py" --once ;;
  maintenance) run_maintenance ;;
  install-boot) install_boot ;;
  uninstall-boot) uninstall_boot ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|once|maintenance|install-boot|uninstall-boot}"
    exit 2
    ;;
esac
