"""Where camcorder clips live and how much of the disk they get.

loggerd's deleter imports this, so it must stay light and must never raise.

  CAMCORDER_QUOTA_BYTES    the camcorder library never grows past this
  CAMCORDER_RESERVE_BYTES  the deleter keeps this much room free for clips, minus
                           what clips already use, by removing old drives sooner

Clips past the reserve only get leftover space. camcorderd also stops a margin
above loggerd's floor, so the recordable guarantee is the reserve minus that
margin (storage.STOP_MARGIN_*).
"""

import os
from pathlib import Path

from openpilot.common.hardware import PC
from openpilot.common.hardware.hw import Paths

CAMCORDER_QUOTA_BYTES = 20 * 1024 * 1024 * 1024
CAMCORDER_RESERVE_BYTES = 10 * 1024 * 1024 * 1024


def clips_root() -> Path:
  override = os.environ.get("CAMCORDER_CLIPS")
  if override:
    return Path(override)
  if PC:
    return Path(Paths.comma_home()) / "media" / "0" / "camcorder"
  return Path("/data/media/0/camcorder")


def library_bytes(root: Path | None = None) -> int:
  """Disk space used by everything under the camcorder folder, including unfinished takes."""
  used = 0
  for dirpath, _, filenames in os.walk(root or clips_root()):
    for name in filenames:
      try:
        used += os.lstat(os.path.join(dirpath, name)).st_blocks * 512
      except OSError:
        pass
  return used


def unused_reserve_bytes(root: Path | None = None) -> int:
  """Free space the deleter holds back for clips not recorded yet; recording shrinks it 1:1."""
  return max(0, CAMCORDER_RESERVE_BYTES - library_bytes(root))
