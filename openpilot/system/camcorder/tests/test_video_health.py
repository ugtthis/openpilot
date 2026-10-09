from openpilot.system.camcorder.video_health import (
  READY_AFTER_S, STALE_AFTER_S, VIDEO_STALL_TIMEOUT_S, VIDEO_START_TIMEOUT_S, VideoHealth, take_video_error,
)

FULL = (2688, 1520)
STOCK = (1344, 760)


def _steady(health: VideoHealth, size: tuple[int, int], start: float, seconds: float, fps: int = 10) -> float:
  """Packets every 1/fps from start through start + seconds; returns the last arrival time."""
  frames = round(seconds * fps)
  for i in range(frames + 1):
    health.note_packet(*size, now=start + i / fps)
  return start + frames / fps


def test_no_packets_is_not_ready():
  health = VideoHealth()
  assert not health.ready(STOCK, now=100.0)
  assert health.packet_age(now=100.0) == float("inf")


def test_ready_only_after_packets_have_been_steady_for_a_moment():
  health = VideoHealth()
  health.note_packet(*STOCK, now=100.0)
  assert not health.ready(STOCK, now=100.0)

  assert not health.ready(STOCK, now=_steady(health, STOCK, 100.0, READY_AFTER_S - 0.1))
  assert health.ready(STOCK, now=_steady(health, STOCK, 100.0, READY_AFTER_S + 0.1))


def test_a_stalled_encoder_stops_being_ready():
  # encoderd can be alive but starved, e.g. attached to a camerad that has exited.
  health = VideoHealth()
  last = _steady(health, STOCK, 100.0, 1.0)
  assert health.ready(STOCK, now=last)
  assert not health.ready(STOCK, now=last + STALE_AFTER_S + 0.01)

  # Packets resuming have to prove themselves steady again.
  health.note_packet(*STOCK, now=last + 5.0)
  assert not health.ready(STOCK, now=last + 5.0)


def test_packets_from_the_previous_mode_are_not_ready_for_the_new_one():
  health = VideoHealth()
  last = _steady(health, STOCK, 100.0, 1.0)
  assert health.ready(STOCK, now=last)
  assert not health.ready(FULL, now=last)

  last = _steady(health, FULL, last + 0.1, 0.1)
  assert not health.ready(FULL, now=last)
  last = _steady(health, FULL, last + 0.1, READY_AFTER_S)
  assert health.ready(FULL, now=last)


def test_take_stops_when_its_video_never_starts():
  assert take_video_error(False, take_age_s=VIDEO_START_TIMEOUT_S - 0.1, packet_age_s=99.0) == ""
  assert take_video_error(False, take_age_s=VIDEO_START_TIMEOUT_S + 0.1, packet_age_s=99.0) == "encoded video did not start"


def test_take_stops_when_its_video_goes_quiet():
  assert take_video_error(True, take_age_s=60.0, packet_age_s=0.05) == ""
  assert take_video_error(True, take_age_s=60.0, packet_age_s=VIDEO_STALL_TIMEOUT_S + 0.1) == "encoded video stopped"
