from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.system.camcorder import storage as storage_module
from openpilot.system.camcorder.storage import (
  STOP_MARGIN_BYTES, STOP_MARGIN_PERCENT, StorageMonitor, has_recording_space,
)
from openpilot.system.loggerd.config import MIN_STORAGE_BYTES, MIN_STORAGE_PERCENT

GIB = 1024**3
BLOCK_SIZE = 4096


def stat(total_bytes: int, available_bytes: int):
  return SimpleNamespace(f_blocks=total_bytes // BLOCK_SIZE,
                         f_bavail=available_bytes // BLOCK_SIZE,
                         f_frsize=BLOCK_SIZE)


def test_recording_space_preserves_loggerd_byte_and_percent_floors():
  assert has_recording_space(stat(50 * GIB, 6 * GIB))
  assert not has_recording_space(stat(20 * GIB, MIN_STORAGE_BYTES + STOP_MARGIN_BYTES - BLOCK_SIZE))
  percent_floor = (MIN_STORAGE_PERCENT + STOP_MARGIN_PERCENT) / 100
  assert not has_recording_space(stat(100 * GIB, int(100 * GIB * percent_floor) - BLOCK_SIZE))


def test_storage_monitor_checks_at_most_once_per_second(tmp_path: Path, monkeypatch):
  now = [10.0]
  enough_space = [True]
  monkeypatch.setattr(storage_module, "has_recording_space", lambda *args: enough_space[0])
  monitor = StorageMonitor(tmp_path, clock=lambda: now[0])

  monitor.start()
  enough_space[0] = False
  assert monitor.available()

  now[0] += 1.0
  assert not monitor.available()


def test_storage_monitor_refuses_to_start_below_the_floor(tmp_path: Path, monkeypatch):
  monkeypatch.setattr(storage_module, "has_recording_space", lambda *args: False)
  monitor = StorageMonitor(tmp_path)

  with pytest.raises(RuntimeError, match="not enough free storage"):
    monitor.start()
