#!/usr/bin/env bash
set -euo pipefail

command -v uv >/dev/null 2>&1 || { echo "Error: uv required (pip install uv)"; exit 1; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
DEFAULT_PYTHON_VERSION="3.12.13"
PYTHON_VERSION="$DEFAULT_PYTHON_VERSION"
if [[ -f "$ROOT/.python-version" ]]; then
  PINNED_VERSION="$(tr -d '[:space:]' < "$ROOT/.python-version")"
  if [[ -n "$PINNED_VERSION" ]]; then
    PYTHON_VERSION="$PINNED_VERSION"
  fi
fi
export UV_PYTHON="$PYTHON_VERSION"

install_python_runtime() {
  local requested="$1"
  if uv python install "$requested"; then
    return 0
  fi

  if [[ "$requested" =~ ^([0-9]+\.[0-9]+)\.[0-9]+$ ]]; then
    local fallback_minor="${BASH_REMATCH[1]}"
    echo "Python $requested unavailable via uv; falling back to $fallback_minor"
    uv python install "$fallback_minor"
    export UV_PYTHON="$fallback_minor"
    return 0
  fi

  echo "Failed to install Python runtime: $requested"
  return 1
}

detect_build_jobs() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
    return
  fi
  if command -v sysctl >/dev/null 2>&1; then
    sysctl -n hw.logicalcpu 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4
    return
  fi
  echo 4
}

build_native_modules() {
  local jobs
  jobs="$(detect_build_jobs)"
  echo "Building native modules (jobs=$jobs)..."
  uv run scons -j"$jobs" msgq_repo common
}

DEMO_ARGS=()
SELF_TEST=0

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: ./run_notifications.sh [options] [-- demo-args...]

Notification demo mode:
  standalone (default): launch UI + notification publisher

Options:
  --demo-args "... "   Pass args to tools/debug/notification_demo.py
  --self-test          Run demo self-test only (no UI or replay)
  -h, --help           Show this help

Examples:
  ./run_notifications.sh
  ./run_notifications.sh --self-test
  ./run_notifications.sh --demo-args "--dwell-seconds 3 --section-pause-seconds 1"
EOF
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --self-test)
      DEMO_ARGS+=("--self-test")
      SELF_TEST=1
      shift
      ;;
    --demo-args)
      read -r -a parsed <<<"${2:-}"
      DEMO_ARGS+=("${parsed[@]}")
      shift 2
      ;;
    --)
      shift
      DEMO_ARGS+=("$@")
      break
      ;;
    *)
      echo "Unknown option: $1"
      echo "Run ./run_notifications.sh --help"
      exit 1
      ;;
  esac
done

echo "Cleanup old sessions..."
pkill -f "$ROOT/selfdrive/ui/ui.py" 2>/dev/null || true
pkill -f "$ROOT/tools/debug/notification_demo.py" 2>/dev/null || true
sleep 1

echo "Syncing submodules..."
git submodule sync --recursive
git submodule update --init --recursive --force --progress

echo "Syncing Python dependencies..."
echo "Using Python $UV_PYTHON"
install_python_runtime "$UV_PYTHON"
uv sync --frozen --all-extras
build_native_modules
export PATH="$ROOT/.venv/bin:$PATH"

[[ ! -f selfdrive/assets/fonts/Inter-Medium.fnt ]] && uv run python selfdrive/assets/fonts/process.py

export ZMQ=1
# Mici-only runner
export BIG=0
# Deterministic static camera background for notification demo.
export UI_NOTIFICATION_DEMO_STATIC_BG=1

UI_PID=""

cleanup() {
  echo ""
  [[ -n "$UI_PID" ]] && kill "$UI_PID" 2>/dev/null || true
  [[ -n "$UI_PID" ]] && wait "$UI_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if [[ "$SELF_TEST" -eq 1 ]]; then
  echo "Running notification self-test..."
  uv run python tools/debug/notification_demo.py "${DEMO_ARGS[@]}"
  exit $?
fi

echo "Starting UI (mici layout: 536x240)..."
uv run python selfdrive/ui/ui.py &
UI_PID=$!

sleep 2
echo "Starting notification publisher..."
if [[ ${#DEMO_ARGS[@]} -gt 0 ]]; then
  uv run python tools/debug/notification_demo.py --ui-pid "$UI_PID" "${DEMO_ARGS[@]}"
else
  uv run python tools/debug/notification_demo.py --ui-pid "$UI_PID"
fi

echo "Notification demo complete. Stopping UI..."
kill "$UI_PID" 2>/dev/null || true
wait "$UI_PID" 2>/dev/null || true
