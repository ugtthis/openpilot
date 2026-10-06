from unittest.mock import patch

from opendbc.car.structs import car

from openpilot.common.params import Params
from openpilot.system.manager.manager import drive_start_restarted_processes, ignition_blocked_processes
from openpilot.system.manager.process import ManagerProcess, ensure_running
from openpilot.system.manager.process_config import camera_encoding, camcorder_capture, microphone_capture


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
    assert not microphone_capture(False, params, CP)
    assert camcorder_capture(False, params, CP)
  assert ignition_blocked_processes(started=False, ignition=False) == []


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
    assert microphone_capture(True, params, CP)
    assert not camcorder_capture(True, params, CP)
