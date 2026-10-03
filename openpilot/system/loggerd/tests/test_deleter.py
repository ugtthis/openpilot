from collections import namedtuple
from pathlib import Path
from collections.abc import Sequence

import openpilot.system.loggerd.deleter as deleter
from openpilot.system.camcorder.library import CAMCORDER_RESERVE_BYTES
from openpilot.system.loggerd.tests.loggerd_tests_common import UploaderTestCase

Stats = namedtuple("Stats", ['f_bavail', 'f_blocks', 'f_frsize'])
GIB = 1024 ** 3


class TestDeleter(UploaderTestCase):
  # Deletion behavior is independent of file size; use smaller files to keep these tests fast.
  def make_file_with_data(self, f_dir: str, fn: str, size_mb: float = .001, lock: bool = False,
                          upload_xattr: bytes | None = None, preserve_xattr: bytes | None = None) -> Path:
    return super().make_file_with_data(f_dir, fn, size_mb, lock, upload_xattr, preserve_xattr)

  def fake_statvfs(self, d):
    return self.fake_stats

  def setup_method(self):
    self.f_type = "fcamera.hevc"
    super().openpilot_setup_method()
    self.fake_stats = Stats(f_bavail=0, f_blocks=10, f_frsize=4096)
    deleter.os.statvfs = self.fake_statvfs  # ty: ignore[invalid-assignment]  # test double

  def test_delete(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)
    assert deleter.deleter_step() == (True, str(f_path.parent))
    assert not f_path.exists()

  def assertDeleteOrder(self, f_paths: Sequence[Path]) -> None:
    deleted_order = []
    for _ in f_paths:
      out_of_space, deleted_path = deleter.deleter_step()
      assert out_of_space and deleted_path is not None
      deleted_order.append(next(f for f in f_paths if f.parent == Path(deleted_path)))

    assert deleted_order == f_paths, "Files not deleted in expected order"

  def test_delete_order(self):
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(1), self.f_type),
      self.make_file_with_data(self.seg_format2.format(0), self.f_type),
    ])

  def test_delete_many_preserved(self):
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(1), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE),
      self.make_file_with_data(self.seg_format.format(2), self.f_type),
    ] + [
      self.make_file_with_data(self.seg_format2.format(i), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE)
      for i in range(5)
    ])

  def test_delete_last(self):
    self.assertDeleteOrder([
      self.make_file_with_data(self.seg_format.format(1), self.f_type),
      self.make_file_with_data(self.seg_format2.format(0), self.f_type),
      self.make_file_with_data(self.seg_format.format(0), self.f_type, preserve_xattr=deleter.PRESERVE_ATTR_VALUE),
      self.make_file_with_data("boot", self.seg_format[:-4]),
      self.make_file_with_data("crash", self.seg_format2[:-4]),
    ])

  def test_no_delete_when_available_space(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)

    block_size = 4096
    available = (10 * GIB + CAMCORDER_RESERVE_BYTES) / block_size  # 10GB free beyond the camcorder reserve
    self.fake_stats = Stats(f_bavail=available, f_blocks=10, f_frsize=block_size)

    assert deleter.deleter_step() == (False, None)
    assert f_path.exists(), "File deleted with available space"

  def test_delete_to_keep_room_for_camcorder_clips(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)
    block_size = 4096
    # above the stock 5GB / 10% floor, but not once the empty camcorder reserve is held back
    self.fake_stats = Stats(f_bavail=12 * GIB // block_size, f_blocks=100 * GIB // block_size, f_frsize=block_size)

    assert deleter.deleter_step() == (True, str(f_path.parent))
    assert not f_path.exists()

  def test_deleter_line_is_the_stock_floor_plus_the_unused_camcorder_reserve(self, monkeypatch):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)
    block_size = 4096
    total = 100 * GIB
    unused_reserve = 7 * GIB
    monkeypatch.setattr(deleter, "unused_reserve_bytes", lambda: unused_reserve)
    line = max(deleter.MIN_STORAGE_BYTES, total * deleter.MIN_STORAGE_PERCENT // 100) + unused_reserve

    self.fake_stats = Stats(f_bavail=line // block_size, f_blocks=total // block_size, f_frsize=block_size)
    assert deleter.deleter_step() == (False, None)

    self.fake_stats = Stats(f_bavail=line // block_size - 1, f_blocks=total // block_size, f_frsize=block_size)
    assert deleter.deleter_step() == (True, str(f_path.parent))

  def test_recorded_clips_use_up_the_camcorder_reserve(self, monkeypatch):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type)
    block_size = 4096
    self.fake_stats = Stats(f_bavail=12 * GIB // block_size, f_blocks=100 * GIB // block_size, f_frsize=block_size)
    monkeypatch.setattr(deleter, "unused_reserve_bytes", lambda: 0)

    assert deleter.deleter_step() == (False, None)
    assert f_path.exists(), "Drive deleted for camcorder room that clips already use"

  def test_no_delete_with_lock_file(self):
    f_path = self.make_file_with_data(self.seg_dir, self.f_type, lock=True)

    assert deleter.deleter_step() == (True, None)
    assert f_path.exists(), "File deleted when locked"
