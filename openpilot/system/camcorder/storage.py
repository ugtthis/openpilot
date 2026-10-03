"""Stop camcorder writes before they consume loggerd's storage floor."""

import os
import time
from collections.abc import Callable
from pathlib import Path

from openpilot.system.camcorder.clip_storage import clips_root
from openpilot.system.loggerd.config import MIN_STORAGE_BYTES, MIN_STORAGE_PERCENT

CHECK_INTERVAL_S = 1.0
STOP_MARGIN_BYTES = 256 * 1024 * 1024
STOP_MARGIN_PERCENT = 1

# Budgeted above the measured 1.2-1.4 MB/s per take so the estimate errs short.
HEVC_BYTES_PER_S = 5_000_000 // 8  # encoderd's main-camera bitrate
PREVIEW_BYTES_PER_S = 750_000  # JPEG previews measured 0.46-0.59 MB/s
AUDIO_BYTES_PER_S = 48_000 * 2 * 2  # 48 kHz stereo int16
TAKE_BYTES_PER_S = HEVC_BYTES_PER_S + PREVIEW_BYTES_PER_S + AUDIO_BYTES_PER_S


class StorageFullError(RuntimeError):
  pass


def recordable_bytes(stat) -> int:
  """Bytes a take may still write before recording stops; negative below the floor."""
  total_bytes = stat.f_blocks * stat.f_frsize
  floor_bytes = max(MIN_STORAGE_BYTES + STOP_MARGIN_BYTES,
                    total_bytes * (MIN_STORAGE_PERCENT + STOP_MARGIN_PERCENT) // 100)
  return stat.f_bavail * stat.f_frsize - floor_bytes


def has_recording_space(stat) -> bool:
  return recordable_bytes(stat) >= 0


def remaining_recording_s(root: Path | None = None) -> float:
  root = root or clips_root()
  while not root.exists() and root != root.parent:
    root = root.parent
  try:
    return max(0, recordable_bytes(os.statvfs(root))) / TAKE_BYTES_PER_S
  except OSError:
    return 0.0


class StorageMonitor:
  """Throttle filesystem checks while preserving room for finalization."""

  def __init__(self, root: Path | None = None, check_interval_s: float = CHECK_INTERVAL_S,
               clock: Callable[[], float] = time.monotonic):
    self.root = root
    self.check_interval_s = check_interval_s
    self._clock = clock
    self._last_check = 0.0

  def start(self) -> None:
    root = self.root or clips_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".finalize-reserve").unlink(missing_ok=True)  # remove the old reservation scheme
    if not self._has_space(root):
      raise StorageFullError("not enough free storage to record")
    self._last_check = self._clock()

  def available(self) -> bool:
    now = self._clock()
    if now - self._last_check < self.check_interval_s:
      return True
    self._last_check = now
    return self._has_space(self.root or clips_root())

  @staticmethod
  def _has_space(root: Path) -> bool:
    try:
      return has_recording_space(os.statvfs(root))
    except OSError:
      return False
