#!/usr/bin/env bash
# RasaOps — macOS setup (Apple Silicon + Intel)
# Usage:  bash scripts/mac/setup_mac.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "==> RasaOps macOS setup"
echo "    root: $ROOT"

# Prefer python3.12/3.11 if available
PY="${RASAOPS_PYTHON:-}"
if [[ -z "$PY" ]]; then
  for c in python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      PY="$c"
      break
    fi
  done
fi
if [[ -z "${PY:-}" ]]; then
  echo "ERROR: python3 not found. Install via Homebrew: brew install python@3.12" >&2
  exit 1
fi

echo "==> Python: $($PY --version)"

# Optional system deps (Homebrew)
if command -v brew >/dev/null 2>&1; then
  echo "==> Checking Homebrew packages (ffmpeg optional for video fixtures)..."
  brew list ffmpeg >/dev/null 2>&1 || echo "    tip: brew install ffmpeg  # optional"
else
  echo "    tip: install Homebrew for ffmpeg/system libs — https://brew.sh"
fi

if [[ ! -d .venv ]]; then
  echo "==> Creating venv..."
  "$PY" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools

echo "==> Installing package [dev,edge] (includes ultralytics for host high-recall)..."
# On Apple Silicon, torch/ultralytics usually install CPU or MPS wheels from PyPI
pip install -e ".[dev,edge]"

echo "==> Ensuring CI tiny ONNX exists..."
python edge/scripts/make_tiny_onnx.py || true

echo "==> Running tests..."
pytest -q

echo ""
echo "Setup complete."
echo "  Activate:  source .venv/bin/activate"
echo "  Demo:      bash scripts/mac/start_demo.sh"
echo "  Webcam:    python -m rasaops_edge.scripts.run_edge_agent --camera 0 --with-cloud"
echo ""
echo "Notes for macOS:"
echo "  - First camera use may prompt for Camera permission (System Settings → Privacy)."
echo "  - OpenCV GUI (live preview) needs a desktop session (not pure SSH without display)."
echo "  - Product Pi path still uses ONNX 320; host lab uses Ultralytics 640 when yolo11n.pt is present."
