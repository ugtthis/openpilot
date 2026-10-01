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


class StorageFullError(RuntimeError):
  pass


def has_recording_space(stat) -> bool:
  available_bytes = stat.f_bavail * stat.f_frsize
  available_percent = 100.0 * stat.f_bavail / stat.f_blocks
  return (available_bytes >= MIN_STORAGE_BYTES + STOP_MARGIN_BYTES and
          available_percent >= MIN_STORAGE_PERCENT + STOP_MARGIN_PERCENT)


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
