from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.system.camcorder import storage as storage_module
from openpilot.system.camcorder.storage import (
  CAMCORDER_QUOTA_BYTES, STOP_MARGIN_BYTES, STOP_MARGIN_PERCENT, TAKE_BYTES_PER_S, StorageMonitor, library_bytes,
  measure_recordable_bytes, recordable_bytes,
)
from openpilot.system.loggerd.config import MIN_STORAGE_BYTES, MIN_STORAGE_PERCENT

GIB = 1024**3
BLOCK_SIZE = 4096


def stat(total_bytes: int, available_bytes: int):
  return SimpleNamespace(f_blocks=total_bytes // BLOCK_SIZE,
                         f_bavail=available_bytes // BLOCK_SIZE,
                         f_frsize=BLOCK_SIZE)


def test_recording_space_preserves_loggerd_byte_and_percent_floors():
  assert recordable_bytes(stat(50 * GIB, 6 * GIB), 0) >= 0
  assert recordable_bytes(stat(20 * GIB, MIN_STORAGE_BYTES + STOP_MARGIN_BYTES - BLOCK_SIZE), 0) < 0
  percent_floor = (MIN_STORAGE_PERCENT + STOP_MARGIN_PERCENT) / 100
  assert recordable_bytes(stat(100 * GIB, int(100 * GIB * percent_floor) - BLOCK_SIZE), 0) < 0


def test_camcorder_quota_caps_recording_even_with_free_disk():
  roomy_disk = stat(100 * GIB, 60 * GIB)

  assert recordable_bytes(roomy_disk, 0) == CAMCORDER_QUOTA_BYTES
  assert recordable_bytes(roomy_disk, CAMCORDER_QUOTA_BYTES - GIB) == GIB
  assert recordable_bytes(roomy_disk, CAMCORDER_QUOTA_BYTES) == 0
  assert recordable_bytes(roomy_disk, CAMCORDER_QUOTA_BYTES + 1) < 0


def test_loggerd_floor_wins_when_drives_fill_the_disk():
  crowded_disk = stat(100 * GIB, 13 * GIB)
  floor_limited = recordable_bytes(crowded_disk, 0)

  assert 0 < floor_limited < CAMCORDER_QUOTA_BYTES
  assert recordable_bytes(crowded_disk, GIB) == floor_limited


def test_library_bytes_counts_every_file_under_the_camcorder_folder(tmp_path: Path):
  (tmp_path / "clip-a").mkdir()
  (tmp_path / "clip-a" / "video.hevc").write_bytes(b"x" * 10_000)
  (tmp_path / "clip-b").mkdir()
  (tmp_path / "clip-b" / "audio.s16le").write_bytes(b"x" * 5_000)

  assert library_bytes(tmp_path) >= 15_000
  assert library_bytes(tmp_path / "missing") == 0


def test_recordable_bytes_measured_before_the_first_clip_folder_exists(tmp_path: Path, monkeypatch):
  checked = []
  monkeypatch.setattr(storage_module.os, "statvfs", lambda root: checked.append(root) or stat(100 * GIB, 60 * GIB))

  assert measure_recordable_bytes(tmp_path / "camcorder" / "not-yet-created") == CAMCORDER_QUOTA_BYTES
  assert checked == [tmp_path]


def test_remaining_time_counts_only_recordable_bytes(tmp_path: Path, monkeypatch):
  monkeypatch.setattr(storage_module, "measure_recordable_bytes", lambda root: 10 * TAKE_BYTES_PER_S)
  assert StorageMonitor(tmp_path).remaining_s() == 10.0

  monkeypatch.setattr(storage_module, "measure_recordable_bytes", lambda root: -1)
  assert StorageMonitor(tmp_path).remaining_s() == 0.0


def test_storage_monitor_measures_at_most_once_per_second(tmp_path: Path, monkeypatch):
  now = [10.0]
  recordable = [GIB]
  measured = []
  monkeypatch.setattr(storage_module, "measure_recordable_bytes", lambda root: measured.append(root) or recordable[0])
  monitor = StorageMonitor(tmp_path, clock=lambda: now[0])

  monitor.start()
  recordable[0] = -1
  assert monitor.available()
  assert monitor.remaining_s() == GIB / TAKE_BYTES_PER_S
  assert len(measured) == 1

  now[0] += 1.0
  assert not monitor.available()
  assert len(measured) == 2


def test_storage_monitor_refuses_to_start_when_full(tmp_path: Path, monkeypatch):
  monkeypatch.setattr(storage_module, "measure_recordable_bytes", lambda root: -1)
  monitor = StorageMonitor(tmp_path)

  with pytest.raises(RuntimeError, match="not enough storage"):
    monitor.start()
