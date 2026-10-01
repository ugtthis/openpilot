"""Keep the last few seconds of encoded video and audio between takes.

encoderd and micd already run while the camcorder is on screen, but a take
can only begin on a keyframe. Without a pre-roll a press waits for the next
one, up to a GOP later. With it, a take begins at the keyframe just before
the press, and audio from the same moment.
"""

import threading
from collections import deque
from dataclasses import dataclass

from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.mici.layouts.hevc_writer import V4L2_BUF_FLAG_KEYFRAME

# The keyframe before the press must survive until the take's writer exists,
# which waits for the first preview frame.
_GOPS = 3
# Covers _GOPS at the slowest main-encoder keyframe interval (30 frames, 1.5 s).
_AUDIO_KEEP_NS = 5_000_000_000
_POLL_S = 0.01


@dataclass(frozen=True, slots=True)
class HevcPacket:
  header: bytes
  data: bytes
  keyframe: bool
  width: int
  height: int
  timestamp_ns: int


@dataclass(frozen=True, slots=True)
class AudioPacket:
  data: bytes
  sample_rate: int
  log_mono_ns: int


class _Run:
  def __init__(self, service: str):
    self.service = service
    self.stop = threading.Event()
    self.lock = threading.Lock()
    self.gops: deque[list[HevcPacket]] = deque(maxlen=_GOPS)
    self.audio: deque[AudioPacket] = deque()

  def add_video(self, encoded) -> None:
    packet = HevcPacket(bytes(encoded.header), bytes(encoded.data),
                        bool(encoded.idx.flags & V4L2_BUF_FLAG_KEYFRAME),
                        int(encoded.width), int(encoded.height),
                        int(encoded.idx.timestampEof))
    with self.lock:
      if packet.keyframe and packet.header:
        self.gops.append([packet])
      elif self.gops:
        self.gops[-1].append(packet)

  def add_audio(self, event) -> None:
    packet = AudioPacket(bytes(event.rawAudioData.data), int(event.rawAudioData.sampleRate), int(event.logMonoTime))
    with self.lock:
      self.audio.append(packet)
      while self.audio and self.audio[0].log_mono_ns < packet.log_mono_ns - _AUDIO_KEEP_NS:
        self.audio.popleft()

  def take(self, press_ns: int) -> tuple[list[HevcPacket], list[AudioPacket]]:
    with self.lock:
      gops = list(self.gops)
      audio = list(self.audio)
    first = 0
    for i, gop in enumerate(gops):
      if gop[0].timestamp_ns <= press_ns:
        first = i
    video = [packet for gop in gops[first:] for packet in gop]
    # Audio stamps mark the end of each packet, so keep the one spanning the start.
    start_ns = min(video[0].timestamp_ns, press_ns) if video else press_ns
    return video, [packet for packet in audio if packet.log_mono_ns > start_ns]


class PreRoll:
  def __init__(self):
    self._lock = threading.Lock()
    self._run: _Run | None = None

  def start(self, service: str) -> None:
    with self._lock:
      if self._run is not None and self._run.service == service:
        return
      self._stop_locked()
      run = _Run(service)
      try:
        threading.Thread(target=self._capture, args=(run,), name="camcorder-preroll", daemon=True).start()
      except RuntimeError:
        cloudlog.exception("camcorder pre-roll could not start")
        return
      self._run = run

  def stop(self) -> None:
    """Non-blocking: the capture thread exits on its next poll."""
    with self._lock:
      self._stop_locked()

  def take(self, press_ns: int) -> tuple[list[HevcPacket], list[AudioPacket]]:
    """Return what to start a take with, then stop buffering for the take."""
    with self._lock:
      run = self._run
      self._stop_locked()
    return run.take(press_ns) if run is not None else ([], [])

  def _stop_locked(self) -> None:
    if self._run is not None:
      self._run.stop.set()
      self._run = None

  @staticmethod
  def _capture(run: _Run) -> None:
    from openpilot.cereal import messaging

    try:
      video_sock = messaging.sub_sock(run.service, conflate=False)
      audio_sock = messaging.sub_sock("rawAudioData", conflate=False)
      while not run.stop.is_set():
        for event in messaging.drain_sock(video_sock, wait_for_one=False):
          run.add_video(getattr(event, run.service))
        for event in messaging.drain_sock(audio_sock, wait_for_one=False):
          run.add_audio(event)
        run.stop.wait(_POLL_S)
    except Exception:
      # A missing pre-roll only means a take waits for the next keyframe.
      cloudlog.exception("camcorder pre-roll failed")
