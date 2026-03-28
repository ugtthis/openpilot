import os
import subprocess
import threading
from enum import Enum

import pyray as rl
from openpilot.common.basedir import BASEDIR
from openpilot.common.filter_simple import BounceFilter
from openpilot.common.params import Params
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.mici.widgets.button import PRESSED_SCALE
from openpilot.system.ui.lib.application import gui_app

_REPLAY_BINARY = os.path.join(BASEDIR, "tools", "replay", "replay")

# Driving-data messages that replay is allowed to publish during demo.
#
# These are exclusively owned by onroad services (controlsd, modeld, selfdrived,
# etc.) which are NOT running when offroad, so replay can publish them without
# conflicting with any always-running system process.
#
# Camera rendering needs both:
#   * camera state messages (roadCameraState / wideRoadCameraState)
#   * encode-index messages (roadEncodeIdx / wideRoadEncodeIdx)
# The state messages drive UI metadata, while the encode indexes let replay feed
# recorded frames into VisionIPC so the actual route footage appears on-screen.
#
# Intentionally excluded:
#   deviceState  — owned by hardwared (always running). The UI's onroad state
#                  is triggered via the DemoReplayActive param instead.
#   pandaStates  — owned by pandad (always running).
#   can/sendcan  — raw CAN bus, not needed for UI rendering.
#   clocks, managerState, logMessage, etc. — system infrastructure.
_REPLAY_ALLOW_MESSAGES = ",".join([
  "carState", "carParams", "carControl", "carOutput",
  "controlsState", "selfdriveState", "onroadEvents",
  "modelV2", "drivingModelData",
  "radarState", "liveCalibration", "liveParameters", "livePose",
  "driverMonitoringState", "driverStateV2",
  "lateralPlan", "longitudinalPlan",
  "roadCameraState", "wideRoadCameraState",
  "roadEncodeIdx", "wideRoadEncodeIdx",
  "gpsLocationExternal",
])

_DOME_SIZE = 150
_DOME_RIGHT_MARGIN = 40
_DOME_TINT_ACTIVE   = rl.WHITE
_DOME_TINT_INACTIVE = rl.Color(120, 120, 120, 255)


class ReplayState(Enum):
  OFF      = "off"
  RUNNING  = "running"
  STOPPING = "stopping"


class DemoReplayController:
  def __init__(self):
    self._state = ReplayState.OFF
    self._replay_proc: subprocess.Popen | None = None

  @property
  def state(self) -> ReplayState:
    if self._state == ReplayState.RUNNING and self._replay_proc is not None and self._replay_proc.poll() is not None:
      # Replay exited on its own (e.g. end of route). Clean up so the UI
      # doesn't stay stuck in onroad mode.
      self._replay_proc = None
      self._state = ReplayState.OFF
      self._clear_demo_active()
    return self._state

  @property
  def is_running(self) -> bool:
    return self.state == ReplayState.RUNNING

  def toggle(self) -> None:
    if self.state == ReplayState.RUNNING:
      self.stop()
    elif self.state == ReplayState.OFF:
      self.start()

  def start(self) -> None:
    if self._state != ReplayState.OFF:
      return

    Params().put_bool("DemoReplayActive", True)
    self._replay_proc = subprocess.Popen(
      [_REPLAY_BINARY, "--demo", "--allow", _REPLAY_ALLOW_MESSAGES],
      env=os.environ,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
    )

    if self._replay_proc.poll() is not None:
      self._clear_demo_active()
      self._replay_proc = None
      return

    self._state = ReplayState.RUNNING

  def stop(self) -> None:
    if self._state != ReplayState.RUNNING:
      return

    replay_proc = self._replay_proc
    self._replay_proc = None
    self._state = ReplayState.STOPPING
    threading.Thread(target=self._shutdown, args=(replay_proc,), daemon=True).start()

  def _shutdown(self, replay_proc: subprocess.Popen) -> None:
    replay_proc.terminate()
    try:
      replay_proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
      replay_proc.kill()
      replay_proc.wait()

    self._clear_demo_active()
    self._state = ReplayState.OFF

  def _clear_demo_active(self) -> None:
    # Clearing this param causes ui_state.started to go False, transitioning
    # the UI back to offroad. hardwared and pandad have been publishing
    # deviceState/pandaStates throughout (replay never touched those channels),
    # so there is nothing else to flush.
    Params().put_bool("DemoReplayActive", False)


class DemoButton(Widget):
  def __init__(self, texture: rl.Texture):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, _DOME_SIZE + _DOME_RIGHT_MARGIN, _DOME_SIZE))
    self._texture = texture
    self._scale_filter = BounceFilter(1.0, 0.1, 1 / gui_app.target_fps)
    self._click_delay = 0.075
    self._active = False

  def set_active(self, active: bool) -> None:
    self._active = active

  def _render(self, rect: rl.Rectangle) -> None:
    scale = self._scale_filter.update(PRESSED_SCALE if self.is_pressed else 1.0)
    draw_w = _DOME_SIZE * scale
    draw_h = _DOME_SIZE * scale
    x = rect.x + (_DOME_SIZE - draw_w) / 2
    y = rect.y + (_DOME_SIZE - draw_h) / 2
    tint = _DOME_TINT_ACTIVE if self._active else _DOME_TINT_INACTIVE
    rl.draw_texture_pro(
      self._texture,
      rl.Rectangle(0, 0, self._texture.width, self._texture.height),
      rl.Rectangle(x, y, draw_w, draw_h),
      rl.Vector2(0, 0), 0.0, tint,
    )
