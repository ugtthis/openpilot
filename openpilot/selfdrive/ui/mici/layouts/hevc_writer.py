"""Append encoderd Annex-B packets and publish them as video.hevc."""

from dataclasses import dataclass
from pathlib import Path

# Same bit as openpilot/system/loggerd/encoder/encoder.h
V4L2_BUF_FLAG_KEYFRAME = 8

MASTER_FILENAME = "video.hevc"
_PARTIAL_FILENAME = "video.hevc.partial"


@dataclass(frozen=True)
class MasterInfo:
  filename: str
  width: int
  height: int
  frame_count: int


class HevcWriter:
  def __init__(self, clip_path: Path):
    self._path = clip_path / MASTER_FILENAME
    self._partial = clip_path / _PARTIAL_FILENAME
    self._file = open(self._partial, "wb")
    self._started = False
    self._width = 0
    self._height = 0
    self._frame_count = 0

  def add_encoded(self, encoded):
    self.add_packet(bytes(encoded.header), bytes(encoded.data),
                    bool(encoded.idx.flags & V4L2_BUF_FLAG_KEYFRAME),
                    int(encoded.width), int(encoded.height))

  def add_packet(self, header: bytes, data: bytes, keyframe: bool, width: int, height: int):
    if not self._started:
      if not keyframe or not header:
        return
      self._started = True
    if keyframe and header:
      self._file.write(header)
    self._file.write(data)
    self._width = width
    self._height = height
    self._frame_count += 1

  def finalize(self) -> MasterInfo | None:
    self._file.close()
    if not self._started or self._frame_count == 0:
      self._partial.unlink(missing_ok=True)
      return None
    self._partial.replace(self._path)
    return MasterInfo(self._path.name, self._width, self._height, self._frame_count)

  def abort(self):
    if not self._file.closed:
      self._file.close()
    self._partial.unlink(missing_ok=True)
    self._path.unlink(missing_ok=True)
