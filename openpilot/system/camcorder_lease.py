"""Ask manager to keep camcorderd running while its UI page or a take needs it."""

from pathlib import Path

from openpilot.system.process_lease import acquire_process_lease, process_lease_requested, release_process_lease, revoke_process_lease

LEASE_PATH = Path("/tmp/openpilot_camcorder")


def acquire_camcorder() -> None:
  acquire_process_lease(LEASE_PATH)


def release_camcorder() -> None:
  release_process_lease(LEASE_PATH)


def revoke_camcorder() -> None:
  revoke_process_lease(LEASE_PATH)


def camcorder_requested() -> bool:
  return process_lease_requested(LEASE_PATH)
