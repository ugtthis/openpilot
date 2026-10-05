"""Clip model and clip.json serialization."""

import json
from dataclasses import MISSING, dataclass, field, fields, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from openpilot.system.camcorder.hevc_writer import MasterInfo
from openpilot.system.camcorder.image import CLIP_ASPECT, CLIP_FPS
from openpilot.system.camcorder.journal import write_json_atomic

if TYPE_CHECKING:
  from openpilot.system.camcorder.audio_track import AudioInfo

CLIP_JSON = "clip.json"
FRAMES_BIN = "frames.bin"
INDEX_BIN = "index.bin"
FRAME_HEADER_SIZE = 4
INDEX_RECORD_SIZE = 12

_META_KEY = "meta_key"
_META_ALIASES = "meta_aliases"
_META_DEFAULT = "meta_default"
_META_FALLBACK = "meta_fallback"
_META_ALWAYS = "meta_always"
_META_GROUP = "meta_group"
_META_READ = "meta_read"
_META_WRITE = "meta_write"
_META_SKIP = "meta_skip"


def _read_datetime(value) -> datetime:
  return datetime.fromisoformat(str(value))


def _write_datetime(value: datetime) -> str:
  return value.isoformat(timespec="seconds")


def format_timecode(seconds: float) -> str:
  total = max(0, int(seconds))
  hours, rem = divmod(total, 3600)
  minutes, secs = divmod(rem, 60)
  if hours:
    return f"{hours}:{minutes:02d}:{secs:02d}"
  return f"{minutes}:{secs:02d}"


@dataclass(frozen=True)
class Clip:
  clip_id: str = field(metadata={_META_KEY: "id"})
  path: Path = field(metadata={_META_SKIP: True})
  camera: str = field(metadata={_META_DEFAULT: "wide"})
  started_at: datetime = field(metadata={_META_READ: _read_datetime, _META_WRITE: _write_datetime})
  width: int
  height: int
  fps: int = field(metadata={_META_DEFAULT: CLIP_FPS})
  frame_count: int
  duration_s: float = field(metadata={_META_DEFAULT: 0.0})
  media_type: str = field(default="video", metadata={_META_ALWAYS: True})
  preview_contains_full_frame: bool = field(
    default=False, metadata={_META_ALIASES: ("preview_uncropped",), _META_ALWAYS: True},
  )
  flip_h: bool = field(default=False, metadata={_META_ALWAYS: True})
  codec: str | None = field(default=None, metadata={_META_GROUP: "master"})
  master: str | None = field(default=None, metadata={_META_GROUP: "master"})
  native_width: int = field(default=0, metadata={_META_GROUP: "master"})
  native_height: int = field(default=0, metadata={_META_GROUP: "master"})
  native_frame_count: int = field(default=0, metadata={_META_GROUP: "master"})
  recording_start_mono_ns: int = 0
  video_start_mono_ns: int = field(default=0, metadata={_META_GROUP: "master"})
  video_gap_count: int = field(default=0, metadata={_META_GROUP: "master"})
  video_dropped_frame_count: int = field(default=0, metadata={_META_GROUP: "master"})
  audio: str | None = field(default=None, metadata={_META_GROUP: "audio"})
  audio_sample_rate: int = field(default=0, metadata={_META_GROUP: "audio"})
  audio_measured_sample_rate: float = field(
    default=0.0, metadata={_META_GROUP: "audio", _META_FALLBACK: "audio_sample_rate"},
  )
  audio_channels: int = field(default=0, metadata={_META_GROUP: "audio"})
  audio_frame_count: int = field(default=0, metadata={_META_GROUP: "audio"})
  audio_start_mono_ns: int = field(default=0, metadata={_META_GROUP: "audio"})
  audio_timestamp: str = field(default="", metadata={_META_GROUP: "audio"})
  audio_gap_count: int = field(default=0, metadata={_META_GROUP: "audio"})
  audio_gap_frame_count: int = field(default=0, metadata={_META_GROUP: "audio"})
  audio_device_name: str = field(default="", metadata={_META_GROUP: "audio"})
  audio_overflow_count: int = field(default=0, metadata={_META_GROUP: "audio"})
  audio_error: str = field(default="", metadata={_META_GROUP: "audio"})
  recovered: bool = False
  recovery_error: str = ""

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

  @property
  def has_timeline_gap(self) -> bool:
    if self.is_photo or self.fps <= 0:
      return False
    if self.video_dropped_frame_count > 0:
      return True

    frame_duration_s = 1 / self.fps
    if self.audio_sample_rate > 0 and self.audio_gap_frame_count / self.audio_sample_rate > frame_duration_s:
      return True
    if not (self.video_start_mono_ns and self.native_frame_count and
            self.audio_start_mono_ns and self.audio_frame_count and self.audio_sample_rate):
      return False

    video_end_s = self.video_start_mono_ns / 1e9 + (self.native_frame_count + self.video_dropped_frame_count) / self.fps
    audio_rate = self.audio_measured_sample_rate or self.audio_sample_rate
    audio_end_s = self.audio_start_mono_ns / 1e9 + self.audio_frame_count / audio_rate
    return abs(audio_end_s - video_end_s) > frame_duration_s


