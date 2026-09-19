"""Ask manager to keep encoderd running while camcorder records.

encoderd normally runs only onroad. A take writes this process's PID here so
parked recordings can still subscribe to the native HEVC stream.
"""

from pathlib import Path

from openpilot.system.process_lease import acquire_process_lease, process_lease_requested, release_process_lease

LEASE_PATH = Path("/tmp/openpilot_camcorder_encoder")


def acquire_encoder() -> None:
  acquire_process_lease(LEASE_PATH)


def release_encoder() -> None:
  release_process_lease(LEASE_PATH)


def encoder_requested() -> bool:
  return process_lease_requested(LEASE_PATH)
