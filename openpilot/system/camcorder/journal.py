"""Small durable metadata writes used to recover an interrupted take."""

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class JournaledFile:
  live_name: str
  published_name: str


class JournaledTrack:
  """Shared truncate, publish, recovery cleanup, and abort lifecycle for media tracks."""

  def __init__(self, root: Path, files: Sequence[JournaledFile], journal_names: Sequence[str] = ()):
    self.root = root
    self.files = tuple(files)
    self.journal_names = tuple(journal_names)

  def live_path(self, index: int = 0) -> Path:
    return self.root / self.files[index].live_name

  def published_path(self, index: int = 0) -> Path:
    return self.root / self.files[index].published_name

  def source_path(self, index: int = 0) -> Path:
    published = self.published_path(index)
    return published if published.is_file() else self.live_path(index)

  def periodic_sync(self, *files: BinaryIO) -> PeriodicSync:
    return PeriodicSync(*files)

  def publish(self, sizes: Sequence[int], *, sync: bool = True) -> tuple[Path, ...]:
    """Truncate every source to complete units, then atomically expose live files."""
    if len(sizes) != len(self.files):
      raise ValueError("one published size is required for each track file")
    sources = tuple(self.source_path(index) for index in range(len(self.files)))
    for source, size in zip(sources, sizes, strict=True):
      with open(source, "r+b") as file:
        file.truncate(size)
        if sync:
          os.fsync(file.fileno())
    outputs = []
    for index, source in enumerate(sources):
      output = self.published_path(index)
      if source != output:
        source.replace(output)
      outputs.append(output)
    return tuple(outputs)

  def cleanup_journal(self) -> None:
    for name in self.journal_names:
      (self.root / name).unlink(missing_ok=True)

  def discard_live(self) -> None:
    for index in range(len(self.files)):
      live = self.live_path(index)
      if live != self.published_path(index):
        live.unlink(missing_ok=True)

  def abort(self) -> None:
    for index in range(len(self.files)):
      self.live_path(index).unlink(missing_ok=True)
      self.published_path(index).unlink(missing_ok=True)
    self.cleanup_journal()


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
