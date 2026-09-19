"""Record a take: RGB preview for the on-device player, HEVC for export."""

import threading
import time

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.mici.layouts.clip_storage import (
  Clip, ClipWriter, extract_clip_rgb, preview_size,
)
from openpilot.selfdrive.ui.mici.layouts.hevc_writer import HevcWriter
from openpilot.system.loggerd.encoder_lease import acquire_encoder, release_encoder

_STREAM_NAMES = {
  VisionStreamType.VISION_STREAM_WIDE_ROAD: "wide",
  VisionStreamType.VISION_STREAM_CABIN: "cabin",
}
_ENCODE_SERVICES = {
  VisionStreamType.VISION_STREAM_WIDE_ROAD: "wideRoadEncodeData",
  VisionStreamType.VISION_STREAM_CABIN: "cabinEncodeData",
}


class ClipRecorder:
  def __init__(self):
    self._preview_thread: threading.Thread | None = None
    self._hevc_thread: threading.Thread | None = None
    self._stop = threading.Event()
    self._preview_ready = threading.Event()
    self._started_mono = 0.0
    self._stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
    self._preview: ClipWriter | None = None
    self._hevc: HevcWriter | None = None
    self._lock = threading.Lock()

  @property
  def recording(self) -> bool:
    return self._preview_thread is not None and self._preview_thread.is_alive()

  @property
  def elapsed_s(self) -> float:
    if not self.recording:
      return 0.0
    return max(0.0, time.monotonic() - self._started_mono)

  def start(self, stream_type: VisionStreamType) -> bool:
    if self.recording or not self._discard_stale():
      return False
    self._stop.clear()
    self._preview_ready.clear()
    self._stream_type = stream_type
    self._started_mono = time.monotonic()
    try:
      acquire_encoder()
    except OSError:
      cloudlog.exception("camcorder could not request native encoder")
      return False
    self._preview_thread = threading.Thread(target=self._capture_preview, name="camcorder-preview", daemon=True)
    self._hevc_thread = threading.Thread(target=self._capture_hevc, name="camcorder-hevc", daemon=True)
    self._preview_thread.start()
    self._hevc_thread.start()
    return True

  def stop(self) -> Clip | None:
    self._stop.set()
    stopped = self._join_threads()
    release_encoder()
    if not stopped:
      cloudlog.error("camcorder recorder did not stop")
      return None
    with self._lock:
      preview, hevc = self._preview, self._hevc
      self._preview = None
      self._hevc = None
    master = hevc.finalize() if hevc is not None else None
    return preview.finalize(master) if preview is not None else None

  def _discard_stale(self) -> bool:
    self._stop.set()
    if not self._join_threads(timeout=1.0):
      cloudlog.error("stale camcorder threads did not stop")
      return False
    release_encoder()
    with self._lock:
      if self._hevc is not None:
        self._hevc.abort()
        self._hevc = None
      if self._preview is not None:
        self._preview.abort()
        self._preview = None
    return True

  def _join_threads(self, timeout: float = 2.0) -> bool:
    threads = (self._preview_thread, self._hevc_thread)
    for thread in threads:
      if thread is not None:
        thread.join(timeout=timeout)
    stopped = all(thread is None or not thread.is_alive() for thread in threads)
    if stopped:
      self._preview_thread = None
      self._hevc_thread = None
    return stopped

  def _capture_preview(self):
    from msgq.visionipc import VisionIpcClient

    client = VisionIpcClient("camerad", self._stream_type, conflate=True)
    cabin = self._stream_type == VisionStreamType.VISION_STREAM_CABIN
    try:
      while not self._stop.is_set() and not (client.is_connected() and client.num_buffers):
        client.connect(False)
        if not (client.is_connected() and client.num_buffers):
          self._stop.wait(0.2)

      while not self._stop.is_set():
        buf = client.recv(timeout_ms=100)
        if buf is None:
          continue
        with self._lock:
          if self._preview is None:
            width, height = preview_size(buf.width, buf.height)
            self._preview = ClipWriter(_STREAM_NAMES.get(self._stream_type, "wide"),
                                       width, height, preview_contains_full_frame=True)
            self._preview_ready.set()
          preview = self._preview
        rgb = extract_clip_rgb(buf.data, buf.width, buf.height, buf.stride, buf.uv_offset,
                               out_w=preview.width, out_h=preview.height,
                               flip_h=cabin, enhance=cabin, crop_aspect=None)
        t_ms = int((time.monotonic() - self._started_mono) * 1000)
        with self._lock:
          preview.add_frame(rgb, t_ms)
    except Exception:
      cloudlog.exception("camcorder preview recorder failed")
      self._stop.set()
    finally:
      del client

  def _capture_hevc(self):
    from openpilot.cereal import messaging

    service = _ENCODE_SERVICES[self._stream_type]
    sock = messaging.sub_sock(service, conflate=False)
    try:
      while not self._stop.is_set():
        if not self._preview_ready.wait(0.05):
          continue
        messages = messaging.drain_sock(sock, wait_for_one=False)
        if not messages:
          self._stop.wait(0.01)
          continue
        with self._lock:
          if self._preview is None:
            continue
          if self._hevc is None:
            self._hevc = HevcWriter(self._preview.path)
          hevc = self._hevc
        for event in messages:
          hevc.add_encoded(getattr(event, service))
    except Exception:
      # Keep the RGB preview even if the native master is incomplete.
      cloudlog.exception("camcorder native recorder failed")
