#!/usr/bin/env python3
import os
import sys

# Before other `selfdrive.ui` imports (MICI pulls in dm strip code that reads RUN_UI_SIMULATE_DM).
if "--dm" in sys.argv:
  os.environ["RUN_UI_SIMULATE_DM"] = "1"
  sys.argv = [a for a in sys.argv if a != "--dm"]

from openpilot.system.hardware import TICI
from openpilot.common.realtime import config_realtime_process, set_core_affinity
from openpilot.system.ui.lib.application import gui_app
from openpilot.selfdrive.ui.layouts.main import MainLayout
from openpilot.selfdrive.ui.mici.layouts.main import MiciMainLayout
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.dm_strip_sim import register as register_dm_strip_sim

BIG_UI = gui_app.big_ui()

register_dm_strip_sim(ui_state)


def main():
  cores = {5, }
  config_realtime_process(0, 51)

  gui_app.init_window("UI")
  if BIG_UI:
    MainLayout()
  else:
    MiciMainLayout()

  for should_render in gui_app.render():
    ui_state.update()
    if should_render:
      # reaffine after power save offlines our core
      if TICI and os.sched_getaffinity(0) != cores:
        try:
          set_core_affinity(list(cores))
        except OSError:
          pass


if __name__ == "__main__":
  main()
