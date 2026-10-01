"""Protect driving storage and keep enough space to finish an active clip."""

import math
import os
import threading
from pathlib import Path

from openpilot.system.camcorder.clip_storage import clips_root
from openpilot.system.loggerd.config import MIN_STORAGE_BYTES, MIN_STORAGE_PERCENT

FINALIZE_RESERVE_BYTES = 32 * 1024 * 1024
_RESERVE_NAME = ".finalize-reserve"
_FALLBACK_WRITE_BYTES = 1024 * 1024


def has_recording_space(stat, reserve_bytes: int = FINALIZE_RESERVE_BYTES) -> bool:
  reserve_blocks = math.ceil(reserve_bytes / stat.f_frsize)
  remaining_blocks = max(0, stat.f_bavail - reserve_blocks)
  remaining_bytes = remaining_blocks * stat.f_frsize
  remaining_percent = 100.0 * remaining_blocks / stat.f_blocks
  return remaining_bytes >= MIN_STORAGE_BYTES and remaining_percent >= MIN_STORAGE_PERCENT


def _allocate(fd: int, size: int) -> None:
  if hasattr(os, "posix_fallocate"):
    os.posix_fallocate(fd, 0, size)
    return
  block = bytes(min(size, _FALLBACK_WRITE_BYTES))
  remaining = size
  while remaining:
    remaining -= os.write(fd, block[:remaining])


class RecordingStorage:
  """Own an allocated reserve that is released before clip finalization."""

  def __init__(self, root: Path | None = None, reserve_bytes: int = FINALIZE_RESERVE_BYTES):
    self.root = root
    self.reserve_bytes = reserve_bytes
    self._lock = threading.Lock()
    self._path: Path | None = None

  def acquire(self) -> None:
    root = self.root or clips_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / _RESERVE_NAME
    with self._lock:
      if self._path is not None:
        return
      path.unlink(missing_ok=True)  # stale reserve from a killed recorder
      if not has_recording_space(os.statvfs(root), self.reserve_bytes):
        raise RuntimeError("not enough free storage to record")
      fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
      try:
        _allocate(fd, self.reserve_bytes)
      except Exception:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
      os.close(fd)
      self._path = path

  def release(self) -> None:
    with self._lock:
      path, self._path = self._path, None
      if path is not None:
        path.unlink(missing_ok=True)
