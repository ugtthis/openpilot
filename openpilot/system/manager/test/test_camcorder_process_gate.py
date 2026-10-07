from unittest.mock import patch

from opendbc.car.structs import car

from openpilot.common.params import Params
from openpilot.system.camcorder.settings import ENCODER_MODE_PARAM, SENSOR_MODE_PARAM, CamcorderSettings, Quality, Resolution
from openpilot.system.manager.manager import (
  camcorder_stock_required, desired_camcorder_sensor_mode, drive_start_restarted_processes, ignition_blocked_processes, update_camcorder_sensor_mode,
)
from openpilot.system.manager.process import ManagerProcess, ensure_running
from openpilot.system.manager.process_config import camera_encoding, camcorder_capture, managed_processes


class FakeProcess(ManagerProcess):
  def __init__(self, name):
    self.name = name
    self.enabled = True
    self.should_run = lambda started, params, CP: True
    self.generation = 0
    self.running = False

  def start(self):
    if not self.running:
      self.running = True
      self.generation += 1

  def stop(self, retry=True, block=True, sig=None):
    self.running = False


def test_offroad_leases_request_capture_processes():
  CP = car.CarParams.new_message()
  params = Params()
  with (
    patch("openpilot.system.manager.process_config.encoder_requested", return_value=True),
    patch("openpilot.system.manager.process_config.camcorder_requested", return_value=True),
  ):
    assert camera_encoding(False, params, CP)
    assert camcorder_capture(False, params, CP)
    assert managed_processes["camerad"].should_run(False, params, CP)
  assert ignition_blocked_processes(started=False, ignition=False) == []


def test_camerad_stays_off_offroad_without_a_viewer(tmp_path):
  CP = car.CarParams.new_message()
  params = Params(str(tmp_path / "params"))
  with patch("openpilot.system.manager.process_config.camcorder_requested", return_value=False):
    assert not managed_processes["camerad"].should_run(False, params, CP)
    assert managed_processes["camerad"].should_run(True, params, CP)


def test_ignition_blocks_lease_started_processes_until_onroad():
  assert set(ignition_blocked_processes(started=False, ignition=True)) == {"encoderd", "camcorderd"}
  assert ignition_blocked_processes(started=True, ignition=True) == []


def test_drive_start_restarts_camcorder_shared_processes_once():
  assert set(drive_start_restarted_processes(started=True, started_prev=False)) == {"camerad", "encoderd"}
  assert drive_start_restarted_processes(started=True, started_prev=True) == []
  assert drive_start_restarted_processes(started=False, started_prev=True) == []
  assert drive_start_restarted_processes(started=False, started_prev=False) == []


def test_offroad_processes_are_replaced_when_a_drive_starts():
  # Ignition and drive start can arrive in the same loop, so the ignition hold never applies.
  procs = [FakeProcess("camerad"), FakeProcess("encoderd")]
  params, CP = Params(), car.CarParams.new_message()
  started_prev = False
  for started in (False, False, True, True, True):
    not_run = drive_start_restarted_processes(started, started_prev)
    ensure_running(procs, started, params=params, CP=CP, not_run=not_run)
    started_prev = started

  for p in procs:
    assert p.running, p.name
    assert p.generation == 2, p.name


def test_started_uses_normal_onroad_process_predicates_without_leases():
  CP = car.CarParams.new_message()
  params = Params()
  with (
    patch("openpilot.system.manager.process_config.encoder_requested", return_value=False),
    patch("openpilot.system.manager.process_config.camcorder_requested", return_value=False),
  ):
    assert camera_encoding(True, params, CP)
    assert managed_processes["camerad"].should_run(True, params, CP)
    assert not camcorder_capture(True, params, CP)


def test_camcorder_mode_change_restarts_shared_processes_and_drive_restores_stock(tmp_path):
  params = Params(str(tmp_path / "params"))
  CamcorderSettings(Quality.MAX, 60, resolution=Resolution.FULL).save(params)

  assert desired_camcorder_sensor_mode(False, params) == "2688x1520@60"
  assert set(update_camcorder_sensor_mode(False, params)) == {"camerad", "encoderd"}
  assert params.get(SENSOR_MODE_PARAM) == "2688x1520@60"
  assert params.get(ENCODER_MODE_PARAM) == "2688x1520@60:max"
  assert update_camcorder_sensor_mode(False, params) == []

  assert set(update_camcorder_sensor_mode(True, params)) == {"camerad", "encoderd"}
  assert params.get(SENSOR_MODE_PARAM) == "stock"
  assert params.get(ENCODER_MODE_PARAM) == "stock:stock"


def test_camcorder_modes_stay_stock_until_ignition_is_known_off():
  with patch("openpilot.system.manager.manager.CAMCORDER_MODES_SUPPORTED", True):
    # manager restarting mid-drive: no pandaStates yet, so ignition is unknown
    assert camcorder_stock_required(started=False, ignition=False, panda_state_seen=False)
    assert camcorder_stock_required(started=False, ignition=True, panda_state_seen=True)
    assert camcorder_stock_required(started=True, ignition=False, panda_state_seen=True)
    assert not camcorder_stock_required(started=False, ignition=False, panda_state_seen=True)
  with patch("openpilot.system.manager.manager.CAMCORDER_MODES_SUPPORTED", False):
    assert camcorder_stock_required(started=False, ignition=False, panda_state_seen=True)


def test_mode_change_waits_for_take_but_stock_never_waits(tmp_path):
  params = Params(str(tmp_path / "params"))
  CamcorderSettings(Quality.MAX, 60, resolution=Resolution.FULL).save(params)
  assert update_camcorder_sensor_mode(False, params, take_active=True) == []
  assert params.get(SENSOR_MODE_PARAM, return_default=True) == "stock"

  update_camcorder_sensor_mode(False, params)
  assert set(update_camcorder_sensor_mode(True, params, take_active=True)) == {"camerad", "encoderd"}
  assert params.get(ENCODER_MODE_PARAM) == "stock:stock"


def test_quality_only_change_restarts_only_encoderd(tmp_path):
  params = Params(str(tmp_path / "params"))
  CamcorderSettings().save(params)
  update_camcorder_sensor_mode(False, params)
  CamcorderSettings(quality=Quality.MAX).save(params)
  assert update_camcorder_sensor_mode(False, params) == ["encoderd"]
