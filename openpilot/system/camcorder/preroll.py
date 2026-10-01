"""Keep the last few encoded video GOPs between takes.

encoderd already runs while the camcorder is on screen, but a take can only
begin on a keyframe. Direct microphone capture keeps its own bounded pre-roll.
"""

import threading
from collections import deque
from dataclasses import dataclass

from openpilot.common.swaglog import cloudlog
from openpilot.system.camcorder.hevc_writer import V4L2_BUF_FLAG_KEYFRAME

# The keyframe before the press must survive until the take's writer exists,
# which waits for the first preview frame.
_GOPS = 3
_POLL_S = 0.01


@dataclass(frozen=True, slots=True)
class HevcPacket:
  header: bytes
  data: bytes
  keyframe: bool
  width: int
  height: int
  timestamp_ns: int


class _Run:
  def __init__(self, service: str):
    self.service = service
    self.stop = threading.Event()
    self.lock = threading.Lock()
    self.gops: deque[list[HevcPacket]] = deque(maxlen=_GOPS)

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

  def take(self, press_ns: int) -> list[HevcPacket]:
    with self.lock:
      gops = list(self.gops)
    first = 0
    for i, gop in enumerate(gops):
      if gop[0].timestamp_ns <= press_ns:
        first = i
    return [packet for gop in gops[first:] for packet in gop]


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

  def take(self, press_ns: int) -> list[HevcPacket]:
    """Return what to start a take with, then stop buffering for the take."""
    with self._lock:
      run = self._run
      self._stop_locked()
    return run.take(press_ns) if run is not None else []

  def _stop_locked(self) -> None:
    if self._run is not None:
      self._run.stop.set()
      self._run = None

  @staticmethod
  def _capture(run: _Run) -> None:
    from openpilot.cereal import messaging

    try:
      video_sock = messaging.sub_sock(run.service, conflate=False)
      while not run.stop.is_set():
        for event in messaging.drain_sock(video_sock, wait_for_one=False):
          run.add_video(getattr(event, run.service))
        run.stop.wait(_POLL_S)
    except Exception:
      # A missing pre-roll only means a take waits for the next keyframe.
      cloudlog.exception("camcorder pre-roll failed")
