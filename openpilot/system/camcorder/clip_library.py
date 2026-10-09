"""Clip collection listing, deletion, and interrupted-recording recovery."""

from pathlib import Path
import shutil
import struct

from openpilot.system.camcorder.audio_track import recover_audio
from openpilot.system.camcorder.clip_meta import (
  CLIP_JSON,
  Clip,
  clip_from_metadata,
  clip_with_audio,
  clip_with_master,
  load_clip,
  write_clip_metadata,
)
from openpilot.system.camcorder.hevc_writer import cleanup_hevc_journal, recover_hevc
from openpilot.system.camcorder.image import CLIP_FPS
from openpilot.system.camcorder.journal import read_json
from openpilot.system.camcorder.library import clips_root
from openpilot.system.camcorder.preview_track import publish_preview

_AUDIO_INFO = "audio.info"


def _recover_interrupted_clip(path: Path, meta: dict) -> Clip | None:
  try:
    preview = publish_preview(path)
  except (OSError, struct.error):
    return None
  if preview is None:
    return None
  frame_count, last_t_ms = preview
  fps = int(meta.get("fps", CLIP_FPS))
  if fps <= 0:
    return None
  master = recover_hevc(path)
  if master is None:
    # A preview with no video behind it is not a clip.
    shutil.rmtree(path, ignore_errors=True)
    return None
  audio = recover_audio(path)
  meta.update({
    "frame_count": frame_count,
    "duration_s": max(last_t_ms / 1000.0, frame_count / float(fps)),
    "recovered": True,
    "recovery_error": "recording was interrupted",
  })
  clip = clip_with_master(clip_from_metadata(path, meta), master)
  if audio is not None:
    clip = clip_with_audio(clip, audio)
  write_clip_metadata(clip, "ready")
  return load_clip(path)


def recover_interrupted_clips(root: Path | None = None) -> list[Clip]:
  root = root or clips_root()
  if not root.is_dir():
    return []
  recovered = []
  for path in root.iterdir():
    if not path.is_dir() or (meta := read_json(path / CLIP_JSON)) is None:
      continue
    if meta.get("status") == "ready":
      cleanup_hevc_journal(path)
      (path / _AUDIO_INFO).unlink(missing_ok=True)
      continue
    if meta.get("media_type") == "photo":
      shutil.rmtree(path, ignore_errors=True)
      continue
    if meta.get("status") != "recording":
      continue
    try:
      if clip := _recover_interrupted_clip(path, meta):
        recovered.append(clip)
    except (OSError, TypeError, ValueError, ZeroDivisionError):
      continue
  return recovered


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
