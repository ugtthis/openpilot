"""On-device clip format: RGB preview frames plus clip.json metadata.

  clip.json     status must be "ready" to appear in the list
  frames.bin    [uint32 size][zlib rgb8]...
  index.bin     [uint64 offset][uint32 t_ms]...
  video.hevc    native hardware-encoded master, written by HevcWriter
  audio.s16le   mono int16 PCM captured from micd
"""

import bisect
import json
import os
import shutil
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from openpilot.common.hardware import PC
from openpilot.common.hardware.hw import Paths
from openpilot.selfdrive.ui.mici.layouts.hevc_writer import MasterInfo
from openpilot.system.audio_utils import PCM_SAMPLE_BYTES

# Must match camcorder_style.FEED_ASPECT so the take matches the live crop.
CLIP_WIDTH = 480
CLIP_HEIGHT = 360
CLIP_FPS = 20
CLIP_ASPECT = CLIP_WIDTH / CLIP_HEIGHT

_ZLIB_LEVEL = 1
_INDEX = struct.Struct("<QI")
_SIZE = struct.Struct("<I")
_CLIP_JSON = "clip.json"
_FRAMES_BIN = "frames.bin"
_INDEX_BIN = "index.bin"
_AUDIO_PCM = "audio.s16le"
_AUDIO_PARTIAL = "audio.s16le.partial"


def clips_root() -> Path:
  override = os.environ.get("CAMCORDER_CLIPS")
  if override:
    return Path(override)
  if PC:
    return Path(Paths.comma_home()) / "media" / "0" / "camcorder"
  return Path("/data/media/0/camcorder")


def format_timecode(seconds: float) -> str:
  total = max(0, int(seconds))
  hours, rem = divmod(total, 3600)
  minutes, secs = divmod(rem, 60)
  if hours:
    return f"{hours}:{minutes:02d}:{secs:02d}"
  return f"{minutes}:{secs:02d}"


def center_crop(width: float, height: float, aspect: float = CLIP_ASPECT) -> tuple[float, float, float, float]:
  if width <= 0 or height <= 0:
    return 0.0, 0.0, 0.0, 0.0
  if width / height > aspect:
    w = height * aspect
    return (width - w) / 2, 0.0, w, height
  h = width / aspect
  return 0.0, (height - h) / 2, width, h


def preview_size(width: int, height: int, preview_height: int = CLIP_HEIGHT) -> tuple[int, int]:
  if width <= 0 or height <= 0:
    return CLIP_WIDTH, CLIP_HEIGHT
  preview_width = max(2, round(preview_height * width / height / 2) * 2)
  return preview_width, preview_height


@dataclass(frozen=True)
class Clip:
  clip_id: str
  path: Path
  camera: str
  started_at: datetime
  width: int
  height: int
  fps: int
  frame_count: int
  duration_s: float
  media_type: str = "video"
  preview_contains_full_frame: bool = False
  master: str | None = None
  native_width: int = 0
  native_height: int = 0
  recording_start_mono_ns: int = 0
  video_start_mono_ns: int = 0
  audio: str | None = None
  audio_sample_rate: int = 0
  audio_channels: int = 0
  audio_frame_count: int = 0
  audio_start_mono_ns: int = 0

  @property
  def time_label(self) -> str:
    return self.started_at.strftime("%H:%M")

  @property
  def duration_label(self) -> str:
    return format_timecode(self.duration_s)

  @property
  def has_full_frame_preview(self) -> bool:
    return self.preview_contains_full_frame and self.height > 0 and abs(self.width / self.height - CLIP_ASPECT) > 0.01

  @property
  def is_photo(self) -> bool:
    return self.media_type == "photo"

  @property
  def has_audio(self) -> bool:
    return (self.audio is not None and self.audio_sample_rate > 0 and self.audio_channels > 0 and
            self.audio_frame_count > 0 and (self.path / self.audio).is_file())


@dataclass(frozen=True)
class AudioInfo:
  filename: str
  sample_rate: int
  channels: int
  frame_count: int
  first_log_mono_ns: int


