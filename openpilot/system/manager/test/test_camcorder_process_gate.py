from unittest.mock import patch

from opendbc.car.structs import car

from openpilot.common.params import Params
from openpilot.system.manager.manager import ignition_blocked_processes
from openpilot.system.manager.process_config import camera_encoding, camcorder_capture, microphone_capture


def test_offroad_leases_request_capture_processes():
  CP = car.CarParams.new_message()
  params = Params()
  with (
    patch("openpilot.system.manager.process_config.encoder_requested", return_value=True),
    patch("openpilot.system.manager.process_config.mic_requested", return_value=True),
    patch("openpilot.system.manager.process_config.camcorder_requested", return_value=True),
  ):
    assert camera_encoding(False, params, CP)
    assert microphone_capture(False, params, CP)
    assert camcorder_capture(False, params, CP)
  assert ignition_blocked_processes(started=False, ignition=False) == []


def test_ignition_blocks_lease_started_processes_until_onroad():
  assert set(ignition_blocked_processes(started=False, ignition=True)) == {"encoderd", "micd", "camcorderd"}
  assert ignition_blocked_processes(started=True, ignition=True) == []


def test_started_uses_normal_onroad_process_predicates_without_leases():
  CP = car.CarParams.new_message()
  params = Params()
  with (
    patch("openpilot.system.manager.process_config.encoder_requested", return_value=False),
    patch("openpilot.system.manager.process_config.mic_requested", return_value=False),
    patch("openpilot.system.manager.process_config.camcorder_requested", return_value=False),
  ):
    assert camera_encoding(True, params, CP)
    assert microphone_capture(True, params, CP)
    assert not camcorder_capture(True, params, CP)
