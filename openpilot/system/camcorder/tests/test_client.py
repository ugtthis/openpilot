from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from openpilot.cereal.visionipc import VisionStreamType
from openpilot.system.camcorder.client import CamcorderClient

WIDE = VisionStreamType.VISION_STREAM_WIDE_ROAD


class PubMaster:
  def __init__(self):
    self.messages = []

  def send(self, service, message):
    self.messages.append((service, message))


class SubMaster:
  def __init__(self):
    self.updated = {"camcorderState": False}
    self.state = SimpleNamespace(sequence=0, phase="idle", elapsedS=0.0, clipId="")

  def update(self, timeout):
    pass

  def __getitem__(self, service):
    assert service == "camcorderState"
    return self.state


def test_commands_repeat_until_the_daemon_acknowledges_them():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  client._phase = "idle"
  with patch("openpilot.system.camcorder.client.acquire_camcorder"):
    assert client.start(WIDE, 123)

  assert len(pm.messages) == 1
  command = pm.messages[-1][1].camcorderControl
  assert command.action == "start"
  assert command.requestMonoTime == 123

  client.update()
  assert len(pm.messages) == 2

  sm.updated["camcorderState"] = True
  sm.state = SimpleNamespace(sequence=1, phase="recording", elapsedS=2.5, clipId="")
  client.update()
  assert len(pm.messages) == 2
  assert client.recording
  assert client.elapsed_s == 2.5


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
    client.set_warm(True, VisionStreamType.VISION_STREAM_CABIN)
    assert not client.start(VisionStreamType.VISION_STREAM_CABIN, 123)
  command = pm.messages[-1][1].camcorderControl
  assert command.action == "idle"
  assert command.stream == "cabin"


def test_leaving_the_page_keeps_the_lease_until_stop_finishes():
  pm, sm = PubMaster(), SubMaster()
  client = CamcorderClient(pm, sm)
  client._phase = "idle"
  clip = object()
  with (
    patch("openpilot.system.camcorder.client.acquire_camcorder") as acquire,
    patch("openpilot.system.camcorder.client.release_camcorder") as release,
    patch("openpilot.system.camcorder.client.clips_root", return_value=Path("/clips")),
    patch("openpilot.system.camcorder.client.load_clip", return_value=clip) as load,
  ):
    client.start(WIDE, 100)
    client.set_warm(False, WIDE)
    release.assert_not_called()
    client.stop(200)

    sm.updated["camcorderState"] = True
    sm.state = SimpleNamespace(sequence=2, phase="warming", elapsedS=0.0, clipId="saved")
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
  with (
    patch("openpilot.system.camcorder.client.acquire_camcorder"),
    patch("openpilot.system.camcorder.client.clips_root", return_value=Path("/clips")),
    patch("openpilot.system.camcorder.client.load_clip", return_value=clip),
  ):
    assert client.start(WIDE, 100)
    sm.updated["camcorderState"] = True
    sm.state = SimpleNamespace(sequence=1, phase="warming", elapsedS=0.0, clipId="salvaged",
                               error="preview capture failed")
    assert client.update() is clip

  assert not client.recording
  assert client.error == "preview capture failed"
