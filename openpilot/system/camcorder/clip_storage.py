"""Compatibility facade for the camcorder storage modules.

New code should import the focused module that owns each type or operation.
"""

from openpilot.system.camcorder.audio_track import AudioInfo, AudioWriter, recover_audio
from openpilot.system.camcorder.clip_library import (
  clips_root,
  delete_all_clips,
  delete_clip,
  list_clips,
  recover_interrupted_clips,
)
from openpilot.system.camcorder.clip_meta import (
  Clip,
  clip_format_version,
  clip_from_metadata as _clip_from_metadata,
  clip_metadata as _clip_metadata,
  clip_with_audio as _clip_with_audio,
  clip_with_master as _clip_with_master,
  format_timecode,
  load_clip,
  write_clip_metadata as _write_clip_metadata,
)
from openpilot.system.camcorder.image import (
  CLIP_ASPECT,
  CLIP_FPS,
  CLIP_HEIGHT,
  CLIP_WIDTH,
  center_crop,
  extract_clip_rgb,
  preview_size,
  scale_rgb,
)
from openpilot.system.camcorder.preview_track import ClipReader, ClipWriter, publish_preview as _publish_preview

__all__ = [
  "AudioInfo",
  "AudioWriter",
  "CLIP_ASPECT",
  "CLIP_FPS",
  "CLIP_HEIGHT",
  "CLIP_WIDTH",
  "Clip",
  "ClipReader",
  "ClipWriter",
  "_clip_from_metadata",
  "_clip_metadata",
  "_clip_with_audio",
  "_clip_with_master",
  "_publish_preview",
  "_write_clip_metadata",
  "center_crop",
  "clip_format_version",
  "clips_root",
  "delete_all_clips",
  "delete_clip",
  "extract_clip_rgb",
  "format_timecode",
  "list_clips",
  "load_clip",
  "preview_size",
  "recover_audio",
  "recover_interrupted_clips",
  "scale_rgb",
]
