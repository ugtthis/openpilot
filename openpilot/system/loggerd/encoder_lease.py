"""Ask manager to keep encoderd running while camcorder records.

encoderd normally runs only onroad. A take writes this process's PID here so
parked recordings can still subscribe to the native HEVC stream.
"""

import os
from pathlib import Path

LEASE_PATH = Path("/tmp/openpilot_camcorder_encoder")


def acquire_encoder() -> None:
  LEASE_PATH.write_text(str(os.getpid()), encoding="utf-8")


def release_encoder() -> None:
  try:
    if int(LEASE_PATH.read_text(encoding="utf-8")) == os.getpid():
      LEASE_PATH.unlink(missing_ok=True)
  except (OSError, ValueError):
    pass


def encoder_requested() -> bool:
  try:
    os.kill(int(LEASE_PATH.read_text(encoding="utf-8")), 0)
    return True
  except (OSError, ValueError):
    LEASE_PATH.unlink(missing_ok=True)
    return False
