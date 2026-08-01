#!/usr/bin/env bash
# RasaOps — macOS demo launcher (agent + optional live preview + browser)
#
# Usage:
#   bash scripts/mac/start_demo.sh
#   bash scripts/mac/start_demo.sh --youtube "https://www.youtube.com/watch?v=0JGQo-vAgwQ" --scene pub_bar
#   bash scripts/mac/start_demo.sh --no-preview
#   bash scripts/mac/start_demo.sh --camera 0
#   bash scripts/mac/stop_demo.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

YOUTUBE="${RASAOPS_YOUTUBE:-https://www.youtube.com/watch?v=0JGQo-vAgwQ}"
SCENE="${RASAOPS_SCENE:-auto}"
AGENT_FPS="${RASAOPS_AGENT_FPS:-0.5}"
PREVIEW_FPS="${RASAOPS_PREVIEW_FPS:-3}"
MAX_HEIGHT="${RASAOPS_LIVE_MAX_HEIGHT:-480}"
PORT="${RASAOPS_PORT:-8090}"
CLOUD_PORT="${RASAOPS_CLOUD_PORT:-18080}"
CAMERA=""
NO_PREVIEW=0
NO_CLOUD=0
BACKEND="${RASAOPS_INFERENCE_BACKEND:-auto}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --youtube) YOUTUBE="$2"; shift 2 ;;
    --scene) SCENE="$2"; shift 2 ;;
    --camera) CAMERA="$2"; shift 2 ;;
    --agent-fps) AGENT_FPS="$2"; shift 2 ;;
    --preview-fps) PREVIEW_FPS="$2"; shift 2 ;;
    --max-height) MAX_HEIGHT="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --cloud-port) CLOUD_PORT="$2"; shift 2 ;;
    --backend) BACKEND="$2"; shift 2 ;;
    --no-preview) NO_PREVIEW=1; shift ;;
    --no-cloud) NO_CLOUD=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv — run: bash scripts/mac/setup_mac.sh" >&2
  exit 1
fi

PY="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/data/edge/logs"
PID_DIR="$ROOT/data/edge/pids"
mkdir -p "$LOG_DIR" "$PID_DIR"

# Host high-recall on Mac (Ultralytics 640 when .pt present)
export RASAOPS_HOST_HIGH_RECALL="${RASAOPS_HOST_HIGH_RECALL:-1}"
export RASAOPS_IMGSZ="${RASAOPS_IMGSZ:-640}"
export RASAOPS_CONF="${RASAOPS_CONF:-0.15}"
export RASAOPS_YT_COOKIES_FROM_BROWSER="${RASAOPS_YT_COOKIES_FROM_BROWSER:-0}"

echo "==> Stopping previous demo processes..."
bash "$ROOT/scripts/mac/stop_demo.sh" || true
sleep 1

AGENT_ARGS=(
  -m rasaops_edge.scripts.run_edge_agent
  --scene "$SCENE"
  --fps "$AGENT_FPS"
  --port "$PORT"
  --improve-minutes 5
  --backend "$BACKEND"
)
if [[ -n "$CAMERA" ]]; then
  AGENT_ARGS+=(--camera "$CAMERA")
else
  AGENT_ARGS+=(--youtube "$YOUTUBE")
fi
if [[ "$NO_CLOUD" -eq 0 ]]; then
  AGENT_ARGS+=(--with-cloud --cloud-port "$CLOUD_PORT")
fi

echo "==> Starting edge agent..."
if [[ -n "$CAMERA" ]]; then
  echo "    camera=$CAMERA scene=$SCENE fps=$AGENT_FPS"
else
  echo "    youtube=$YOUTUBE scene=$SCENE fps=$AGENT_FPS"
fi

RASAOPS_ORT_THREADS="${RASAOPS_ORT_THREADS:-4}" \
  nohup "$PY" "${AGENT_ARGS[@]}" \
  >"$LOG_DIR/agent.log" 2>&1 &
echo $! >"$PID_DIR/agent.pid"
echo "    agent pid=$(cat "$PID_DIR/agent.pid")  log=$LOG_DIR/agent.log"

if [[ "$NO_PREVIEW" -eq 0 ]]; then
  PREVIEW_ARGS=(
    -m rasaops_edge.scripts.run_live_view
    --scene "$SCENE"
    --fps "$PREVIEW_FPS"
    --max-height "$MAX_HEIGHT"
    --high-priority
    --backend "$BACKEND"
  )
  if [[ -n "$CAMERA" ]]; then
    PREVIEW_ARGS+=(--camera "$CAMERA")
  else
    PREVIEW_ARGS+=(--youtube "$YOUTUBE")
  fi
  echo "==> Starting live preview (OpenCV window)..."
  RASAOPS_ORT_THREADS="${RASAOPS_ORT_THREADS_PREVIEW:-8}" \
    nohup "$PY" "${PREVIEW_ARGS[@]}" \
    >"$LOG_DIR/preview.log" 2>&1 &
  echo $! >"$PID_DIR/preview.pid"
  echo "    preview pid=$(cat "$PID_DIR/preview.pid")  log=$LOG_DIR/preview.log"
fi

echo "==> Waiting for kiosk on :$PORT ..."
OK=0
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null 2>&1; then
    OK=1
    break
  fi
  sleep 1
done

if [[ "$OK" -eq 1 ]]; then
  echo "==> Kiosk ready — opening browser"
  if command -v open >/dev/null 2>&1; then
    open "http://127.0.0.1:${PORT}/"
  fi
else
  echo "==> Kiosk not ready yet — check $LOG_DIR/agent.log"
fi

echo ""
echo "URLs:"
echo "  Kiosk:   http://127.0.0.1:${PORT}/"
echo "  Metrics: http://127.0.0.1:${PORT}/local/metrics"
echo "  Scene:   http://127.0.0.1:${PORT}/local/scene"
if [[ "$NO_CLOUD" -eq 0 ]]; then
  echo "  Cloud:   http://127.0.0.1:${CLOUD_PORT}/health"
fi
echo ""
echo "Stop with: bash scripts/mac/stop_demo.sh"
