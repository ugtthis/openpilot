#!/usr/bin/env python3
"""
Cycle every on-road notification variant from `selfdrived.EVENTS`.
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import time
import tty
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from cereal import car, log
import cereal.messaging as messaging
from msgq import MultiplePublishersError
from opendbc.car.honda.interface import CarInterface
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.selfdrived.events import ET, Events, EVENTS, EVENT_NAME
from openpilot.system.manager.process_config import managed_processes

# ---------------------------------------------------------------------------
# Constants & mappings
# ---------------------------------------------------------------------------

EventName = log.OnroadEvent.EventName
PAUSE_KEY = " "
SKIP_FAMILY_KEY = "s"

ALERT_SIZE_LABELS = {
  log.SelfdriveState.AlertSize.none: "AlertSize.none",
  log.SelfdriveState.AlertSize.small: "AlertSize.small",
  log.SelfdriveState.AlertSize.mid: "AlertSize.mid",
  log.SelfdriveState.AlertSize.full: "AlertSize.full",
}
ALERT_SIZE_LABEL_WIDTH = max(len(v) for v in ALERT_SIZE_LABELS.values())
ET_TO_UI_STATES = {
  "ENABLE": {"DISENGAGED"},
  "NO_ENTRY": {"DISENGAGED"},
  "PRE_ENABLE": {"OVERRIDE"},
  "OVERRIDE_LATERAL": {"OVERRIDE"},
  "OVERRIDE_LONGITUDINAL": {"OVERRIDE"},
  "WARNING": {"ENGAGED"},
  "USER_DISABLE": {"ENGAGED"},
  "SOFT_DISABLE": {"ENGAGED"},
  "IMMEDIATE_DISABLE": {"ENGAGED"},
  "PERMANENT": {"DISENGAGED", "ENGAGED"},
}

EVENT_TYPE_TO_ET = {
  "ENABLE": ET.ENABLE,
  "PRE_ENABLE": ET.PRE_ENABLE,
  "OVERRIDE_LATERAL": ET.OVERRIDE_LATERAL,
  "OVERRIDE_LONGITUDINAL": ET.OVERRIDE_LONGITUDINAL,
  "NO_ENTRY": ET.NO_ENTRY,
  "WARNING": ET.WARNING,
  "USER_DISABLE": ET.USER_DISABLE,
  "SOFT_DISABLE": ET.SOFT_DISABLE,
  "IMMEDIATE_DISABLE": ET.IMMEDIATE_DISABLE,
  "PERMANENT": ET.PERMANENT,
}
ET_VALUE_TO_NAME = {v: k for k, v in EVENT_TYPE_TO_ET.items()}
EVENT_TYPE_PLAYBACK_ORDER = {
  "NO_ENTRY": 0,
  "PRE_ENABLE": 1,
  "ENABLE": 2,
  "OVERRIDE_LATERAL": 3,
  "OVERRIDE_LONGITUDINAL": 4,
  "WARNING": 5,
  "USER_DISABLE": 6,
  "SOFT_DISABLE": 7,
  "IMMEDIATE_DISABLE": 8,
  "PERMANENT": 9,
}
UI_STATE_SORT_ORDER = {
  "UIStatus.DISENGAGED": 0,
  "UIStatus.OVERRIDE": 1,
  "UIStatus.ENGAGED": 2,
}
UI_STATE_ORDER = ("DISENGAGED", "OVERRIDE", "ENGAGED")
UI_STATE_LABEL_WIDTH = max(len(k) for k in UI_STATE_SORT_ORDER.keys())
CAN_SHOW_IN_WIDTH = len("DISENGAGED+ENGAGED")


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

class FakeSubMaster:
  """Writable minimal SubMaster-like object for alert callback evaluation."""

  def __init__(self, services: list[str]):
    self.services = services
    self.data = {}
    self.alive = dict.fromkeys(services, True)
    self.valid = dict.fromkeys(services, True)
    self.freq_ok = dict.fromkeys(services, True)

    for s in services:
      try:
        msg = messaging.new_message(s)
      except Exception:
        msg = messaging.new_message(s, 0)
      self.data[s] = getattr(msg, s)

  def __getitem__(self, s: str):
    return self.data[s]

  def all_alive(self, service_list: list[str] | None = None) -> bool:
    return all(self.alive[s] for s in (service_list or self.services))

  def all_freq_ok(self, service_list: list[str] | None = None) -> bool:
    return all(self.freq_ok[s] for s in (service_list or self.services))

  def all_valid(self, service_list: list[str] | None = None) -> bool:
    return all(self.valid[s] for s in (service_list or self.services))

  def all_checks(self, service_list: list[str] | None = None) -> bool:
    return self.all_alive(service_list) and self.all_freq_ok(service_list) and self.all_valid(service_list)


class PauseController:
  """Keyboard input handler for interactive terminals."""

  def __init__(self, pause_key: str, skip_key: str):
    self.pause_key = pause_key
    self.skip_key = skip_key
    self._fd: int | None = None
    self._original_attrs = None
    self.enabled = False

  def __enter__(self):
    if not sys.stdin.isatty():
      return self
    try:
      self._fd = sys.stdin.fileno()
      self._original_attrs = termios.tcgetattr(self._fd)
      tty.setcbreak(self._fd)
      self.enabled = True
    except Exception:
      self.enabled = False
    return self

  def __exit__(self, exc_type, exc, tb):
    if self.enabled and self._fd is not None and self._original_attrs is not None:
      termios.tcsetattr(self._fd, termios.TCSADRAIN, self._original_attrs)

  def poll_action(self) -> str | None:
    if not self.enabled or self._fd is None:
      return None
    ready, _, _ = select.select([self._fd], [], [], 0)
    if not ready:
      return None
    try:
      ch = os.read(self._fd, 1).decode(errors="ignore")
    except Exception:
      return None
    key = ch.lower()
    if key == self.pause_key.lower():
      return "toggle_pause"
    if key == self.skip_key.lower():
      return "skip_family"
    return None


# ---------------------------------------------------------------------------
# Data model & CLI
# ---------------------------------------------------------------------------

@dataclass
class NotificationVariant:
  event: str
  event_type: str
  row_index: int

  @property
  def alert_id(self) -> str:
    return f"{self.event}/{EVENT_TYPE_TO_ET[self.event_type]}"


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="Publish every on-road notification variant from selfdrived EVENTS."
  )
  parser.add_argument(
    "--dwell-seconds",
    type=float,
    default=1.0,
    help="How long each notification is held on screen.",
  )
  parser.add_argument(
    "--first-dwell-seconds",
    type=float,
    default=2.0,
    help="How long alert #1 is held on screen.",
  )
  parser.add_argument(
    "--warmup-seconds",
    type=float,
    default=3.0,
    help="Warmup hold before counting alert #1 (helps avoid first-alert fade-in loss).",
  )
  parser.add_argument(
    "--section-pause-seconds",
    type=float,
    default=0.75,
    help="Pause inserted between section transitions.",
  )
  parser.add_argument(
    "--metric",
    action="store_true",
    help="Render metric-speed callback variants where applicable.",
  )
  parser.add_argument(
    "--limit",
    type=int,
    default=0,
    help="Optional cap on total variants (0 means no cap).",
  )
  parser.add_argument(
    "--start-at",
    type=int,
    default=1,
    help="1-based index to resume from within the sorted run order.",
  )
  parser.add_argument(
    "--ui-pid",
    type=int,
    default=0,
    help="Optional UI process ID to monitor; demo exits if that process closes.",
  )
  parser.add_argument(
    "--self-test",
    action="store_true",
    help="Run parser/coverage/callback smoke checks only (no UI publishing loop).",
  )
  return parser.parse_args()


# ---------------------------------------------------------------------------
# Variant builder
# ---------------------------------------------------------------------------


def event_type_slug(event_type: str) -> str:
  parts = [p.lower() for p in event_type.split("_") if p]
  if not parts:
    return event_type.lower()
  return parts[0] + "".join(p.capitalize() for p in parts[1:])


def resolve_ui_state_label(variant: NotificationVariant, forced_ui_state: str | None = None) -> str:
  if forced_ui_state is not None:
    return forced_ui_state
  if variant.event_type in {"PRE_ENABLE", "OVERRIDE_LATERAL", "OVERRIDE_LONGITUDINAL"}:
    return "UIStatus.OVERRIDE"

  if variant.event_type in {"WARNING", "SOFT_DISABLE", "IMMEDIATE_DISABLE", "USER_DISABLE"}:
    return "UIStatus.ENGAGED"
  return "UIStatus.DISENGAGED"


def variant_can_show_in_states(variant: NotificationVariant) -> list[str]:
  states = ET_TO_UI_STATES.get(variant.event_type, set())
  return [s for s in UI_STATE_ORDER if s in states]


def variant_can_show_in_label(variant: NotificationVariant) -> str:
  ordered = variant_can_show_in_states(variant)
  return "+".join(ordered) if ordered else "unknown"


def build_variants_from_events() -> list[NotificationVariant]:
  variants: list[NotificationVariant] = []
  for event_id, event_map in EVENTS.items():
    event_name = EVENT_NAME[event_id]
    for et_value in event_map.keys():
      et_name = ET_VALUE_TO_NAME.get(et_value)
      if et_name is None:
        continue
      variants.append(
        NotificationVariant(
          event=event_name,
          event_type=et_name,
          row_index=len(variants) + 1,
        )
      )

  if not variants:
    raise RuntimeError("No notification variants found in EVENTS.")
  return variants


def format_progress(current: int, total: int) -> str:
  width = max(1, len(str(total)))
  return f"{current:>{width}}/{total}"


# ---------------------------------------------------------------------------
# Demo engine
# ---------------------------------------------------------------------------

class NotificationDemo:
  def __init__(self, metric: bool, dwell_seconds: float):
    self.metric = metric
    self.dwell_seconds = dwell_seconds
    self.frame = 0
    self.events = Events()
    self.cp = CarInterface.get_non_essential_params("HONDA_CIVIC")
    self.cs = car.CarState.new_message()
    self.personality = "standard"
    self.soft_disable_time = int(2.0 / DT_CTRL)

    self.sm = FakeSubMaster([
      "deviceState",
      "pandaStates",
      "audioFeedback",
      "roadCameraState",
      "wideRoadCameraState",
      "driverCameraState",
      "modelV2",
      "liveCalibration",
      "liveParameters",
      "driverMonitoringState",
      "longitudinalPlan",
      "livePose",
      "managerState",
      "carControl",
      "alertDebug",
    ])
    self.pm = messaging.PubMaster(["selfdriveState", "deviceState", "pandaStates", "carOutput", "modelV2"])
    self._sim_torque = 0.0
    self._sim_confidence = -0.5
    self._pub_retry_attempts = 8
    self._pub_retry_delay = 0.05

    self._set_base_fake_inputs()

  def _send(self, service: str, msg) -> None:
    for attempt in range(self._pub_retry_attempts):
      try:
        self.pm.send(service, msg)
        return
      except MultiplePublishersError as e:
        if attempt + 1 < self._pub_retry_attempts:
          time.sleep(self._pub_retry_delay)
        else:
          raise RuntimeError(
            f"Publisher collision on '{service}' after {self._pub_retry_attempts} retries. "
            + "Another process is still publishing this topic."
          ) from e

  def _set_base_fake_inputs(self) -> None:
    self.sm["deviceState"].freeSpacePercent = 42.0
    self.sm["deviceState"].memoryUsagePercent = 54
    self.sm["deviceState"].memoryTempC = 54.0
    self.sm["deviceState"].cpuTempC = [52.0, 53.0, 55.0]
    self.sm["deviceState"].gpuTempC = [48.0, 50.0]
    self.sm["deviceState"].cpuUsagePercent = [23] * 8

    self.sm["modelV2"].frameDropPerc = 3.4
    self.sm["modelV2"].velocity.x = [11.1]

    self.sm["liveCalibration"].calStatus = log.LiveCalibrationData.Status.uncalibrated
    self.sm["liveCalibration"].calPerc = 57
    self.sm["liveCalibration"].rpyCalib = [0.0, 0.03, -0.02]

    self.sm["liveParameters"].angleOffsetValid = False
    self.sm["liveParameters"].angleOffsetDeg = 3.8
    self.sm["liveParameters"].steerRatioValid = True
    self.sm["liveParameters"].steerRatio = 15.0
    self.sm["liveParameters"].stiffnessFactorValid = True
    self.sm["liveParameters"].stiffnessFactor = 1.0

    self.sm["carControl"].actuators.accel = 1.0
    self.sm["carControl"].actuators.torque = 0.12

    self.sm["audioFeedback"].blockNum = 0

    self.sm["alertDebug"].alertText1 = "Longitudinal Maneuver Active"
    self.sm["alertDebug"].alertText2 = "Keeping distance from lead vehicle"

    self.sm["livePose"].inputsOK = True
    self.sm["livePose"].posenetOK = True

    procs = [p.get_process_state_msg() for p in managed_processes.values()]
    for p in procs:
      p.shouldBeRunning = True
      p.running = True
    if procs:
      procs[0].running = False
    self.sm["managerState"].processes = procs

    for key in self.sm.data.keys():
      self.sm.alive[key] = True
      self.sm.valid[key] = True
      self.sm.freq_ok[key] = True

  def _apply_variant_inputs(self, variant: NotificationVariant) -> None:
    self._set_base_fake_inputs()

    # Allow callback-based alerts to produce meaningful output.
    if variant.event in {"cameraMalfunction", "commIssue"}:
      self.sm.alive["roadCameraState"] = False
      self.sm.valid["roadCameraState"] = False
      self.sm.freq_ok["roadCameraState"] = False
    if variant.event == "calibrationRecalibrating":
      self.sm["liveCalibration"].calStatus = log.LiveCalibrationData.Status.recalibrating
    if variant.event == "calibrationInvalid":
      self.sm["liveCalibration"].calStatus = log.LiveCalibrationData.Status.invalid
    if variant.event == "paramsdTemporaryError":
      self.sm["liveParameters"].angleOffsetValid = False
    if variant.event == "personalityChanged":
      self.personality = "sport"
    else:
      self.personality = "standard"

  def _make_alert_for_variant(self, variant: NotificationVariant):
    event_enum = getattr(EventName, variant.event, None)
    if event_enum is None:
      return None

    et_value = EVENT_TYPE_TO_ET[variant.event_type]
    self.events.clear()
    self.events.add(event_enum)
    # Many alerts have creation_delay; force this event to be eligible immediately.
    self.events.event_counters[event_enum] = max(self.events.event_counters.get(event_enum, 0), int(60.0 / DT_CTRL))
    alerts = self.events.create_alerts(
      [et_value],
      [self.cp, self.cs, self.sm, self.metric, self.soft_disable_time, self.personality],
    )
    if not alerts:
      return None
    # A single event/type usually yields one alert, but keep highest-priority if multiple.
    return max(alerts, key=lambda a: a.priority)

  def _publish_onroad_state(self) -> None:
    ds = messaging.new_message("deviceState")
    ds.deviceState.started = True
    ds.deviceState.networkType = log.DeviceState.NetworkType.wifi
    self._send("deviceState", ds)

    ps = messaging.new_message("pandaStates", 1)
    ps.pandaStates[0].pandaType = log.PandaState.PandaType.uno
    ps.pandaStates[0].ignitionLine = True
    self._send("pandaStates", ps)

    car_output = messaging.new_message("carOutput")
    # Torque bar uses negated carOutput torque in the non-angle path.
    car_output.carOutput.actuatorsOutput.torque = -float(self._sim_torque)
    self._send("carOutput", car_output)

    model = messaging.new_message("modelV2")
    prob = float(np.clip(1.0 - self._sim_confidence, 0.0, 1.0))
    model.modelV2.meta.disengagePredictions.brakeDisengageProbs = [prob]
    model.modelV2.meta.disengagePredictions.steerOverrideProbs = [prob]
    self._send("modelV2", model)

  def _context_implies_enabled(self, variant: NotificationVariant) -> bool:
    return variant.event_type in {
      "WARNING",
      "SOFT_DISABLE",
      "IMMEDIATE_DISABLE",
      "USER_DISABLE",
      "OVERRIDE_LATERAL",
      "OVERRIDE_LONGITUDINAL",
    }

  def _state_for_variant(self, variant: NotificationVariant, forced_ui_state: str | None = None):
    if forced_ui_state == "UIStatus.OVERRIDE":
      return log.SelfdriveState.OpenpilotState.overriding
    if variant.event_type == "PRE_ENABLE":
      return log.SelfdriveState.OpenpilotState.preEnabled
    if variant.event_type in {"OVERRIDE_LATERAL", "OVERRIDE_LONGITUDINAL"}:
      return log.SelfdriveState.OpenpilotState.overriding
    return None

  def _ui_status_label(self, variant: NotificationVariant, forced_ui_state: str | None = None) -> str:
    return resolve_ui_state_label(variant, forced_ui_state)

  def _enabled_for_variant(self, variant: NotificationVariant, forced_ui_state: str | None = None) -> bool:
    if forced_ui_state == "UIStatus.DISENGAGED":
      return False
    if forced_ui_state in {"UIStatus.ENGAGED", "UIStatus.OVERRIDE"}:
      return True
    return self._context_implies_enabled(variant)

  def _variant_scope_label(self, variant: NotificationVariant) -> str:
    return variant_can_show_in_label(variant)

  def _set_overlay_profile(self, variant: NotificationVariant, alert, forced_ui_state: str | None = None) -> None:
    enabled = self._enabled_for_variant(variant, forced_ui_state)
    self._sim_torque = 0.0
    self._sim_confidence = -0.5 if not enabled else 0.8

    if alert is None or not enabled:
      return

    steer_required = alert.visual_alert == car.CarControl.HUDControl.VisualAlert.steerRequired
    critical = alert.alert_status == log.SelfdriveState.AlertStatus.critical
    user_prompt = alert.alert_status == log.SelfdriveState.AlertStatus.userPrompt

    if variant.event_type in {"OVERRIDE_LATERAL", "OVERRIDE_LONGITUDINAL"}:
      self._sim_torque = 0.75
      self._sim_confidence = 0.35
      return

    if steer_required:
      # "Take control" style alerts: large arc and low confidence.
      self._sim_torque = 0.95 if critical else 0.85
      self._sim_confidence = 0.12 if critical else 0.35
      return

    if user_prompt:
      self._sim_torque = 0.65
      self._sim_confidence = 0.4
      return

    self._sim_torque = 0.25
    self._sim_confidence = 0.8

  def _publish_alert(self, alert, variant: NotificationVariant, forced_ui_state: str | None = None) -> None:
    msg = messaging.new_message("selfdriveState")
    msg.selfdriveState.enabled = self._enabled_for_variant(variant, forced_ui_state)
    state_value = self._state_for_variant(variant, forced_ui_state)
    if state_value is not None:
      msg.selfdriveState.state = state_value
    if alert is not None:
      text1 = alert.alert_text_1
      text2 = alert.alert_text_2

      # Emulate mici-specific alert text behavior while running on non-mici hosts.
      if variant.event_type == "NO_ENTRY" and text2 and text1.strip().lower() == "openpilot unavailable":
        text1, text2 = text2, text1
      if variant.event.startswith("startup") and text2 == "Always keep hands on wheel and eyes on road":
        text2 = ""

      msg.selfdriveState.alertText1 = text1
      msg.selfdriveState.alertText2 = text2
      msg.selfdriveState.alertSize = alert.alert_size
      msg.selfdriveState.alertStatus = alert.alert_status
      msg.selfdriveState.alertType = alert.alert_type
      msg.selfdriveState.alertSound = alert.audible_alert
      msg.selfdriveState.alertHudVisual = alert.visual_alert
    self._send("selfdriveState", msg)

  def _wait_with_checks(self, seconds: float, pause_controller: PauseController, paused: bool, ui_pid: int) -> bool:
    steps = max(1, int(max(0.0, seconds) / DT_CTRL))
    for _ in range(steps):
      if ui_pid > 0:
        try:
          os.kill(ui_pid, 0)
        except OSError:
          raise RuntimeError("UI process closed; stopping notification demo.") from None
      action = pause_controller.poll_action()
      if action == "toggle_pause":
        paused = not paused
        key_label = "space" if pause_controller.pause_key == " " else pause_controller.pause_key
        print(f"  {'Paused' if paused else 'Resumed'} (press '{key_label}' to toggle)")
      self._publish_onroad_state()
      self.frame += 1
      time.sleep(DT_CTRL)
    return paused

  def warmup(self, seconds: float, pause_controller: PauseController, paused: bool, ui_pid: int) -> bool:
    # Warm the UI pipeline before alert #1 is counted.
    return self._wait_with_checks(seconds, pause_controller, paused, ui_pid)

  def show_variant(self, variant: NotificationVariant, pause_controller: PauseController, paused: bool, ui_pid: int,
                   dwell_seconds: float | None = None,
                   on_display_start: Callable[[dict[str, float | bool | str]], None] | None = None,
                   forced_ui_state: str | None = None,
                   ) -> tuple[bool, bool, dict[str, float | bool | str] | None, bool]:
    self._apply_variant_inputs(variant)
    alert = self._make_alert_for_variant(variant)
    if alert is None:
      return False, paused, None, False
    self._set_overlay_profile(variant, alert, forced_ui_state)

    metadata = {
      "source_duration_s": float(alert.duration * DT_CTRL),
      "alert_size_none": bool(alert.alert_size == log.SelfdriveState.AlertSize.none),
      "alert_size_label": ALERT_SIZE_LABELS.get(alert.alert_size, str(alert.alert_size)),
      "ui_state_label": self._ui_status_label(variant, forced_ui_state),
      "state_scope_label": self._variant_scope_label(variant),
    }
    if on_display_start is not None:
      on_display_start(metadata)

    hold_seconds = self.dwell_seconds if dwell_seconds is None else max(0.01, dwell_seconds)
    loops = max(1, int(hold_seconds / DT_CTRL))
    remaining = loops
    while remaining > 0:
      if ui_pid > 0:
        try:
          os.kill(ui_pid, 0)
        except OSError:
          raise RuntimeError("UI process closed; stopping notification demo.") from None
      action = pause_controller.poll_action()
      if action == "toggle_pause":
        paused = not paused
        key_label = "space" if pause_controller.pause_key == " " else pause_controller.pause_key
        print(f"  {'Paused' if paused else 'Resumed'} (press '{key_label}' to toggle)")
      elif action == "skip_family":
        return False, paused, metadata, True
      self._publish_onroad_state()
      self._publish_alert(alert, variant, forced_ui_state)
      self.frame += 1
      time.sleep(DT_CTRL)
      if not paused:
        remaining -= 1
    return True, paused, metadata, False


# ---------------------------------------------------------------------------
# Playback planner
# ---------------------------------------------------------------------------

def playback_states_for_variant(variant: NotificationVariant) -> list[str]:
  ordered = [f"UIStatus.{s}" for s in variant_can_show_in_states(variant)]
  if ordered:
    return ordered
  return [resolve_ui_state_label(variant)]


def sort_variants(variants: list[NotificationVariant]) -> list[NotificationVariant]:
  return sorted(
    variants,
    key=lambda v: (
      v.event,
      UI_STATE_SORT_ORDER.get(resolve_ui_state_label(v), 99),
      EVENT_TYPE_PLAYBACK_ORDER.get(v.event_type, 99),
      v.row_index,
    ),
  )


def assert_playback_planner_invariants(variants: list[NotificationVariant]) -> None:
  for variant in variants:
    states = playback_states_for_variant(variant)
    canonical_states = [f"UIStatus.{s}" for s in variant_can_show_in_states(variant)]
    if canonical_states:
      if states != canonical_states:
        raise AssertionError(f"{variant.alert_id}: playback states drifted from ET mapping")
      continue

    if len(states) != 1 or states[0] not in UI_STATE_SORT_ORDER:
      raise AssertionError(f"{variant.alert_id}: fallback playback state is invalid")

  sorted_variants = sort_variants(variants)
  seen_events: set[str] = set()
  current_event: str | None = None
  for variant in sorted_variants:
    if variant.event != current_event:
      if variant.event in seen_events:
        raise AssertionError(f"{variant.event}: event family is not contiguous")
      seen_events.add(variant.event)
      current_event = variant.event


# ---------------------------------------------------------------------------
# Self-test & validation
# ---------------------------------------------------------------------------

def validate_alert_contract(variant: NotificationVariant, alert) -> list[str]:
  failures: list[str] = []

  if not alert.alert_type:
    failures.append("empty alert_type")
  elif alert.alert_type != variant.alert_id:
    failures.append(f"alert_type mismatch ({alert.alert_type} != {variant.alert_id})")

  if alert.duration <= 0:
    failures.append(f"non-positive duration ({alert.duration})")

  if alert.alert_size not in ALERT_SIZE_LABELS:
    failures.append(f"unknown alert_size ({alert.alert_size})")

  valid_statuses = {
    log.SelfdriveState.AlertStatus.normal,
    log.SelfdriveState.AlertStatus.userPrompt,
    log.SelfdriveState.AlertStatus.critical,
  }
  if alert.alert_status not in valid_statuses:
    failures.append(f"unknown alert_status ({alert.alert_status})")

  return failures


def run_self_test(args: argparse.Namespace) -> int:
  variants = build_variants_from_events()

  print("Self-test results:")
  print(f"- Canonical EVENTS variants: {len(variants)}")

  try:
    assert_playback_planner_invariants(variants)
  except AssertionError as e:
    print(f"- Playback planner checks: FAIL ({e})")
    return 1

  # Smoke-check each variant can build an alert and satisfy a minimal contract.
  demo = NotificationDemo(metric=args.metric, dwell_seconds=args.dwell_seconds)
  failures: list[str] = []
  for variant in sort_variants(variants):
    demo._apply_variant_inputs(variant)
    try:
      alert = demo._make_alert_for_variant(variant)
    except Exception as e:
      failures.append(f"{variant.alert_id}: {e}")
      continue
    if alert is None:
      failures.append(f"{variant.alert_id}: no alert generated")
      continue
    contract_failures = validate_alert_contract(variant, alert)
    failures.extend([f"{variant.alert_id}: {msg}" for msg in contract_failures])

  if failures:
    print(f"- Alert generation contract: FAIL ({len(failures)} failures)")
    for f in failures[:20]:
      print(f"  {f}")
    return 1

  print(f"- Playback planner checks: PASS ({len(variants)} variants)")
  print(f"- Alert generation contract: PASS ({len(variants)} variants)")
  print("Self-test passed.")
  return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run() -> int:
  args = parse_args()
  if args.self_test:
    return run_self_test(args)

  variants = build_variants_from_events()

  assert_playback_planner_invariants(variants)
  variants = sort_variants(variants)
  if args.start_at > 1:
    variants = variants[args.start_at - 1:]
  if args.limit > 0:
    variants = variants[:args.limit]
  if not variants:
    raise RuntimeError("No variants selected after filters.")

  demo = NotificationDemo(metric=args.metric, dwell_seconds=args.dwell_seconds)

  print(f"Loaded {len(variants)} notification variants")
  print("Press 'space' to pause/resume, 's' to skip current alert family")
  timing_warmup = max(0.0, args.warmup_seconds)
  timing_first = max(0.01, args.first_dwell_seconds)
  timing_default = max(0.01, args.dwell_seconds)
  print(f"Timing: warmup={timing_warmup:.2f}s, first_alert={timing_first:.2f}s, default={timing_default:.2f}s")
  print("Playback grouping: event families (all variants contiguous)")
  family_order = list(dict.fromkeys(v.event for v in variants))
  family_count = len(family_order)
  family_index_by_event = {event: idx for idx, event in enumerate(family_order, start=1)}
  print(f"Event families: {family_count}")
  print("")

  playback_plan: list[tuple[NotificationVariant, str]] = []
  for variant in variants:
    for ui_state_label in playback_states_for_variant(variant):
      playback_plan.append((variant, ui_state_label))

  total = len(playback_plan)
  event_counts = Counter(v.event for v in variants)
  event_variant_ids: dict[str, list[str]] = {}
  for v in variants:
    event_variant_ids.setdefault(v.event, []).append(event_type_slug(v.event_type))
  event_play_counts = Counter(v.event for v, _ in playback_plan)
  last_event = ""
  shown = 0
  skipped = 0
  paused = False
  skip_family_event: str | None = None
  progress_width = len(format_progress(total, total))
  details_width = len(
    "AlertSize.small | duration: 10.00s | can_show_in:DISENGAGED+ENGAGED | current:UIStatus.DISENGAGED"
  )

  with PauseController(PAUSE_KEY, SKIP_FAMILY_KEY) as pause_controller:
    if args.warmup_seconds > 0:
      print(f"\nWarming up UI for {args.warmup_seconds:.2f}s before alert #1...")
      paused = demo.warmup(args.warmup_seconds, pause_controller, paused, args.ui_pid)

    for idx, (variant, playback_state) in enumerate(playback_plan, start=1):
      if args.ui_pid > 0:
        try:
          os.kill(args.ui_pid, 0)
        except OSError:
          print("\nUI process closed; stopping notification demo.")
          return 0

      if skip_family_event == variant.event:
        skipped += 1
        continue

      if variant.event != last_event:
        skip_family_event = None
        if idx > 1:
          family_pause_steps = max(1, int(max(0.0, args.section_pause_seconds) / DT_CTRL))
          for _ in range(family_pause_steps):
            if args.ui_pid > 0:
              try:
                os.kill(args.ui_pid, 0)
              except OSError:
                print("\nUI process closed; stopping notification demo.")
                return 0
            action = pause_controller.poll_action()
            if action == "toggle_pause":
              paused = not paused
              key_label = "space" if pause_controller.pause_key == " " else pause_controller.pause_key
              print(f"  {'Paused' if paused else 'Resumed'} (press '{key_label}' to toggle)")
            elif action == "skip_family":
              skip_family_event = variant.event
              family_idx = family_index_by_event.get(variant.event, 0)
              print(f"  Skipping {family_idx}/{family_count}: {variant.event}")
              break
            time.sleep(DT_CTRL)
          if skip_family_event == variant.event:
            skipped += 1
            continue
        total_count = event_counts[variant.event]
        play_count = event_play_counts[variant.event]
        play_note = f", {play_count} plays" if play_count != total_count else ""
        family_idx = family_index_by_event.get(variant.event, 0)
        print(f"\n-- {family_idx}/{family_count} {variant.event} ({total_count} variants{play_note}) --")
        print(f"   variants: {', '.join(event_variant_ids.get(variant.event, []))}")
        last_event = variant.event

      hold = args.first_dwell_seconds if idx == 1 else args.dwell_seconds
      progress = format_progress(idx, total)
      line_printed = {"done": False}

      def on_display_start(
        alert_meta: dict[str, float | bool | str],
        progress: str = progress,
        variant_alert_id: str = variant.alert_id,
        line_printed: dict[str, bool] = line_printed,
      ) -> None:
        size_label = str(alert_meta["alert_size_label"])
        current_label = str(alert_meta["ui_state_label"])
        can_show_in_label = str(alert_meta["state_scope_label"])
        details = " | ".join([
          f"{size_label:<{ALERT_SIZE_LABEL_WIDTH}}",
          f"duration: {alert_meta['source_duration_s']:.2f}s",
          f"can_show_in:{can_show_in_label:<{CAN_SHOW_IN_WIDTH}}",
          f"current:{current_label:<{UI_STATE_LABEL_WIDTH}}",
        ])
        print(f"{progress:<{progress_width}} | {details:<{details_width}} | {variant_alert_id}")
        line_printed["done"] = True

      ok, paused, _, skip_requested = demo.show_variant(
        variant,
        pause_controller,
        paused,
        args.ui_pid,
        dwell_seconds=hold,
        on_display_start=on_display_start,
        forced_ui_state=playback_state,
      )
      if skip_requested:
        skip_family_event = variant.event
        skipped += 1
        family_idx = family_index_by_event.get(variant.event, 0)
        print(f"  Skipping {family_idx}/{family_count}: {variant.event}")
        continue
      if ok:
        shown += 1
      else:
        skipped += 1

      if not line_printed["done"]:
        print(f"{progress:<{progress_width}} | {'skipped':<{details_width}} | {variant.alert_id}")

  print("\nRun complete.")
  print(f"Shown: {shown}")
  print(f"Skipped: {skipped}")
  return 0


if __name__ == "__main__":
  raise SystemExit(run())
