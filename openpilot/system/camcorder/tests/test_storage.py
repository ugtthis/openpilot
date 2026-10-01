from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.system.camcorder import storage as storage_module
from openpilot.system.camcorder.storage import FINALIZE_RESERVE_BYTES, RecordingStorage, has_recording_space

GIB = 1024**3
BLOCK_SIZE = 4096


def stat(total_bytes: int, available_bytes: int):
  return SimpleNamespace(f_blocks=total_bytes // BLOCK_SIZE,
                         f_bavail=available_bytes // BLOCK_SIZE,
                         f_frsize=BLOCK_SIZE)


def test_recording_space_preserves_loggerd_byte_and_percent_floors():
  assert has_recording_space(stat(50 * GIB, 6 * GIB))
  assert not has_recording_space(stat(50 * GIB, 5 * GIB + FINALIZE_RESERVE_BYTES - BLOCK_SIZE))
  assert not has_recording_space(stat(100 * GIB, 9 * GIB + FINALIZE_RESERVE_BYTES))


def test_storage_reserve_is_allocated_and_released(tmp_path: Path, monkeypatch):
  monkeypatch.setattr(storage_module, "has_recording_space", lambda *args: True)
  reserve = RecordingStorage(tmp_path, reserve_bytes=1024 * 1024)

  reserve.acquire()

  path = tmp_path / ".finalize-reserve"
  assert path.stat().st_size == 1024 * 1024
  reserve.release()
  assert not path.exists()


def test_storage_reserve_refuses_to_consume_the_safety_floor(tmp_path: Path, monkeypatch):
  monkeypatch.setattr(storage_module, "has_recording_space", lambda *args: False)
  reserve = RecordingStorage(tmp_path, reserve_bytes=1024)

  with pytest.raises(RuntimeError, match="not enough free storage"):
    reserve.acquire()

  assert not (tmp_path / ".finalize-reserve").exists()
