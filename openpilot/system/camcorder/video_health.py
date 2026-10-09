"""Whether a camera's encoded video is actually arriving, judged by packet arrival times."""

import math
import time

# Steady packets for this long before the shutter is offered, so an encoder is
# trusted only once it is delivering, not on its first frame.
READY_AFTER_S = 0.3
# Far longer than the gap between frames at any supported frame rate.
STALE_AFTER_S = 0.5


class VideoHealth:
  def __init__(self):
    self._flowing_since: float | None = None
    self._last_packet = 0.0
    self._size = (0, 0)

  def note_packet(self, width: int, height: int, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    size = (width, height)
    if self._flowing_since is None or now - self._last_packet > STALE_AFTER_S or size != self._size:
      self._flowing_since = now
    self._last_packet = now
    self._size = size

  def packet_age(self, now: float | None = None) -> float:
    if self._flowing_since is None:
      return math.inf
    return (time.monotonic() if now is None else now) - self._last_packet

  def ready(self, expected_size: tuple[int, int], now: float | None = None) -> bool:
    """Packets of the expected size have been arriving steadily up to now."""
    now = time.monotonic() if now is None else now
    return (self._flowing_since is not None and self._size == expected_size and
            self.packet_age(now) <= STALE_AFTER_S and now - self._flowing_since >= READY_AFTER_S)
