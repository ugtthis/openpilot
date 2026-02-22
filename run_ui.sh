#!/usr/bin/env bash
set -euo pipefail

command -v uv >/dev/null 2>&1 || { echo "Error: uv required (pip install uv)"; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: ./run_ui.sh [--ui] [--tici] [replay args...]"
  echo "  --tici     Use tici layout (2160x1080) instead of mici (536x240)"
  echo "Default replay args: --demo"
  exit 0
fi

RUN_REPLAY=1
BIG_UI=0
if [[ "${1:-}" == "--ui" ]]; then
  RUN_REPLAY=0
  shift
fi
if [[ "${1:-}" == "--tici" ]]; then
  BIG_UI=1
  shift
fi

REPLAY_ARGS=("$@")
if [[ ${#REPLAY_ARGS[@]} -eq 0 ]]; then
  REPLAY_ARGS=("--demo")
fi

echo "Cleanup old sessions..."
pkill -f "$ROOT/tools/replay/replay" 2>/dev/null || true
pkill -f "$ROOT/selfdrive/ui/ui.py" 2>/dev/null || true
sleep 1

echo "Syncing submodules..."
git submodule update --init --recursive

if ! uv run python -c "import PIL" >/dev/null 2>&1; then
  echo "Installing Pillow..."
  uv pip install -q Pillow
  uv run python -c "import PIL" >/dev/null 2>&1
fi

[[ ! -f selfdrive/assets/fonts/Inter-Medium.fnt ]] && uv run python selfdrive/assets/fonts/process.py

if [[ ! -x tools/replay/replay ]] || [[ ! -f msgq_repo/msgq/ipc_pyx.so ]] || [[ ! -f common/params_pyx.so ]]; then
  echo "Building..."
  JOBS="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"
  uv run scons -j"$JOBS" msgq_repo common tools/replay/replay
fi

export ZMQ=1
export BIG=$BIG_UI

REPLAY_PID=""
if [[ "$RUN_REPLAY" -eq 1 ]]; then
  echo "Starting replay (${REPLAY_ARGS[*]})..."
  tools/replay/replay "${REPLAY_ARGS[@]}" &
  REPLAY_PID=$!
fi

trap 'echo ""; [[ -n "${REPLAY_PID}" ]] && kill "${REPLAY_PID}" 2>/dev/null || true; [[ -n "${REPLAY_PID}" ]] && wait "${REPLAY_PID}" 2>/dev/null || true; exit' EXIT INT TERM

sleep 4

echo "Starting UI ($([[ "$BIG_UI" -eq 1 ]] && echo "tici layout: 2160x1080" || echo "mici layout: 536x240"))"
uv run python selfdrive/ui/ui.py
