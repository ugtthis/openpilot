"""Keep camcorder clips inside their own budget and out of loggerd's storage floor.

A take may write only while both hold:
  - the camcorder library stays under CAMCORDER_QUOTA_BYTES
  - free space stays above loggerd's floor plus a stop margin

deleter never removes camcorder clips. It holds back the unused part of
CAMCORDER_RESERVE_BYTES instead, so the first CAMCORDER_RESERVE_BYTES of clips
(minus the stop margin below) always have room even when drives fill the disk.
"""

import os
import time
from collections.abc import Callable
from pathlib import Path

from openpilot.system.camcorder.library import CAMCORDER_QUOTA_BYTES, clips_root, library_bytes
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


def recordable_bytes(stat, used_bytes: int) -> int:
  """Bytes a take may still write; negative once the floor or the quota is reached."""
  total_bytes = stat.f_blocks * stat.f_frsize
  floor_bytes = max(MIN_STORAGE_BYTES + STOP_MARGIN_BYTES,
                    total_bytes * (MIN_STORAGE_PERCENT + STOP_MARGIN_PERCENT) // 100)
  above_floor = stat.f_bavail * stat.f_frsize - floor_bytes
  under_quota = CAMCORDER_QUOTA_BYTES - used_bytes
  return min(above_floor, under_quota)


def measure_recordable_bytes(root: Path) -> int:
  disk = root
  while not disk.exists() and disk != disk.parent:
    disk = disk.parent
  try:
    return recordable_bytes(os.statvfs(disk), library_bytes(root))
  except OSError:
    return -1


class StorageMonitor:
  """Measure recordable space at most once per interval; takes and the time-left chip share it."""

  def __init__(self, root: Path | None = None, check_interval_s: float = CHECK_INTERVAL_S,
               clock: Callable[[], float] = time.monotonic):
    self.root = root
    self.check_interval_s = check_interval_s
    self._clock = clock
    self._last_check: float | None = None
    self._recordable_bytes = 0

  def start(self) -> None:
    root = self.root or clips_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".finalize-reserve").unlink(missing_ok=True)  # remove the old reservation scheme
    if self._measure() < 0:
      raise StorageFullError("not enough storage to record")

  def available(self) -> bool:
    return self._recordable() >= 0

  def remaining_s(self) -> float:
    return max(0, self._recordable()) / TAKE_BYTES_PER_S

  def _recordable(self) -> int:
    if self._last_check is None or self._clock() - self._last_check >= self.check_interval_s:
      self._measure()
    return self._recordable_bytes

  def _measure(self) -> int:
    self._last_check = self._clock()
    self._recordable_bytes = measure_recordable_bytes(self.root or clips_root())
    return self._recordable_bytes
