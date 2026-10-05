"""Clip preview track writing and reading."""

import bisect
from datetime import datetime
import io
from pathlib import Path
import shutil
import struct
import zlib

import numpy as np
from PIL import Image

from openpilot.system.camcorder.audio_track import AudioInfo
from openpilot.system.camcorder.cameras import Camera
from openpilot.system.camcorder.clip_meta import (
  FRAMES_BIN,
  INDEX_BIN,
  Clip,
  clip_with_audio,
  clip_with_master,
  load_clip,
  write_clip_metadata,
)
from openpilot.system.camcorder.hevc_writer import MasterInfo, cleanup_hevc_journal
from openpilot.system.camcorder.image import CLIP_FPS, CLIP_HEIGHT, CLIP_WIDTH
from openpilot.system.camcorder.journal import JournaledFile, JournaledTrack
from openpilot.system.camcorder.library import clips_root

_ZLIB_LEVEL = 1
_JPEG_QUALITY = 90
_JPEG_MAGIC = b"\xff\xd8"
_INDEX = struct.Struct("<QI")
_SIZE = struct.Struct("<I")
_AUDIO_INFO = "audio.info"


def _preview_track(path: Path) -> JournaledTrack:
  return JournaledTrack(
    path,
    [
      JournaledFile(FRAMES_BIN, FRAMES_BIN),
      JournaledFile(INDEX_BIN, INDEX_BIN),
    ],
  )


def _new_clip_id(root: Path, when: datetime) -> str:
  base = when.strftime("%Y-%m-%d--%H-%M-%S")
  clip_id = base
  suffix = 2
  while (root / clip_id).exists():
    clip_id = f"{base}-{suffix}"
    suffix += 1
  return clip_id


class ClipWriter:
  def __init__(self, camera: Camera, width: int = CLIP_WIDTH, height: int = CLIP_HEIGHT,
               fps: int = CLIP_FPS, started_at: datetime | None = None,
               preview_contains_full_frame: bool = False, media_type: str = "video",
               recording_start_mono_ns: int = 0):
    self.camera = camera
    self.width = width
    self.height = height
    self.fps = fps
    self.started_at = started_at or datetime.now()
    self.preview_contains_full_frame = preview_contains_full_frame
    self.media_type = media_type
    self.recording_start_mono_ns = recording_start_mono_ns
    self.frame_count = 0
    self._last_t_ms = 0
    self._frames = None
    self._index = None
    root = clips_root()
    root.mkdir(parents=True, exist_ok=True)
    self.clip_id = _new_clip_id(root, self.started_at)
    self.path = root / self.clip_id
    self.path.mkdir()
    try:
      self._frames = open(self.path / FRAMES_BIN, "wb", buffering=0)
      self._index = open(self.path / INDEX_BIN, "wb", buffering=0)
      self._sync = _preview_track(self.path).periodic_sync(self._frames, self._index)
      self._write_meta("recording")
    except OSError:
      self._close_files()
      shutil.rmtree(self.path, ignore_errors=True)
      raise

  def add_frame(self, rgb: np.ndarray, t_ms: int):
    if self._frames is None or self._index is None:
      raise RuntimeError("writer is closed")
    if rgb.shape != (self.height, self.width, 3):
      raise ValueError(f"expected {(self.height, self.width, 3)}, got {rgb.shape}")
    blob = _encode_frame(rgb, lossless=self.media_type == "photo")
    offset = self._frames.tell()
    self._frames.write(_SIZE.pack(len(blob)))
    self._frames.write(blob)
    self._index.write(_INDEX.pack(offset, t_ms))
    self.frame_count += 1
    self._last_t_ms = t_ms
    self._sync.maybe_sync()

  def finalize(self, master: MasterInfo | None = None, audio: AudioInfo | None = None,
               end_ns: int | None = None) -> Clip | None:
    """Publish the clip with the preview frames received by end_ns."""
    self._close_files()
    end_ms = None if end_ns is None else (end_ns - self.recording_start_mono_ns) // 1_000_000
    preview = publish_preview(self.path, end_ms) if self.frame_count else None
    if preview is None:
      self.abort()
      return None
    self.frame_count, self._last_t_ms = preview
    self._write_meta("ready", master, audio)
    cleanup_hevc_journal(self.path)
    (self.path / _AUDIO_INFO).unlink(missing_ok=True)
    return load_clip(self.path)

  def abort(self):
    self._close_files()
    shutil.rmtree(self.path, ignore_errors=True)

  def _close_files(self):
    if self._frames is not None:
      self._frames.close()
      self._frames = None
    if self._index is not None:
      self._index.close()
      self._index = None

  def _write_meta(self, status: str, master: MasterInfo | None = None, audio: AudioInfo | None = None):
    duration = 0.0
    if self.frame_count and self.media_type == "video":
      duration = max(self._last_t_ms / 1000.0, self.frame_count / float(self.fps))
    clip = Clip(
      clip_id=self.clip_id,
      path=self.path,
      camera=self.camera.clip_name,
      started_at=self.started_at,
      width=self.width,
      height=self.height,
      fps=self.fps,
      frame_count=self.frame_count,
      duration_s=duration,
      media_type=self.media_type,
      preview_contains_full_frame=self.preview_contains_full_frame,
      flip_h=self.camera.flip_h,
      recording_start_mono_ns=self.recording_start_mono_ns,
    )
    if master is not None:
      clip = clip_with_master(clip, master)
    if audio is not None:
      clip = clip_with_audio(clip, audio)
    write_clip_metadata(clip, status)


