import os
from openpilot.common.hardware.hw import Paths


CAMERA_FPS = 20
SEGMENT_LENGTH = 60
MIN_STORAGE_BYTES = 5 * 1024 * 1024 * 1024
MIN_STORAGE_PERCENT = 10

def get_available_percent(default: float, reserved_bytes: int = 0) -> float:
  try:
    statvfs = os.statvfs(Paths.log_root())
    available_bytes = statvfs.f_bavail * statvfs.f_frsize - reserved_bytes
    available_percent = 100.0 * available_bytes / (statvfs.f_blocks * statvfs.f_frsize)
  except OSError:
    available_percent = default

  return available_percent


def get_available_bytes(default: int, reserved_bytes: int = 0) -> int:
  try:
    statvfs = os.statvfs(Paths.log_root())
    available_bytes = statvfs.f_bavail * statvfs.f_frsize - reserved_bytes
  except OSError:
    available_bytes = default

  return available_bytes
