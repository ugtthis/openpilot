import pytest

from openpilot.selfdrive.ui.mici.layouts.playback_transport import PlaybackTransport


def playing_transport(duration: float = 10.0, now: float = 100.0) -> PlaybackTransport:
  transport = PlaybackTransport()
  transport.reset_for_show(duration, photo=False, now=now)
  return transport


def test_audio_is_master_and_wall_clock_resumes_from_its_last_position():
  transport = playing_transport()

  assert not transport.tick(100.5, audio_playhead=0.2)
  assert transport.playhead == pytest.approx(0.2)
  assert not transport.tick(101.0, audio_playhead=0.8)
  assert transport.playhead == pytest.approx(0.8)

  assert not transport.tick(101.5, audio_playhead=None)
  assert transport.playhead == pytest.approx(1.3)


def test_wall_clock_advances_without_audio_and_stops_at_the_end():
  transport = playing_transport(duration=2.0, now=0.0)

  assert not transport.tick(1.5, None)
  assert transport.playhead == pytest.approx(1.5)
  assert transport.tick(2.5, None)
  assert transport.playhead == 2.0
  assert not transport.playing

  transport.tick(4.0, None)
  assert transport.playhead == 2.0


def test_seek_clamps_and_preserves_playing_until_it_reaches_the_end():
  transport = playing_transport(duration=5.0, now=0.0)

  transport.seek(3.0, now=10.0)
  assert transport.playhead == 3.0
  assert transport.playing
  transport.seek(99.0, now=11.0)
  assert transport.playhead == 5.0
  assert not transport.playing
  transport.seek(-1.0, now=12.0)
  assert transport.playhead == 0.0
  assert not transport.playing


def test_toggle_pause_resume_and_restart_from_end_preserve_sync_order():
  transport = playing_transport(duration=5.0, now=0.0)
  transport.seek(3.0, now=10.0)

  assert transport.toggle(now=10.0) == (3.0, False)
  assert not transport.playing
  assert transport.toggle(now=10.0) == (3.0, True)
  assert transport.playing
  transport.tick(11.0, None)
  assert transport.playhead == 4.0

  transport.seek(9.0, now=12.0)
  assert transport.at_end
  assert transport.toggle(now=13.0) == (0.0, False)
  assert transport.playhead == 0.0
  assert transport.playing


def test_photo_transport_never_starts():
  transport = PlaybackTransport()
  transport.reset_for_show(0.0, photo=True, now=0.0)

  assert transport.toggle(now=1.0) is None
  assert not transport.tick(5.0, None)
  assert transport.playhead == 0.0
  assert not transport.playing


def test_transport_exposes_display_state_and_mute():
  transport = playing_transport(duration=4.0, now=0.0)
  transport.seek(1.2349, now=1.0)

  assert transport.playhead_ms == 1234
  assert transport.progress == pytest.approx(1.2349 / 4.0)
  assert transport.toggle_mute()
  assert transport.muted
  transport.reset_for_open()
  assert not transport.muted

  transport.stop()
  assert not transport.playing
  assert transport.playhead == pytest.approx(1.2349)
