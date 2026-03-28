import os
import subprocess
import threading
import time
from enum import Enum

import pyray as rl
from cereal import log, messaging
from openpilot.common.basedir import BASEDIR
from openpilot.common.filter_simple import BounceFilter
from openpilot.system.ui.widgets import Widget
from openpilot.selfdrive.ui.mici.widgets.button import PRESSED_SCALE
from openpilot.system.ui.lib.application import gui_app

_REPLAY_BINARY = os.path.join(BASEDIR, "tools", "replay", "replay")

_OFFROAD_FLUSH_FRAMES = 20
_OFFROAD_FLUSH_DT = 0.05

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
    # Detect when replay exited on its own without an explicit stop()
    if self._state == ReplayState.RUNNING and self._replay_proc is not None and self._replay_proc.poll() is not None:
      self._replay_proc = None
      self._state = ReplayState.OFF
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

    # Block system-level messages published by always-running device services.
    # Replay would kill their publisher sockets causing those processes to crash.
    # The UI only needs the driving data messages (carState, modelV2, etc.) from replay.
    _SYSTEM_BLOCK = "clocks,managerState,peripheralState,logMessage,errorLogMessage,androidLog,procLog,uploaderState"
    self._replay_proc = subprocess.Popen(
      [_REPLAY_BINARY, "--demo", "--block", _SYSTEM_BLOCK],
      env=os.environ,
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
    )

    # Detect immediate failure (binary missing or instant crash)
    if self._replay_proc.poll() is not None:
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

    self._restore_offroad_state()
    self._state = ReplayState.OFF

  def _restore_offroad_state(self) -> None:
    # SubMaster caches the last received message, so when replay stops the UI
    # stays "onroad" because deviceState.started and ignition are never reset.
    # Publish explicit offroad messages to flush those cached values.
    pm = messaging.PubMaster(["deviceState", "pandaStates"])
    for _ in range(_OFFROAD_FLUSH_FRAMES):
      ds = messaging.new_message("deviceState")
      ds.deviceState.started = False
      pm.send("deviceState", ds)

      ps = messaging.new_message("pandaStates", 1)
      ps.pandaStates[0].ignitionLine = False
      ps.pandaStates[0].pandaType = log.PandaState.PandaType.uno
      pm.send("pandaStates", ps)
      time.sleep(_OFFROAD_FLUSH_DT)


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
