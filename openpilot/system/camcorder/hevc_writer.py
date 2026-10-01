"""Append encoderd Annex-B packets and publish them as video.hevc."""

import struct
from dataclasses import dataclass
from pathlib import Path

from openpilot.system.camcorder.journal import PeriodicSync, read_json, write_json_atomic

# Same bit as openpilot/system/loggerd/encoder/encoder.h
V4L2_BUF_FLAG_KEYFRAME = 8

MASTER_FILENAME = "video.hevc"
_PARTIAL_FILENAME = "video.hevc.partial"
_INDEX_FILENAME = "video.index.partial"
_INFO_FILENAME = "video.info"
_INDEX = struct.Struct("<QQ")  # complete byte offset, timestamp
# encoderd runs every camera at 20 fps.
_FRAME_NS = 50_000_000


@dataclass(frozen=True)
class MasterInfo:
  filename: str
  width: int
  height: int
  frame_count: int
  first_timestamp_ns: int = 0
  gap_count: int = 0
  dropped_frame_count: int = 0


class HevcWriter:
  def __init__(self, clip_path: Path):
    self._path = clip_path / MASTER_FILENAME
    self._partial = clip_path / _PARTIAL_FILENAME
    self._index_path = clip_path / _INDEX_FILENAME
    self._info_path = clip_path / _INFO_FILENAME
    self._file = open(self._partial, "wb", buffering=0)
    self._index = open(self._index_path, "wb", buffering=0)
    self._sync = PeriodicSync(self._file, self._index)
    self._started = False
    self._width = 0
    self._height = 0
    self._frame_count = 0
    self._last_complete_offset = 0
    self._first_timestamp_ns = 0
    self._last_timestamp_ns = 0
    self._gap_count = 0
    self._dropped_frame_count = 0

  def add_encoded(self, encoded):
    self.add_packet(bytes(encoded.header), bytes(encoded.data),
                    bool(encoded.idx.flags & V4L2_BUF_FLAG_KEYFRAME),
                    int(encoded.width), int(encoded.height),
                    int(encoded.idx.timestampEof))

  def add_packet(self, header: bytes, data: bytes, keyframe: bool, width: int, height: int,
                 timestamp_ns: int = 0):
    if not self._started:
      if not keyframe or not header:
        return
      write_json_atomic(self._info_path, {
        "width": width,
        "height": height,
        "first_timestamp_ns": timestamp_ns,
      })
      self._started = True
      self._first_timestamp_ns = timestamp_ns
    elif timestamp_ns and self._last_timestamp_ns:
      dropped = round((timestamp_ns - self._last_timestamp_ns) / _FRAME_NS) - 1
      if dropped > 0:
        self._gap_count += 1
        self._dropped_frame_count += dropped
    self._last_timestamp_ns = timestamp_ns
    if keyframe and header:
      self._file.write(header)
    self._file.write(data)
    complete_offset = self._file.tell()
    self._index.write(_INDEX.pack(complete_offset, timestamp_ns))
    self._last_complete_offset = complete_offset
    self._width = width
    self._height = height
    self._frame_count += 1
    self._sync.maybe_sync()

  def finalize(self) -> MasterInfo | None:
    self._file.truncate(self._last_complete_offset)
    self._index.truncate(self._frame_count * _INDEX.size)
    self._sync.sync()
    self._close()
    if not self._started or self._frame_count == 0:
      self._cleanup()
      return None
    self._partial.replace(self._path)
    return MasterInfo(self._path.name, self._width, self._height,
                      self._frame_count, self._first_timestamp_ns,
                      self._gap_count, self._dropped_frame_count)

  def abort(self):
    self._close()
    self._cleanup()
    self._path.unlink(missing_ok=True)

  def _close(self) -> None:
    if not self._file.closed:
      self._file.close()
    if not self._index.closed:
      self._index.close()

  def _cleanup(self) -> None:
    self._partial.unlink(missing_ok=True)
    self._index_path.unlink(missing_ok=True)
    self._info_path.unlink(missing_ok=True)


def recover_hevc(clip_path: Path) -> MasterInfo | None:
  partial = clip_path / _PARTIAL_FILENAME
  output = clip_path / MASTER_FILENAME
  source = output if output.is_file() else partial
  index_path = clip_path / _INDEX_FILENAME
  info_path = clip_path / _INFO_FILENAME
  info = read_json(info_path)
  if info is None or not source.is_file() or not index_path.is_file():
    return None
  try:
    raw_index = index_path.read_bytes()
    file_size = source.stat().st_size
    records: list[tuple[int, int]] = []
    previous_end = 0
    for offset in range(0, len(raw_index) - _INDEX.size + 1, _INDEX.size):
      end, timestamp_ns = _INDEX.unpack_from(raw_index, offset)
      if end <= previous_end or end > file_size:
        break
      records.append((end, timestamp_ns))
      previous_end = end
    if not records:
      return None
    with open(source, "r+b") as file:
      file.truncate(records[-1][0])
    if source == partial:
      partial.replace(output)
    timestamps = [timestamp for _, timestamp in records]
    dropped = sum(max(0, round((current - previous) / _FRAME_NS) - 1)
                  for previous, current in zip(timestamps, timestamps[1:], strict=False))
    gaps = sum(1 for previous, current in zip(timestamps, timestamps[1:], strict=False)
               if round((current - previous) / _FRAME_NS) - 1 > 0)
    return MasterInfo(output.name, int(info["width"]), int(info["height"]), len(records),
                      int(info["first_timestamp_ns"]), gaps, dropped)
  except (KeyError, OSError, TypeError, ValueError, struct.error):
    return None
  finally:
    partial.unlink(missing_ok=True)
    index_path.unlink(missing_ok=True)
    info_path.unlink(missing_ok=True)


def cleanup_hevc_journal(clip_path: Path) -> None:
  (clip_path / _INDEX_FILENAME).unlink(missing_ok=True)
  (clip_path / _INFO_FILENAME).unlink(missing_ok=True)
