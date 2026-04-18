#!/usr/bin/env bash
set -euo pipefail

command -v uv >/dev/null 2>&1 || { echo "Error: uv required (pip install uv)"; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
export UV_PYTHON=3.12
OPENSSL_WRAPPER_DIR=""
REPLAY_LOG="$ROOT/.run_ui_replay.log"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  echo "Usage: ./run_ui.sh [--ui] [--tici] [--fresh] [--dm] [replay args...]"
  echo "  --fresh    Launch setup flow first and reset onboarding for first-use UI"
  echo "  --tici     Use tici layout (2160x1080) instead of mici (536x240)"
  echo "  --dm       After engage, ramp fake DM awareness on the MICI dmoji strip (RUN_UI_SIMULATE_DM=1)"
  echo "Default replay args: --demo"
  exit 0
fi

RUN_REPLAY=1
BIG_UI=0
FRESH_UI=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ui)
      RUN_REPLAY=0
      shift
      ;;
    --tici)
      BIG_UI=1
      shift
      ;;
    --fresh)
      FRESH_UI=1
      shift
      ;;
    --dm)
      export RUN_UI_SIMULATE_DM=1
      shift
      ;;
    *)
      break
      ;;
  esac
done

if [[ "$FRESH_UI" -eq 1 ]]; then
  RUN_REPLAY=0
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
git submodule sync --recursive
# Force checkout so linked worktrees don't leave submodules as metadata-only directories.
git submodule update --init --recursive --force --progress

echo "Syncing Python dependencies..."
uv python install 3.12
uv sync --frozen --all-extras

export PATH="$ROOT/.venv/bin:$PATH"

if [[ "$FRESH_UI" -eq 1 ]]; then
  echo "Resetting onboarding state..."
  python3 - <<'PY'
from openpilot.common.params import Params

params = Params()
for key in ("HasAcceptedTerms", "CompletedTrainingVersion", "PrimeType"):
  params.remove(key)
PY
fi

[[ ! -f selfdrive/assets/fonts/Inter-Medium.fnt ]] && python3 selfdrive/assets/fonts/process.py

if [[ "$(uname -s)" == "Darwin" ]]; then
  OPENSSL_PREFIX=""
  if command -v brew >/dev/null 2>&1; then
    OPENSSL_PREFIX="$(brew --prefix openssl@3 2>/dev/null || brew --prefix openssl 2>/dev/null || true)"
  fi

  if [[ ! -f "$OPENSSL_PREFIX/include/openssl/sha.h" ]]; then
    if command -v brew >/dev/null 2>&1; then
      echo "Installing OpenSSL with Homebrew..."
      brew install openssl@3
      OPENSSL_PREFIX="$(brew --prefix openssl@3 2>/dev/null || true)"
    fi
  fi

  if [[ ! -n "$OPENSSL_PREFIX" || ! -f "$OPENSSL_PREFIX/include/openssl/sha.h" ]]; then
    cat <<'EOF'
Error: replay build needs OpenSSL headers on macOS.

If Homebrew is installed, run:
  brew install openssl@3

Otherwise install Homebrew:
  https://brew.sh/

Then rerun:
  ./run_ui.sh
EOF
    exit 1
  fi

  OPENSSL_WRAPPER_DIR="$(mktemp -d "${TMPDIR:-/tmp}/run_ui_openssl.XXXXXX")"
  for compiler in cc c++ gcc g++ clang clang++; do
    REAL_COMPILER="$(command -v "$compiler" 2>/dev/null || true)"
    [[ -n "$REAL_COMPILER" ]] || continue

    cat > "$OPENSSL_WRAPPER_DIR/$compiler" <<EOF
#!/usr/bin/env bash
set -e
for arg in "\$@"; do
  if [[ "\$arg" == "-c" ]]; then
    exec "$REAL_COMPILER" -I"$OPENSSL_PREFIX/include" "\$@"
  fi
done
exec "$REAL_COMPILER" -I"$OPENSSL_PREFIX/include" -L"$OPENSSL_PREFIX/lib" "\$@"
EOF
    chmod +x "$OPENSSL_WRAPPER_DIR/$compiler"
  done
fi

echo "Building..."
JOBS="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"
if [[ -n "$OPENSSL_WRAPPER_DIR" ]]; then
  PATH="$OPENSSL_WRAPPER_DIR:$PATH" uv run scons -j"$JOBS" msgq_repo common tools/replay/replay
else
  uv run scons -j"$JOBS" msgq_repo common tools/replay/replay
fi

export ZMQ=1
export BIG=$BIG_UI

REPLAY_PID=""
if [[ "$RUN_REPLAY" -eq 1 ]]; then
  echo "Starting replay (${REPLAY_ARGS[*]})..."
  : > "$REPLAY_LOG"
  tools/replay/replay "${REPLAY_ARGS[@]}" >"$REPLAY_LOG" 2>&1 </dev/null &
  REPLAY_PID=$!
  echo "Replay log: $REPLAY_LOG"

  for _ in {1..8}; do
    if ! kill -0 "$REPLAY_PID" 2>/dev/null; then
      echo "Replay exited early. Last log lines:"
      tail -n 40 "$REPLAY_LOG" 2>/dev/null || true
      exit 1
    fi

    if grep -q "failed to load route" "$REPLAY_LOG" || grep -q "Failed to fetch route files from server" "$REPLAY_LOG"; then
      echo "Replay failed to load route. Last log lines:"
      tail -n 40 "$REPLAY_LOG" 2>/dev/null || true
      exit 1
    fi

    if grep -q "loading route" "$REPLAY_LOG"; then
      echo "Replay is loading/downloading route in the background."
      break
    fi

    sleep 1
  done
fi

trap 'echo ""; [[ -n "${REPLAY_PID}" ]] && kill "${REPLAY_PID}" 2>/dev/null || true; [[ -n "${REPLAY_PID}" ]] && wait "${REPLAY_PID}" 2>/dev/null || true; [[ -n "${OPENSSL_WRAPPER_DIR}" ]] && rm -rf "${OPENSSL_WRAPPER_DIR}" 2>/dev/null || true; exit' EXIT INT TERM

sleep 4

if [[ -n "${REPLAY_PID}" ]] && kill -0 "${REPLAY_PID}" 2>/dev/null && grep -q "loading route" "$REPLAY_LOG" 2>/dev/null; then
  echo "Replay may still be downloading route data. Follow progress in $REPLAY_LOG"
fi

if [[ "$FRESH_UI" -eq 1 ]]; then
  echo "Starting setup flow ($([[ "$BIG_UI" -eq 1 ]] && echo "tici" || echo "mici"))..."
  export RUN_UI_AUTO_DM_PASS=1
  if [[ "$BIG_UI" -eq 1 ]]; then
    python3 system/ui/tici_setup.py
  else
    python3 system/ui/mici_setup.py
  fi
fi

echo "Starting UI ($([[ "$BIG_UI" -eq 1 ]] && echo "tici layout: 2160x1080" || echo "mici layout: 536x240"))"
uv run python selfdrive/ui/ui.py
