#!/usr/bin/env bash
# Stop RasaOps demo processes started by start_demo.sh (and stragglers).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_DIR="$ROOT/data/edge/pids"

stop_pidfile() {
  local f="$1"
  if [[ -f "$f" ]]; then
    local pid
    pid="$(cat "$f" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "  kill $pid ($(basename "$f" .pid))"
      kill "$pid" 2>/dev/null || true
      sleep 0.3
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$f"
  fi
}

echo "Stopping RasaOps demo..."
if [[ -d "$PID_DIR" ]]; then
  stop_pidfile "$PID_DIR/agent.pid"
  stop_pidfile "$PID_DIR/preview.pid"
fi

# Stragglers by module name (macOS pgrep)
if command -v pgrep >/dev/null 2>&1; then
  for pat in \
    "rasaops_edge.scripts.run_edge_agent" \
    "rasaops_edge.scripts.run_live_view" \
    "rasaops_edge.scripts.run_webcam_dogfood"; do
    pgrep -f "$pat" 2>/dev/null | while read -r pid; do
      echo "  kill stray $pid ($pat)"
      kill "$pid" 2>/dev/null || true
    done || true
  done
fi

echo "Done."