class AudioWriter:
  """Atomically persist one fixed-format little-endian int16 PCM stream."""

  def __init__(self, clip_path: Path):
    self._path = clip_path / _AUDIO_PCM
    self._partial = clip_path / _AUDIO_PARTIAL
    self._file = open(self._partial, "wb")
    self._sample_rate = 0
    self._channels = 1
    self._frame_count = 0
    self._first_log_mono_ns = 0

  def add_packet(self, data: bytes, sample_rate: int, log_mono_ns: int, channels: int = 1) -> None:
    if self._file.closed:
      raise RuntimeError("audio writer is closed")
    if sample_rate <= 0 or channels <= 0:
      raise ValueError("invalid int16 audio packet")
    frame_size = PCM_SAMPLE_BYTES * channels
    if len(data) % frame_size:
      raise ValueError("invalid int16 audio packet")
    if self._sample_rate and (sample_rate != self._sample_rate or channels != self._channels):
      raise ValueError("audio format changed during recording")
    if not self._sample_rate:
      self._sample_rate = sample_rate
      self._channels = channels
      self._first_log_mono_ns = log_mono_ns
    self._file.write(data)
    self._frame_count += len(data) // frame_size

  def finalize(self) -> AudioInfo | None:
    self._file.close()
    if self._frame_count <= 0:
      self._partial.unlink(missing_ok=True)
      return None
    self._partial.replace(self._path)
    return AudioInfo(self._path.name, self._sample_rate, self._channels,
                     self._frame_count, self._first_log_mono_ns)

  def abort(self) -> None:
    if not self._file.closed:
      self._file.close()
    self._partial.unlink(missing_ok=True)
    self._path.unlink(missing_ok=True)


def _new_clip_id(root: Path, when: datetime) -> str:
  base = when.strftime("%Y-%m-%d--%H-%M-%S")
  clip_id = base
  suffix = 2
  while (root / clip_id).exists():
    clip_id = f"{base}-{suffix}"
    suffix += 1
  return clip_id


def _as_u8(data) -> np.ndarray:
  if isinstance(data, np.ndarray):
    return data.reshape(-1)
  return np.frombuffer(memoryview(data), dtype=np.uint8)


