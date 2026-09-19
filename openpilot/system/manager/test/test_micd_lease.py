import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from openpilot.system.micd_lease import acquire_mic, mic_requested, release_mic


def test_mic_lease_lifecycle():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "mic_lease"
    with patch("openpilot.system.micd_lease.LEASE_PATH", lease):
      acquire_mic()
      assert lease.read_text(encoding="utf-8") == str(os.getpid())
      assert mic_requested()
      release_mic()
      assert not lease.exists()


def test_stale_mic_lease_is_removed():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "mic_lease"
    lease.write_text("not-a-pid", encoding="utf-8")
    with patch("openpilot.system.micd_lease.LEASE_PATH", lease):
      assert not mic_requested()
      assert not lease.exists()


def test_release_does_not_remove_another_process_lease():
  with TemporaryDirectory() as directory:
    lease = Path(directory) / "mic_lease"
    lease.write_text(str(os.getpid() + 1), encoding="utf-8")
    with patch("openpilot.system.micd_lease.LEASE_PATH", lease):
      release_mic()
      assert lease.exists()
