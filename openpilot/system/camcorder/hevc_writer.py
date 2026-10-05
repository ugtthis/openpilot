"""Append encoderd Annex-B packets and publish them as video.hevc."""

import struct
from dataclasses import dataclass
from itertools import pairwise, takewhile
from pathlib import Path

from openpilot.system.camcorder.journal import JournaledFile, JournaledTrack, read_json, write_json_atomic

# Same bit as openpilot/system/loggerd/encoder/encoder.h
V4L2_BUF_FLAG_KEYFRAME = 8

MASTER_FILENAME = "video.hevc"
_PARTIAL_FILENAME = "video.hevc.partial"
_INDEX_FILENAME = "video.index.partial"
_INFO_FILENAME = "video.info"
_INDEX = struct.Struct("<QQ")  # complete byte offset, timestamp
# encoderd runs every camera at 20 fps.
_FRAME_NS = 50_000_000


def _hevc_track(clip_path: Path) -> JournaledTrack:
  return JournaledTrack(
    clip_path,
    [JournaledFile(_PARTIAL_FILENAME, MASTER_FILENAME)],
    [_INDEX_FILENAME, _INFO_FILENAME],
  )


@dataclass(frozen=True)
class MasterInfo:
  filename: str
  width: int
  height: int
  frame_count: int
  first_timestamp_ns: int = 0
  gap_count: int = 0
  dropped_frame_count: int = 0
  last_timestamp_ns: int = 0

  @property
  def end_ns(self) -> int:
    """When the last frame stops showing, on the frame timestamp clock."""
    return self.last_timestamp_ns + _FRAME_NS


class HevcWriter:
  def __init__(self, clip_path: Path):
    self._clip_path = clip_path
    self._track = _hevc_track(clip_path)
    self._partial = self._track.live_path()
    self._index_path = clip_path / _INDEX_FILENAME
    self._info_path = clip_path / _INFO_FILENAME
    self._file = open(self._partial, "wb", buffering=0)
    self._index = open(self._index_path, "wb", buffering=0)
    self._sync = self._track.periodic_sync(self._file, self._index)
    self._started = False

  @property
  def started(self) -> bool:
    """True once the first keyframe, where the track can begin, has been written."""
    return self._started

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
      write_json_atomic(self._info_path, {"width": width, "height": height})
      self._started = True
    if keyframe and header:
      self._file.write(header)
    self._file.write(data)
    self._index.write(_INDEX.pack(self._file.tell(), timestamp_ns))
    self._sync.maybe_sync()

  def finalize(self, end_ns: int | None = None) -> MasterInfo | None:
    """Publish video.hevc with the frames captured by end_ns."""
    self._close()
    master = _publish(self._clip_path, end_ns) if self._started else None
    if master is None:
      self.abort()
    return master

  def abort(self):
    self._close()
    self._track.abort()

  def _close(self) -> None:
    if not self._file.closed:
      self._file.close()
    if not self._index.closed:
      self._index.close()

  def _cleanup(self) -> None:
    self._track.discard_live()
    self._track.cleanup_journal()


def _journaled_frames(index: bytes, data_size: int) -> list[tuple[int, int]]:
  """(end offset, timestamp) of each frame written completely before its index entry."""
  frames: list[tuple[int, int]] = []
  for end, timestamp_ns in _INDEX.iter_unpack(index[:len(index) - len(index) % _INDEX.size]):
    if end <= (frames[-1][0] if frames else 0) or end > data_size:
      break
    frames.append((end, timestamp_ns))
  return frames


def _publish(clip_path: Path, end_ns: int | None = None) -> MasterInfo | None:
  track = _hevc_track(clip_path)
  source = track.source_path()
  index_path = clip_path / _INDEX_FILENAME
  info = read_json(clip_path / _INFO_FILENAME)
  if info is None or not source.is_file() or not index_path.is_file():
    return None
  frames = _journaled_frames(index_path.read_bytes(), source.stat().st_size)
  if end_ns is not None:
    frames = list(takewhile(lambda frame: frame[1] <= end_ns, frames))
  if not frames:
    return None
  output, = track.publish([frames[-1][0]])
  timestamps = [timestamp for _, timestamp in frames]
  drops = [round((current - previous) / _FRAME_NS) - 1 for previous, current in pairwise(timestamps)]
  drops = [dropped for dropped in drops if dropped > 0]
  return MasterInfo(output.name, int(info["width"]), int(info["height"]), len(frames),
                    timestamps[0], len(drops), sum(drops), timestamps[-1])


def recover_hevc(clip_path: Path) -> MasterInfo | None:
  try:
    return _publish(clip_path)
  except (KeyError, OSError, TypeError, ValueError, struct.error):
    return None
  finally:
    _hevc_track(clip_path).discard_live()
    cleanup_hevc_journal(clip_path)


def cleanup_hevc_journal(clip_path: Path) -> None:
  _hevc_track(clip_path).cleanup_journal()