def publish_preview(path: Path, end_ms: int | None = None) -> tuple[int, int] | None:
  """Truncate the preview to its complete frames received by end_ms; returns (count, last t_ms)."""
  track = _preview_track(path)
  frames_path = track.source_path(0)
  index_path = track.source_path(1)
  if not frames_path.is_file() or not index_path.is_file():
    return None
  raw_index = index_path.read_bytes()
  frames_size = frames_path.stat().st_size
  valid_count = 0
  valid_end = 0
  last_t_ms = 0
  with open(frames_path, "rb") as frames:
    for offset in range(0, len(raw_index) - _INDEX.size + 1, _INDEX.size):
      frame_offset, t_ms = _INDEX.unpack_from(raw_index, offset)
      if frame_offset != valid_end or t_ms < last_t_ms or frame_offset + _SIZE.size > frames_size:
        break
      if end_ms is not None and t_ms > end_ms:
        break
      frames.seek(frame_offset)
      (size,) = _SIZE.unpack(frames.read(_SIZE.size))
      frame_end = frame_offset + _SIZE.size + size
      if size <= 0 or frame_end > frames_size:
        break
      valid_count += 1
      valid_end = frame_end
      last_t_ms = t_ms
  if valid_count <= 0:
    return None
  track.publish([valid_end, valid_count * _INDEX.size])
  return valid_count, last_t_ms


class ClipReader:
  def __init__(self, clip: Clip):
    self.clip = clip
    self._frames = open(clip.path / FRAMES_BIN, "rb")
    ready = False
    try:
      data = (clip.path / INDEX_BIN).read_bytes()
      rec = _INDEX.size
      usable = len(data) - (len(data) % rec)
      self._index = [_INDEX.unpack_from(data, i) for i in range(0, usable, rec)]
      if not self._index:
        raise ValueError("empty clip")
      self._timestamps = [t_ms for _offset, t_ms in self._index]
      ready = True
    finally:
      if not ready:
        self.close()

  def __enter__(self):
    return self

  def __exit__(self, *_exc):
    self.close()

  def close(self):
    if self._frames is not None:
      self._frames.close()
      self._frames = None

  def timestamps_ms(self) -> list[int]:
    return self._timestamps

  def frame_index_at_ms(self, t_ms: int) -> int:
    i = bisect.bisect_right(self._timestamps, t_ms) - 1
    return min(max(i, 0), len(self._timestamps) - 1)

  def frame(self, index: int) -> np.ndarray:
    if self._frames is None:
      raise RuntimeError("reader is closed")
    index = min(max(index, 0), len(self._index) - 1)
    offset, _t_ms = self._index[index]
    self._frames.seek(offset)
    header = self._frames.read(_SIZE.size)
    if len(header) != _SIZE.size:
      raise ValueError("truncated clip")
    (size,) = _SIZE.unpack(header)
    blob = self._frames.read(size)
    if len(blob) != size:
      raise ValueError("truncated clip")
    return _decode_frame(blob).reshape(self.clip.height, self.clip.width, 3).copy()


def _encode_frame(rgb: np.ndarray, lossless: bool) -> bytes:
  # A photo's frame is the deliverable; a video's preview only stands in for its HEVC master.
  if lossless:
    return zlib.compress(np.ascontiguousarray(rgb).tobytes(), _ZLIB_LEVEL)
  jpeg = io.BytesIO()
  Image.fromarray(rgb).save(jpeg, "JPEG", quality=_JPEG_QUALITY)
  return jpeg.getvalue()


def _decode_frame(blob: bytes) -> np.ndarray:
  if blob.startswith(_JPEG_MAGIC):
    return np.asarray(Image.open(io.BytesIO(blob)).convert("RGB"))
  return np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
