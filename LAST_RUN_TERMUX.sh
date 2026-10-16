#!/data/data/com.termux/files/usr/bin/bash
set -eu

PROJECT_DIR="${HOME}/nse-fundamentals-monitor-"
DATA_DIR="${NSE_DATA_DIR:-$HOME/nse-fundamentals-data}"
RUNTIME_DIR="$DATA_DIR/runtime"

cd "$PROJECT_DIR"

# Pull the newest GitHub version, then re-exec this script so the remaining
# steps always run from the just-pulled copy.
if [ "${1:-}" != "--after-pull" ]; then
  git pull --ff-only
  exec bash "$0" --after-pull
fi

echo "=== Restart backend ==="
bash termux_backend.sh restart

echo "=== Wait for supervised workers ==="
sleep 35

echo "=== Backend status ==="
bash termux_backend.sh status

echo "=== Official NSE history worker ==="
cat "$RUNTIME_DIR/history_worker_status.json" 2>/dev/null || echo "history_worker_status.json not available"

echo "=== Official NSE EOD collection ==="
cat "$RUNTIME_DIR/nse_eod_history_status.json" 2>/dev/null || echo "nse_eod_history_status.json not available"

echo "=== Daily research worker ==="
cat "$RUNTIME_DIR/research_worker_status.json" 2>/dev/null || echo "research_worker_status.json not available"

echo "=== Last history log lines ==="
tail -n 80 "$RUNTIME_DIR/history_worker.log" 2>/dev/null || true

echo "=== Last backend log lines ==="
tail -n 80 "$RUNTIME_DIR/termux_backend.log" 2>/dev/null || true

echo "=== LAST RUN COMPLETE ==="
