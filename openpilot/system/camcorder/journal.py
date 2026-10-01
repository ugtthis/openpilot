"""Small durable metadata writes used to recover an interrupted take."""

import json
import os
from pathlib import Path


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
