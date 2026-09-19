"""Ask manager to keep micd running while the camcorder records."""

from pathlib import Path

from openpilot.system.process_lease import acquire_process_lease, process_lease_requested, release_process_lease

LEASE_PATH = Path("/tmp/openpilot_camcorder_mic")


def acquire_mic() -> None:
  acquire_process_lease(LEASE_PATH)


def release_mic() -> None:
  release_process_lease(LEASE_PATH)


def mic_requested() -> bool:
  return process_lease_requested(LEASE_PATH)