def extract_clip_rgb(data, width: int, height: int, stride: int, uv_offset: int,
                     out_w: int = CLIP_WIDTH, out_h: int = CLIP_HEIGHT,
                     flip_h: bool = False, enhance: bool = False,
                     crop_aspect: float | None = CLIP_ASPECT) -> np.ndarray:
  """Copy out of the VisionIPC buffer; camerad reuses it."""
  raw = _as_u8(data)
  y_plane = raw[:uv_offset].reshape(-1, stride)
  uv_height = max(1, (len(raw) - uv_offset) // stride)
  uv_plane = raw[uv_offset:uv_offset + stride * uv_height].reshape(-1, stride)

  x0, y0, crop_w, crop_h = center_crop(width, height, crop_aspect) if crop_aspect else (0.0, 0.0, width, height)
  x0, y0 = int(x0) & ~1, int(y0) & ~1
  crop_w, crop_h = int(crop_w) & ~1, int(crop_h) & ~1

  ys = np.clip(y0 + (np.arange(out_h) * crop_h / max(out_h, 1)).astype(np.intp), 0, y_plane.shape[0] - 1)
  xs = np.clip(x0 + (np.arange(out_w) * crop_w / max(out_w, 1)).astype(np.intp), 0, width - 1)
  if flip_h:
    xs = xs[::-1]

  y = y_plane[ys][:, xs].astype(np.int16)
  u = uv_plane[ys // 2][:, (xs // 2) * 2].astype(np.int16) - 128
  v = uv_plane[ys // 2][:, (xs // 2) * 2 + 1].astype(np.int16) - 128
  rgb = np.stack((y + (359 * v) // 256,
                  y - (88 * u) // 256 - (183 * v) // 256,
                  y + (454 * u) // 256), axis=-1)
  rgb = rgb.clip(0, 255).astype(np.uint8)
  if enhance:
    x = rgb.astype(np.float32) * (1.0 / 255.0)
    x = np.clip((x + 0.15 - 0.5) * 0.88 + 0.5, 0.0, 1.0)
    x = x * x * (3.0 - 2.0 * x)
    rgb = (np.power(x, 0.8) * 255.0).astype(np.uint8)
  return rgb


def scale_rgb(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
  ys = (np.arange(height) * rgb.shape[0] / height).astype(np.intp)
  xs = (np.arange(width) * rgb.shape[1] / width).astype(np.intp)
  return np.ascontiguousarray(rgb[ys][:, xs])


class ClipWriter:
  def __init__(self, camera: str, width: int = CLIP_WIDTH, height: int = CLIP_HEIGHT,
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
      self._frames = open(self.path / _FRAMES_BIN, "wb")
      self._index = open(self.path / _INDEX_BIN, "wb")
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
    blob = zlib.compress(np.ascontiguousarray(rgb).tobytes(), _ZLIB_LEVEL)
    offset = self._frames.tell()
    self._index.write(_INDEX.pack(offset, t_ms))
    self._frames.write(_SIZE.pack(len(blob)))
    self._frames.write(blob)
    self.frame_count += 1
    self._last_t_ms = t_ms

  def finalize(self, master: MasterInfo | None = None, audio: AudioInfo | None = None) -> Clip | None:
    self._close_files()
    if self.frame_count <= 0:
      self.abort()
      return None
    self._write_meta("ready", master, audio)
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
    format_version = 1
    if self.preview_contains_full_frame or self.media_type != "video":
      format_version = 2
    if audio is not None:
      format_version = 3
    payload = {
      "format_version": format_version,
      "id": self.clip_id,
      "status": status,
      "media_type": self.media_type,
      "camera": self.camera,
      "flip_h": self.camera == "cabin",
      "started_at": self.started_at.isoformat(timespec="seconds"),
      "width": self.width,
      "height": self.height,
      "fps": self.fps,
      "frame_count": self.frame_count,
      "duration_s": duration,
      "preview_contains_full_frame": self.preview_contains_full_frame,
    }
    if self.recording_start_mono_ns:
      payload["recording_start_mono_ns"] = self.recording_start_mono_ns
    if master is not None:
      payload.update({
        "codec": "hevc",
        "master": master.filename,
        "native_width": master.width,
        "native_height": master.height,
        "native_frame_count": master.frame_count,
        "video_start_mono_ns": master.first_timestamp_ns,
      })
    if audio is not None:
      payload.update({
        "audio": audio.filename,
        "audio_sample_rate": audio.sample_rate,
        "audio_channels": audio.channels,
        "audio_frame_count": audio.frame_count,
        "audio_start_mono_ns": audio.first_log_mono_ns,
      })
    (self.path / _CLIP_JSON).write_text(json.dumps(payload), encoding="utf-8")


def load_clip(path: Path) -> Clip | None:
  meta_path = path / _CLIP_JSON
  frames = path / _FRAMES_BIN
  index = path / _INDEX_BIN
  if not meta_path.is_file() or not frames.is_file() or not index.is_file():
    return None
  if frames.stat().st_size < _SIZE.size or index.stat().st_size < _INDEX.size:
    return None
  try:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("status") != "ready" or int(meta.get("frame_count", 0)) <= 0:
      return None
    return Clip(
      clip_id=str(meta["id"]),
      path=path,
      camera=str(meta.get("camera", "wide")),
      started_at=datetime.fromisoformat(meta["started_at"]),
      width=int(meta["width"]),
      height=int(meta["height"]),
      fps=int(meta.get("fps", CLIP_FPS)),
      frame_count=int(meta["frame_count"]),
      duration_s=float(meta.get("duration_s") or 0.0),
      media_type=str(meta.get("media_type", "video")),
      preview_contains_full_frame=bool(meta.get("preview_contains_full_frame",
                                                meta.get("preview_uncropped", False))),
      master=str(meta["master"]) if meta.get("master") else None,
      native_width=int(meta.get("native_width", 0)),
      native_height=int(meta.get("native_height", 0)),
      recording_start_mono_ns=int(meta.get("recording_start_mono_ns", 0)),
      video_start_mono_ns=int(meta.get("video_start_mono_ns", 0)),
      audio=str(meta["audio"]) if meta.get("audio") else None,
      audio_sample_rate=int(meta.get("audio_sample_rate", 0)),
      audio_channels=int(meta.get("audio_channels", 0)),
      audio_frame_count=int(meta.get("audio_frame_count", 0)),
      audio_start_mono_ns=int(meta.get("audio_start_mono_ns", 0)),
    )
  except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
    return None


def list_clips() -> list[Clip]:
  root = clips_root()
  if not root.is_dir():
    return []
  clips = []
  for path in root.iterdir():
    if path.is_dir() and (clip := load_clip(path)):
      clips.append(clip)
  clips.sort(key=lambda c: c.started_at, reverse=True)
  return clips


def delete_clip(clip: Clip) -> bool:
  shutil.rmtree(clip.path, ignore_errors=True)
  return not clip.path.exists()


def delete_all_clips() -> int:
  return sum(1 for clip in list_clips() if delete_clip(clip))


class ClipReader:
  def __init__(self, clip: Clip):
    self.clip = clip
    self._frames = open(clip.path / _FRAMES_BIN, "rb")
    ready = False
    try:
      data = (clip.path / _INDEX_BIN).read_bytes()
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
    rgb = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
    return rgb.reshape(self.clip.height, self.clip.width, 3).copy()
