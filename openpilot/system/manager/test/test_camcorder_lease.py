import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpilot.system.camcorder_lease import acquire_camcorder, camcorder_requested, release_camcorder, revoke_camcorder


def test_camcorder_lease_lifecycle():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "camcorder_lease"
    with patch("openpilot.system.camcorder_lease.LEASE_PATH", lease):
      acquire_camcorder()
      assert lease.read_text(encoding="utf-8") == str(os.getpid())
      assert camcorder_requested()
      release_camcorder()
      assert not lease.exists()


def test_stale_camcorder_lease_is_removed():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "camcorder_lease"
    lease.write_text("not-a-pid", encoding="utf-8")
    with patch("openpilot.system.camcorder_lease.LEASE_PATH", lease):
      assert not camcorder_requested()
      assert not lease.exists()


def test_release_does_not_remove_another_process_lease():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "camcorder_lease"
    lease.write_text(str(os.getpid() + 1), encoding="utf-8")
    with patch("openpilot.system.camcorder_lease.LEASE_PATH", lease):
      release_camcorder()
      assert lease.exists()


def test_manager_can_revoke_another_process_lease():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "camcorder_lease"
    lease.write_text(str(os.getpid() + 1), encoding="utf-8")
    with patch("openpilot.system.camcorder_lease.LEASE_PATH", lease):
      revoke_camcorder()
      assert not lease.exists()
