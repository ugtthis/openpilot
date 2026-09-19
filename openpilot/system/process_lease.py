"""Small file-based leases for manager-controlled processes.

A client writes its PID while it needs a normally conditional process to stay
running. The manager treats the lease as active only while that PID exists.
"""

import os
from pathlib import Path


def acquire_process_lease(path: Path) -> None:
  owner_pid = str(os.getpid())
  temporary = path.with_name(f".{path.name}.{owner_pid}.tmp")
  try:
    # Atomic replacement prevents manager from observing a partially written PID.
    temporary.write_text(owner_pid, encoding="utf-8")
    temporary.replace(path)
  finally:
    try:
      temporary.unlink(missing_ok=True)
    except OSError:
      pass


def release_process_lease(path: Path) -> None:
  _remove_if_owned(path, str(os.getpid()))


def process_lease_requested(path: Path) -> bool:
  owner_pid = _read_owner_pid(path)
  if owner_pid is None:
    return False
  try:
    os.kill(int(owner_pid), 0)
    return True
  except ValueError:
    _remove_if_owned(path, owner_pid)
    return False
  except ProcessLookupError:
    _remove_if_owned(path, owner_pid)
    return False
  except PermissionError:
    # The process exists even if this user cannot signal it.
    return True
  except OSError:
    _remove_if_owned(path, owner_pid)
    return False


def _read_owner_pid(path: Path) -> str | None:
  try:
    return path.read_text(encoding="utf-8")
  except OSError:
    return None


def _remove_if_owned(path: Path, expected_owner_pid: str) -> None:
  """Avoid deleting a lease that another process has replaced."""
  try:
    if _read_owner_pid(path) == expected_owner_pid:
      path.unlink(missing_ok=True)
  except OSError:
    pass
