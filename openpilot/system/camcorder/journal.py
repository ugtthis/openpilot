"""Small durable metadata writes used to recover an interrupted take."""

import json
import os
import time
from pathlib import Path
from typing import BinaryIO

SYNC_INTERVAL_S = 1.0


class PeriodicSync:
  """Bound power-loss exposure without syncing on every media packet."""

  def __init__(self, *files: BinaryIO, interval_s: float = SYNC_INTERVAL_S):
    self._files = files
    self._interval_s = interval_s
    self._last_sync = time.monotonic()

  def maybe_sync(self) -> bool:
    now = time.monotonic()
    if now - self._last_sync < self._interval_s:
      return False
    self.sync()
    self._last_sync = now
    return True

  def sync(self) -> None:
    for file in self._files:
      if not file.closed:
        file.flush()
        os.fsync(file.fileno())


def write_json_atomic(path: Path, payload: dict) -> None:
  temporary = path.with_suffix(path.suffix + ".tmp")
  with open(temporary, "w", encoding="utf-8") as file:
    json.dump(payload, file)
    file.flush()
    os.fsync(file.fileno())
  temporary.replace(path)
  directory = os.open(path.parent, os.O_RDONLY)
  try:
    os.fsync(directory)
  finally:
    os.close(directory)


def read_json(path: Path) -> dict | None:
  try:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None
  except (json.JSONDecodeError, OSError):
    return None
