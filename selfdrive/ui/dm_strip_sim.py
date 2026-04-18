"""MICI-only UI replay helper: fake engaged **active** distracted countdown.

**Enable:** `RUN_UI_SIMULATE_DM=1` (or `./run_ui.sh --dm`, which sets that before other UI imports).

**Behavior:** While engaged, fake awareness runs 1→0 over RAMP_DURATION_S (matches
`DRIVER_MONITOR_SETTINGS._DISTRACTED_TIME`), then holds at 0 for TERMINAL_HOLD_S so the red
terminal alert is visible, then repeats. Banners match `EventName.*DriverDistracted` in
`selfdrive/selfdrived/events.py`; band edges match `og_dm_*_threshold(True)` (same as the strip).

**Pipeline (this file is the single owner of sim state):**
  `ui.py` — sets env, strips `--dm`, calls `register(ui_state)` (ramp starts on engage).
  `driver_state.py` — `awareness_for_dm_strip` feeds the LED strip; `should_draw` + fade use
    `alert_for_sim_ramp` so dmoji hides whenever a sim banner would show.
  `alert_renderer.py` — `get_alert` uses real `selfdriveState` first, else `alert_for_sim_ramp`."""

from __future__ import annotations

import os
import time
from cereal import car, log

# Same timeline as `selfdrive/monitoring/helpers.py` DRIVER_MONITOR_SETTINGS active distracted
_DISTRACTED_TIME_S = 11.0
RAMP_DURATION_S = _DISTRACTED_TIME_S
# Hold awareness at 0 before looping; otherwise we reset the ramp the same frame we'd hit 0
# and never show `driverDistracted` (red) — only pre → prompt.
_TERMINAL_HOLD_S = 2.5

_ramp_t0: float | None = None


def _sim_on() -> bool:
  return os.environ.get("RUN_UI_SIMULATE_DM", "").strip().lower() in ("1", "true", "yes")


def is_enabled() -> bool:
  return _sim_on()


def register(ui_state) -> None:
  def _on_engaged_transition() -> None:
    global _ramp_t0
    if not _sim_on():
      return
    if ui_state.engaged:
      _ramp_t0 = time.monotonic()
    else:
      _ramp_t0 = None

  if not _sim_on():
    return
  ui_state.add_engaged_transition_callback(_on_engaged_transition)


def _simulated_awareness(engaged: bool) -> float | None:
  """Ramp awareness 1→0 while engaged; None if not simulating this frame."""
  global _ramp_t0
  if not _sim_on() or not engaged:
    return None
  if _ramp_t0 is None:
    _ramp_t0 = time.monotonic()
  elapsed = time.monotonic() - _ramp_t0

  if elapsed < RAMP_DURATION_S:
    return float(1.0 - elapsed / RAMP_DURATION_S)

  # awareness == 0 → terminal red alert; stay here briefly, then loop
  if elapsed < RAMP_DURATION_S + _TERMINAL_HOLD_S:
    return 0.0

  _ramp_t0 = time.monotonic()
  return 1.0


def awareness_for_dm_strip(raw_awareness: float, engaged: bool) -> float:
  sim = _simulated_awareness(engaged)
  return sim if sim is not None else raw_awareness


def alert_for_sim_ramp(engaged: bool):
  """MICI alert row: None = no DM sim banner (fall through to real selfdriveState)."""
  if not _sim_on() or not engaged:
    return None
  aw = _simulated_awareness(engaged)
  if aw is None:
    return None
  from openpilot.selfdrive.ui.mici.onroad.alert_renderer import Alert
  from openpilot.selfdrive.ui.onroad.og_dm_segment_bar import og_dm_pre_threshold, og_dm_prompt_threshold

  AlertSize = log.SelfdriveState.AlertSize
  AlertStatus = log.SelfdriveState.AlertStatus
  VA = car.CarControl.HUDControl.VisualAlert

  # Same band edges as the horizontal strip (`og_dm_display_level` active thresholds).
  pre_th = og_dm_pre_threshold(True)
  prompt_th = og_dm_prompt_threshold(True)

  # Match EventName.*DriverDistracted copy in selfdrive/selfdrived/events.py
  if aw > pre_th:
    return None
  if aw > prompt_th:
    return Alert(
      text1="Pay Attention",
      text2="",
      size=AlertSize.small,
      status=AlertStatus.normal,
      visual_alert=VA.none,
      alert_type="preDriverDistracted/permanent",
    )
  if aw > 0.0:
    return Alert(
      text1="Pay Attention",
      text2="Driver Distracted",
      size=AlertSize.mid,
      status=AlertStatus.userPrompt,
      visual_alert=VA.steerRequired,
      alert_type="promptDriverDistracted/permanent",
    )
  return Alert(
    text1="DISENGAGE IMMEDIATELY",
    text2="Driver Distracted",
    size=AlertSize.full,
    status=AlertStatus.critical,
    visual_alert=VA.steerRequired,
    alert_type="driverDistracted/permanent",
  )
