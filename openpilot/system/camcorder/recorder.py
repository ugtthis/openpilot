"""Record a take: RGB preview for the on-device player, HEVC for export."""

import threading
import time
from collections.abc import Callable

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.common.swaglog import cloudlog
from openpilot.system.camcorder.preroll import AudioPacket, HevcPacket, PreRoll
from openpilot.system.camcorder.clip_storage import (
  AudioWriter, Clip, ClipWriter, extract_clip_rgb, preview_size,
)
from openpilot.system.camcorder.hevc_writer import HevcWriter
from openpilot.system.loggerd.encoder_lease import acquire_encoder, release_encoder
from openpilot.system.micd_lease import acquire_mic, release_mic

_STREAM_NAMES = {
  VisionStreamType.VISION_STREAM_WIDE_ROAD: "wide",
  VisionStreamType.VISION_STREAM_CABIN: "cabin",
}
_ENCODE_SERVICES = {
  VisionStreamType.VISION_STREAM_WIDE_ROAD: "wideRoadEncodeData",
  VisionStreamType.VISION_STREAM_CABIN: "cabinEncodeData",
}
# micd sends a packet every 50 ms; the UI thread waits on this at stop.
_AUDIO_TAIL_TIMEOUT_S = 0.2


class ClipRecorder:
  def __init__(self, capture_allowed: Callable[[], bool] = lambda: True):
    self._capture_allowed = capture_allowed
    self._preview_thread: threading.Thread | None = None
    self._hevc_thread: threading.Thread | None = None
    self._audio_thread: threading.Thread | None = None
    self._stop = threading.Event()
    self._preview_ready = threading.Event()
    self._finalizing = threading.Event()
    self._started_mono = 0.0
    self._stop_mono_ns = 0
    self._stream_type = VisionStreamType.VISION_STREAM_WIDE_ROAD
    self._preview: ClipWriter | None = None
    self._hevc: HevcWriter | None = None
    self._audio: AudioWriter | None = None
    self._lock = threading.Lock()
    self._stop_lock = threading.Lock()
    self._warm = False
    self._preroll = PreRoll()
    self._preroll_video: list[HevcPacket] = []
    self._preroll_audio: list[AudioPacket] = []
    # Newest pre-roll timestamp written; the take's own sockets repeat earlier packets.
    self._hevc_after_ns = 0
    self._audio_after_ns = 0

  @property
  def recording(self) -> bool:
    return self._finalizing.is_set() or (self._preview_thread is not None and self._preview_thread.is_alive())

  @property
  def elapsed_s(self) -> float:
    if not self.recording:
      return 0.0
    return max(0.0, time.monotonic() - self._started_mono)

  def set_warm(self, warm: bool, stream_type: VisionStreamType) -> None:
    """Keep encoderd and micd running, with a pre-roll, while the camcorder is on screen.

    Spawning both on the shutter press cost every take its first ~0.7 s.
    """
    warm = warm and self._capture_allowed()
    if warm != self._warm:
      self._warm = warm
      if warm:
        try:
          acquire_encoder()
          acquire_mic()
        except OSError:
          self._warm = False
          self._release_leases()
          cloudlog.exception("camcorder could not warm up recording services")
      elif not self.recording:
        self._release_leases()
    if not self._warm:
      self._preroll.stop()
    elif not self.recording:
      self._preroll.start(_ENCODE_SERVICES[stream_type])

  def start(self, stream_type: VisionStreamType, recording_start_mono_ns: int | None = None) -> bool:
    # Recorder-level backstop: never acquire offroad capture processes based
    # only on the UI page being visible.
    if not self._capture_allowed() or self.recording or not self._discard_stale():
      return False
    self._stop.clear()
    self._preview_ready.clear()
    self._stop_mono_ns = 0
    self._preroll_video = []
    self._preroll_audio = []
    self._hevc_after_ns = 0
    self._audio_after_ns = 0
    self._stream_type = stream_type
    self._started_mono = (recording_start_mono_ns / 1e9) if recording_start_mono_ns else time.monotonic()
    try:
      acquire_encoder()
      acquire_mic()
    except OSError:
      self._release_leases()
      cloudlog.exception("camcorder could not request recording services")
      return False
    self._preview_thread = threading.Thread(target=self._capture_preview, name="camcorder-preview", daemon=True)
    self._hevc_thread = threading.Thread(target=self._capture_hevc, name="camcorder-hevc", daemon=True)
    self._audio_thread = threading.Thread(target=self._capture_audio, name="camcorder-audio", daemon=True)
    self._preview_thread.start()
    self._hevc_thread.start()
    self._audio_thread.start()
    return True

  def stop(self, stop_mono_ns: int | None = None) -> Clip | None:
    self._stop_mono_ns = stop_mono_ns or time.monotonic_ns()
    self._stop.set()
    return self._finish_stop(release_after=True)

  def _finish_stop(self, release_after: bool) -> Clip | None:
    with self._stop_lock:
      stopped = self._join_threads()
      if release_after:
        # Normal shutter stop: preserve the clip tail until capture has drained.
        self._release_leases()
      if not stopped:
        cloudlog.error("camcorder recorder did not stop")
        return None
      with self._lock:
        preview, hevc, audio = self._preview, self._hevc, self._audio
        self._preview = None
        self._hevc = None
        self._audio = None
      master = hevc.finalize() if hevc is not None else None
      audio_info = audio.finalize() if audio is not None else None
      return preview.finalize(master, audio_info) if preview is not None else None

  def stop_async(self) -> None:
    """Abort for ignition without joining capture workers on the UI thread."""
    self._stop_mono_ns = time.monotonic_ns()
    self._stop.set()
    self._warm = False
    self._preroll.stop()
    self._release_leases()
    self._finalizing.set()
    try:
      threading.Thread(target=self._finish_stop_async, name="camcorder-stop", daemon=True).start()
    except RuntimeError:
      # Resources are already released and capture workers have been asked to
      # stop. Leave stale-file cleanup to the next offroad start.
      self._finalizing.clear()
      cloudlog.exception("camcorder stop finalizer could not start")

  def _finish_stop_async(self) -> None:
    try:
      self._finish_stop(release_after=False)
    finally:
      self._finalizing.clear()

  def _discard_stale(self) -> bool:
    self._stop.set()
    stopped = self._join_threads(timeout=1.0)
    self._release_leases()
    if not stopped:
      cloudlog.error("stale camcorder threads did not stop")
      return False
    with self._lock:
      if self._hevc is not None:
        self._hevc.abort()
        self._hevc = None
      if self._audio is not None:
        self._audio.abort()
        self._audio = None
      if self._preview is not None:
        self._preview.abort()
        self._preview = None
    return True

  def _release_leases(self) -> None:
    if self._warm:
      return
    release_encoder()
    release_mic()

  def _join_threads(self, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    threads = (self._preview_thread, self._hevc_thread, self._audio_thread)
    for thread in threads:
      if thread is not None:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    stopped = all(thread is None or not thread.is_alive() for thread in threads)
    if stopped:
      self._preview_thread = None
      self._hevc_thread = None
      self._audio_thread = None
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
            press_ns = int(self._started_mono * 1e9)
            # Overlaps the take's own subscriptions, which began at the press.
            self._preroll_video, self._preroll_audio = self._preroll.take(press_ns)
            width, height = preview_size(buf.width, buf.height)
            self._preview = ClipWriter(_STREAM_NAMES.get(self._stream_type, "wide"),
                                       width, height, preview_contains_full_frame=True,
                                       recording_start_mono_ns=press_ns)
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
        self._write_hevc([getattr(event, service) for event in messages])
    except Exception:
      # Keep the RGB preview even if the native master is incomplete.
      cloudlog.exception("camcorder native recorder failed")

  def _write_hevc(self, encoded_frames) -> None:
    with self._lock:
      if self._preview is None:
        return
      if self._hevc is None:
        self._hevc = HevcWriter(self._preview.path)
        for packet in self._preroll_video:
          self._hevc.add_packet(packet.header, packet.data, packet.keyframe,
                                packet.width, packet.height, packet.timestamp_ns)
          self._hevc_after_ns = packet.timestamp_ns
      hevc = self._hevc
    for encoded in encoded_frames:
      if encoded.idx.timestampEof > self._hevc_after_ns:
        hevc.add_encoded(encoded)

  def _capture_audio(self):
    from openpilot.cereal import messaging

    sock = messaging.sub_sock("rawAudioData", conflate=False)
    try:
      while not self._stop.is_set():
        if not self._preview_ready.wait(0.05):
          continue
        messages = messaging.drain_sock(sock, wait_for_one=False)
        if not messages:
          self._stop.wait(0.01)
          continue
        self._write_audio(messages)
      if self._preview_ready.is_set():
        self._drain_audio_tail(sock)
    except Exception:
      # Audio is optional; never stop an otherwise healthy video recording.
      cloudlog.exception("camcorder audio recorder failed")

  def _write_audio(self, messages) -> int:
    """Write packets and return the newest logMonoTime written, or 0."""
    if not messages:
      return 0
    with self._lock:
      if self._preview is None:
        return 0
      if self._audio is None:
        self._audio = AudioWriter(self._preview.path)
        for packet in self._preroll_audio:
          self._audio.add_packet(packet.data, packet.sample_rate, packet.log_mono_ns)
          self._audio_after_ns = packet.log_mono_ns
      audio = self._audio
    for event in messages:
      if event.logMonoTime > self._audio_after_ns:
        audio.add_packet(bytes(event.rawAudioData.data),
                         int(event.rawAudioData.sampleRate),
                         int(event.logMonoTime))
    return int(messages[-1].logMonoTime)

  def _drain_audio_tail(self, sock) -> None:
    from openpilot.cereal import messaging

    stop_ns = self._stop_mono_ns
    deadline = time.monotonic() + _AUDIO_TAIL_TIMEOUT_S
    while stop_ns and time.monotonic() < deadline:
      if self._write_audio(messaging.drain_sock(sock, wait_for_one=False)) >= stop_ns:
        return
      time.sleep(0.005)
