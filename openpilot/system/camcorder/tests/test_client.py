from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.client import CamcorderClient

WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD
CABIN = VisionStreamType.VISION_STREAM_CABIN


def state(**fields):
  defaults = {"sessionId": 1, "sequence": 0, "phase": "idle", "elapsedS": 0.0, "remainingS": 3600.0,
              "clipId": "", "error": "", "notice": "none", "micName": ""}
  return SimpleNamespace(**(defaults | fields))


class PubMaster:
  def __init__(self):
    self.messages = []

  def send(self, service, message):
    self.messages.append((service, message))

  @property
  def last_command(self):
    return self.messages[-1][1].camcorderControl


class SubMaster:
  def __init__(self):
    self.updated = {"camcorderState": False}
    self.state = state()

  def publish(self, **fields):
    self.updated["camcorderState"] = True
    self.state = state(**fields)

  def update(self, timeout):
    pass

  def __getitem__(self, service):
    assert service == "camcorderState"
    return self.state


def recording_client(stream=WIDE):
  """A client whose take was acknowledged by recorder session 1."""
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  sm.publish(phase="idle")
  with patch("openpilot.system.camcorder.client.acquire_camcorder"):
    client.update()
    assert client.start(stream, 100)
  sm.publish(sequence=1, phase="recording", elapsedS=2.0)
  client.update()
  assert client.recording
  return client, pm, sm


def patch_clip_loading(clip):
  return (
    patch("openpilot.system.camcorder.client.clips_root", return_value=Path("/clips")),
    patch("openpilot.system.camcorder.client.load_clip", return_value=clip),
  )


def test_commands_repeat_until_the_daemon_acknowledges_them():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  client._phase = "idle"
  with patch("openpilot.system.camcorder.client.acquire_camcorder"):
    assert client.start(WIDE, 123)

  assert len(pm.messages) == 1
  assert pm.last_command.action == "start"
  assert pm.last_command.requestMonoTime == 123

  client.update()
  assert len(pm.messages) == 2

  sm.publish(sequence=1, phase="recording", elapsedS=2.5)
  client.update()
  assert len(pm.messages) == 2
  assert client.recording
  assert client.elapsed_s == 2.5


def test_remaining_time_is_unknown_until_the_recorder_reports_it():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  assert client.remaining_s is None

  sm.publish(remainingS=125.0)
  client.update()
  assert client.remaining_s == 125.0


def test_mic_label_follows_the_recorder_and_clears_when_the_mic_stops_working():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  assert client.mic_label == ""

  sm.publish(micName="DJI USB Audio")
  client.update()
  assert client.mic_label == "USB mic"

  sm.publish(micName="")
  client.update()
  assert client.mic_label == ""


def test_recording_cannot_start_before_preroll_is_ready():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  with patch("openpilot.system.camcorder.client.acquire_camcorder"):
    client.set_warm(True, WIDE)
    assert not client.start(WIDE, 123)
  assert pm.messages == []


def test_camera_switch_returns_to_warming_until_the_new_stream_is_ready():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  client._phase = "idle"
  with patch("openpilot.system.camcorder.client.acquire_camcorder"):
    client.set_warm(True, CABIN)
    assert not client.start(CABIN, 123)
  assert pm.last_command.action == "idle"
  assert pm.last_command.stream == "cabin"


def test_leaving_the_page_keeps_the_lease_until_stop_finishes():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  client._phase = "idle"
  clip = object()
  clips_root, load_clip = patch_clip_loading(clip)
  with (
    patch("openpilot.system.camcorder.client.acquire_camcorder") as acquire,
    patch("openpilot.system.camcorder.client.release_camcorder") as release,
    clips_root, load_clip as load,
  ):
    client.start(WIDE, 100)
    client.set_warm(False, WIDE)
    release.assert_not_called()
    client.stop(200)

    sm.publish(sequence=2, phase="warming", clipId="saved")
    assert client.update() is clip

  acquire.assert_called_once()
  release.assert_called_once()
  load.assert_called_once_with(Path("/clips/saved"))
  assert not client.recording


def test_failed_recording_returns_a_salvaged_clip_and_the_error():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  client._phase = "idle"
  clip = object()
  clips_root, load_clip = patch_clip_loading(clip)
  with patch("openpilot.system.camcorder.client.acquire_camcorder"), clips_root, load_clip:
    assert client.start(WIDE, 100)
    sm.publish(sequence=1, phase="warming", clipId="salvaged",
               error="preview capture failed", notice="recordingErrorSaved")
    assert client.update() is clip

  assert not client.recording
  assert client.error == "Camera error — clip saved"

  client.dismiss_error()
  assert client.error == ""
  assert client.update() is None
  assert client.error == ""


def test_saved_clip_with_a_timeline_gap_shows_a_dismissible_warning():
  client, _, sm = recording_client()
  clip = object()
  clips_root, load_clip = patch_clip_loading(clip)

  sm.publish(sequence=2, phase="warming", clipId="saved", notice="timelineGapSaved")
  with clips_root, load_clip:
    assert client.update() is clip

  assert client.error == "Recording gap detected — clip saved"
  client.dismiss_error()
  assert client.error == ""


def test_recorder_restart_returns_the_recovered_clip_to_the_existing_ui():
  client, pm, sm = recording_client()
  clip = object()
  clips_root, load_clip = patch_clip_loading(clip)

  sm.publish(sessionId=2, phase="warming", clipId="recovered", notice="recordingRecovered")
  with clips_root, load_clip:
    assert client.update() is clip

  assert not client.recording
  assert client.error == "Recorder restarted — clip recovered"


def test_recorder_restart_without_a_clip_unlatches_even_after_stop_was_pressed():
  client, pm, sm = recording_client()
  client.stop(200)
  assert pm.last_command.action == "stop"

  sm.publish(sessionId=2, phase="warming")
  assert client.update() is None

  assert not client.recording
  assert client.error == "Recorder restarted — no clip recovered"


def test_recorder_restart_rewarms_the_camera_the_ui_is_showing():
  client, pm, sm = recording_client(CABIN)
  client.set_warm(True, CABIN)

  sm.publish(sessionId=2, phase="warming", clipId="recovered")
  clips_root, load_clip = patch_clip_loading(object())
  with clips_root, load_clip:
    client.update()

  assert pm.last_command.action == "idle"
  assert pm.last_command.stream == "cabin"


def test_slow_recorder_is_not_mistaken_for_a_dead_one():
  client, pm, sm = recording_client()
  sm.updated["camcorderState"] = False
  last_update = client._last_state_update

  with patch("openpilot.system.camcorder.client.time.monotonic", return_value=last_update + 5.0):
    client.update()

  assert client.recording
  assert client.error == ""


def test_recorder_that_never_returns_does_not_leave_the_shutter_latched():
  client, pm, sm = recording_client()
  client.stop(200)
  sm.updated["camcorderState"] = False
  last_update = client._last_state_update

  with patch("openpilot.system.camcorder.client.time.monotonic", return_value=last_update + 11.0):
    client.update()

  assert not client.recording
  assert client.error == "Recorder unavailable — reopen camera"