def clip_format_version(clip: Clip) -> int:
  """Return the on-disk schema generation required by this clip.

  Version 1 is the legacy cropped preview, version 2 adds full-frame previews
  and photos, and version 4 adds direct microphone audio and track timing.
  """
  if clip.audio is not None:
    return 4
  if clip.preview_contains_full_frame or clip.is_photo:
    return 2
  return 1


def _field_default(item):
  if _META_DEFAULT in item.metadata:
    return item.metadata[_META_DEFAULT]
  if item.default is not MISSING:
    return item.default
  if item.default_factory is not MISSING:
    return item.default_factory()
  raise KeyError(item.metadata.get(_META_KEY, item.name))


def _read_clip_field(item, meta: dict):
  key = item.metadata.get(_META_KEY, item.name)
  value = MISSING
  for candidate in (key, *item.metadata.get(_META_ALIASES, ())):
    if candidate in meta:
      value = meta[candidate]
      break
  if value is MISSING:
    value = _field_default(item)
  fallback = item.metadata.get(_META_FALLBACK)
  if fallback is not None and not value:
    value = meta.get(fallback, value)
  if reader := item.metadata.get(_META_READ):
    return reader(value)
  if item.type is bool:
    return bool(value)
  if item.type is int:
    return int(value)
  if item.type is float:
    return float(value or 0.0)
  if item.type is str:
    return str(value)
  # Every optional metadata field is an optional string.
  if item.default is None:
    return str(value) if value else None
  return value


def clip_from_metadata(path: Path, meta: dict) -> Clip:
  values = {}
  for item in fields(Clip):
    if item.name == "path":
      values[item.name] = path
    else:
      values[item.name] = _read_clip_field(item, meta)
  return Clip(**values)


def clip_metadata(clip: Clip, status: str) -> dict:
  payload = {
    "format_version": clip_format_version(clip),
    "status": status,
  }
  for item in fields(clip):
    if item.metadata.get(_META_SKIP):
      continue
    value = getattr(clip, item.name)
    group = item.metadata.get(_META_GROUP)
    if group is not None and getattr(clip, group) is None:
      continue
    if group is None and not item.metadata.get(_META_ALWAYS) and item.default is not MISSING and value == item.default:
      continue
    if writer := item.metadata.get(_META_WRITE):
      value = writer(value)
    payload[item.metadata.get(_META_KEY, item.name)] = value
  return payload


def write_clip_metadata(clip: Clip, status: str) -> None:
  write_json_atomic(clip.path / CLIP_JSON, clip_metadata(clip, status))


def clip_with_master(clip: Clip, master: MasterInfo) -> Clip:
  return replace(
    clip,
    codec="hevc",
    master=master.filename,
    native_width=master.width,
    native_height=master.height,
    native_frame_count=master.frame_count,
    video_start_mono_ns=master.first_timestamp_ns,
    video_gap_count=master.gap_count,
    video_dropped_frame_count=master.dropped_frame_count,
  )


def clip_with_audio(clip: Clip, audio: "AudioInfo") -> Clip:
  return replace(
    clip,
    audio=audio.filename,
    audio_sample_rate=audio.sample_rate,
    audio_measured_sample_rate=audio.measured_sample_rate or audio.sample_rate,
    audio_channels=audio.channels,
    audio_frame_count=audio.frame_count,
    audio_start_mono_ns=audio.first_log_mono_ns,
    audio_timestamp="adc_start_boottime",
    audio_gap_count=audio.gap_count,
    audio_gap_frame_count=audio.gap_frame_count,
    audio_device_name=audio.device_name,
    audio_overflow_count=audio.overflow_count,
    audio_error=audio.error,
  )


def load_clip(path: Path) -> Clip | None:
  meta_path = path / CLIP_JSON
  frames = path / FRAMES_BIN
  index = path / INDEX_BIN
  if not meta_path.is_file() or not frames.is_file() or not index.is_file():
    return None
  if frames.stat().st_size < FRAME_HEADER_SIZE or index.stat().st_size < INDEX_RECORD_SIZE:
    return None
  try:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("status") != "ready" or int(meta.get("frame_count", 0)) <= 0:
      return None
    return clip_from_metadata(path, meta)
  except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
    return None
