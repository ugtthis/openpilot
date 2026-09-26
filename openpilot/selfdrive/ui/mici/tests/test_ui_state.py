from types import SimpleNamespace
from typing import Any, cast

from openpilot.cereal import log
from openpilot.selfdrive.ui.ui_state import UIState


class FakeSubMaster:
  def __init__(self):
    self.updated = {"pandaStates": False, "wideRoadCameraState": False}
    self.alive = {"pandaStates": True, "wideRoadCameraState": False}
    self.valid = {"wideRoadCameraState": False}
    self.data = {
      "pandaStates": [],
      "deviceState": SimpleNamespace(started=False),
    }

  def __getitem__(self, service):
    return self.data[service]


def make_state() -> UIState:
  state = object.__new__(UIState)
  state.sm = cast(Any, FakeSubMaster())
  state.CP = None
  state.is_body = False
  state.ignition = False
  state._ignition_prev = False
  state.panda_type = log.PandaState.PandaType.unknown
  state.light_sensor = -1.0
  state.started = False
  state._ignition_transition_callbacks = []
  return state


def test_ignition_transition_callback_fires_on_edges():
  state = make_state()
  transitions = []
  state.add_ignition_transition_callback(lambda: transitions.append(state.ignition))

  state.sm.updated["pandaStates"] = True
  state.sm.data["pandaStates"] = [
    SimpleNamespace(pandaType=1, ignitionLine=True, ignitionCan=False),
  ]
  state._update_state()
  state._update_state()

  state.sm.data["pandaStates"][0].ignitionLine = False
  state._update_state()

  assert transitions == [True, False]


def test_ignition_stays_true_when_panda_service_dies():
  state = make_state()
  state.ignition = True
  state._ignition_prev = True
  transitions = []
  state.add_ignition_transition_callback(lambda: transitions.append(state.ignition))

  state.sm.alive["pandaStates"] = False
  state._update_state()

  assert state.ignition
  assert state.panda_type == log.PandaState.PandaType.unknown
  assert transitions == []
