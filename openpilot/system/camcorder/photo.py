"""One-shot still capture owned by camcorderd."""

import time
from collections.abc import Callable

import numpy as np

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.cameras import camera_for_stream
from openpilot.system.camcorder.clip_meta import Clip
from openpilot.system.camcorder.image import extract_clip_rgb
from openpilot.system.camcorder.preview_track import ClipWriter

Still = tuple[np.ndarray, int, int]


def grab_still(stream_type: VisionStreamType, timeout_s: float = 1.0) -> Still | None:
  """Copy one native-size frame from camerad before its VisionIPC buffer is reused."""
  from msgq.visionipc import VisionIpcClient

  client = VisionIpcClient("camerad", stream_type, conflate=True)
  deadline = time.monotonic() + timeout_s
  try:
    while time.monotonic() < deadline:
      if not (client.is_connected() and client.num_buffers):
        client.connect(False)
        if not (client.is_connected() and client.num_buffers):
          time.sleep(0.02)
          continue
      remaining_ms = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
      buf = client.recv(timeout_ms=remaining_ms)
      if buf is None:
        continue
      camera = camera_for_stream(stream_type)
      rgb = extract_clip_rgb(
        buf.data, buf.width, buf.height, buf.stride, buf.uv_offset,
        out_w=buf.width, out_h=buf.height,
        flip_h=camera.flip_h, enhance=camera.enhance, crop_aspect=None,
      )
      return rgb.copy(), int(buf.width), int(buf.height)
    return None
  finally:
    del client


def write_photo(stream_type: VisionStreamType, still: Still) -> Clip | None:
  rgb, width, height = still
  writer = ClipWriter(
    camera_for_stream(stream_type),
    width, height,
    preview_contains_full_frame=True,
    media_type="photo",
  )
  try:
    writer.add_frame(rgb, 0)
    return writer.finalize()
  except Exception:
    writer.abort()
    raise


def take_photo(stream_type: VisionStreamType,
               grab: Callable[[VisionStreamType], Still | None] = grab_still) -> Clip | None:
  still = grab(stream_type)
  return write_photo(stream_type, still) if still is not None else None
