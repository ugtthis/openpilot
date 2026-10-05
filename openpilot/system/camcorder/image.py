"""Pure image geometry and VisionIPC RGB conversion for camcorder media."""

import numpy as np

# Must match camcorder_style.FEED_ASPECT so the take matches the live crop.
CLIP_WIDTH = 480
CLIP_HEIGHT = 360
CLIP_FPS = 20
CLIP_ASPECT = CLIP_WIDTH / CLIP_HEIGHT
THUMB_WIDTH = 240
THUMB_HEIGHT = 180


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
  return _CABIN_TONE[rgb] if enhance else rgb


def _cabin_tone_curve() -> np.ndarray:
  x = np.arange(256, dtype=np.float32) * (1.0 / 255.0)
  x = np.clip((x + 0.15 - 0.5) * 0.88 + 0.5, 0.0, 1.0)
  x = x * x * (3.0 - 2.0 * x)
  return (np.power(x, 0.8) * 255.0).astype(np.uint8)


_CABIN_TONE = _cabin_tone_curve()


def scale_rgb(rgb: np.ndarray, width: int, height: int) -> np.ndarray:
  ys = (np.arange(height) * rgb.shape[0] / height).astype(np.intp)
  xs = (np.arange(width) * rgb.shape[1] / width).astype(np.intp)
  return np.ascontiguousarray(rgb[ys][:, xs])


def crop_rgb(rgb: np.ndarray, aspect: float = CLIP_ASPECT) -> np.ndarray:
  x, y, width, height = (round(v) for v in center_crop(rgb.shape[1], rgb.shape[0], aspect))
  return np.ascontiguousarray(rgb[y:y + height, x:x + width])
