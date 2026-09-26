import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpilot.system.loggerd.encoder_lease import acquire_encoder, encoder_requested, release_encoder, revoke_encoder


def test_encoder_lease_lifecycle():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "encoder_lease"
    with patch("openpilot.system.loggerd.encoder_lease.LEASE_PATH", lease):
      acquire_encoder()
      assert lease.read_text(encoding="utf-8") == str(os.getpid())
      assert encoder_requested()
      release_encoder()
      assert not lease.exists()


def test_stale_encoder_lease_is_removed():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "encoder_lease"
    lease.write_text("not-a-pid", encoding="utf-8")
    with patch("openpilot.system.loggerd.encoder_lease.LEASE_PATH", lease):
      assert not encoder_requested()
      assert not lease.exists()


def test_release_does_not_remove_another_process_lease():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "encoder_lease"
    lease.write_text(str(os.getpid() + 1), encoding="utf-8")
    with patch("openpilot.system.loggerd.encoder_lease.LEASE_PATH", lease):
      release_encoder()
      assert lease.exists()


def test_manager_can_revoke_another_process_lease():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "encoder_lease"
    lease.write_text(str(os.getpid() + 1), encoding="utf-8")
    with patch("openpilot.system.loggerd.encoder_lease.LEASE_PATH", lease):
      revoke_encoder()
      assert not lease.exists()
