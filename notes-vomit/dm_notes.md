# DM Bar Deep Dive: Driver Monitoring Awareness on Tici

The DM bar is the driver-monitoring segment inside `DisengageBars` on the tici on-road UI. It replaced the old tici on-road DMoji as the primary always-visible driver-monitoring indicator in the road view.

If another engineer wants to implement something similar on their fork, the most important thing to understand is that this bar does **not** visualize raw head pose or face direction. It visualizes the driver-monitoring system's single accumulated awareness value, exposed as `driverMonitoringState.awarenessStatus`, then inverts that value so the bar rises as distraction increases.

---

## What The DM Bar Is

On tici, the DM bar is the rightmost bar in the reactive section of `DisengageBars`:

- Predictive group: `C`, `B`, `G`, `S`, `S2`
- Reactive group: `BI`, `SA`, `DM`

The implementation describes it this way:

```374:381:selfdrive/ui/onroad/disengage_bars.py
      DM – driver distraction level (1 - driverMonitoringState.awarenessStatus)
           label changes to P (pose) / E (eyes/blink) / Ph (phone) when distracted

  C uses modelV2.confidence (fill_model_msg.py), which is distinct from the continuous ball.
  B, G, S, and the confidence ball share the same NN meta head source.
  BI fills upward as the car brakes -- directly reacts to measured deceleration.
  SA mirrors the mici TorqueBar signal but shows absolute utilization (0=none, 1=limit).
  DM fills upward as the driver becomes more distracted; empty = fully attentive.
```

In plain English:

- Empty bar = driver is fully attentive
- Taller bar = awareness is being depleted
- Full bar = awareness is at or below terminal threshold
- Label can change to show what kind of distraction is active

This is a very different UI concept from the old DMoji:

- DMoji showed head direction and active/inactive DM mode
- DM bar shows accumulated distraction severity
- DMoji did not show awareness countdown
- DM bar does not show face direction

---

## Platform Split: Tici vs Mici

This distinction is easy to miss when porting code:

- `tici` on-road view uses `DisengageBars`, which includes the DM bar
- `mici` on-road view still uses the separate `DriverStateRenderer` / DMoji approach

That means a fork cannot copy "the DM UI" from just one file unless the target is specifically the tici bar implementation.

Useful references:

- `selfdrive/ui/onroad/disengage_bars.py` - DM bar logic and drawing
- `selfdrive/ui/onroad/augmented_road_view.py` - where the widget is attached to the road view
- `selfdrive/ui/ui_state.py` - where the UI subscribes to `driverMonitoringState`
- `selfdrive/monitoring/helpers.py` - where `awarenessStatus`, `distractedType`, and `isRHD` are produced
- `selfdrive/ui/onroad/DMOJI_DEEP_DIVE.md` - broader background on DMoji vs DM bar

---

## Where It Lives In The UI

The road view owns a `DisengageBars` instance and renders it after the main camera content, HUD, alerts, and border:

```51:55:selfdrive/ui/onroad/augmented_road_view.py
    self.model_renderer = ModelRenderer()
    self._hud_renderer = HudRenderer()
    self.alert_renderer = AlertRenderer()
    self._disengage_bars = DisengageBars()
```

```108:112:selfdrive/ui/onroad/augmented_road_view.py
    # Draw colored border based on driving state
    self._draw_border(rect)

    # Render disengage bars after scissor and border so they always paint on top
    self._disengage_bars.render(self._content_rect)
```

That render order matters:

- The widget is intentionally on top of the road view
- It is not clipped away with the camera scissor region
- It preserves readability even when other overlays are present

---

## Data Flow End To End

The full signal path is:

1. `driverStateV2` provides raw driver-camera model output
2. `DriverStateMachine` in `selfdrive/monitoring/helpers.py` updates DM state
3. The DM state machine publishes `driverMonitoringState`
4. `UIState` subscribes to `driverMonitoringState`
5. `DisengageBars._update_state()` reads `awarenessStatus`, `distractedType`, and `isRHD`
6. `DisengageBars._render()` converts the filtered value into a segmented vertical bar

The UI subscription happens here:

```35:58:selfdrive/ui/ui_state.py
    self.sm = messaging.SubMaster(
      [
        "modelV2",
        "controlsState",
        "onroadEvents",
        "liveCalibration",
        "radarState",
        "deviceState",
        "pandaStates",
        "carParams",
        "driverMonitoringState",
        "carState",
        "driverStateV2",
        "roadCameraState",
        "wideRoadCameraState",
        "managerState",
        "selfdriveState",
        "longitudinalPlan",
        "gpsLocationExternal",
        "carOutput",
        "carControl",
        "liveParameters",
        "rawAudioData",
      ]
    )
```

The message gets built here:

```400:419:selfdrive/monitoring/helpers.py
  def get_state_packet(self, valid=True):
    # build driverMonitoringState packet
    dat = messaging.new_message('driverMonitoringState', valid=valid)
    dat.driverMonitoringState = {
      "events": self.current_events.to_msg(),
      "faceDetected": self.face_detected,
      "isDistracted": self.driver_distracted,
      "distractedType": sum(self.distracted_types),
      "awarenessStatus": self.awareness,
      "stepChange": self.step_change,
      "awarenessActive": self.awareness_active,
      "awarenessPassive": self.awareness_passive,
      "isActiveMode": self.active_monitoring_mode,
      "isRHD": self.wheel_on_right,
      "uncertainCount": self.dcam_uncertain_cnt,
    }
```

If someone is recreating the DM bar outside this exact UI stack, this message is the cleanest abstraction boundary to reuse.

---

## The Core Signal: `awarenessStatus`

The entire DM bar is built around one scalar:

- `awarenessStatus = 1.0` means fully attentive
- `awarenessStatus = 0.0` means terminal red threshold
- `awarenessStatus < 0.0` means force-decel region

This scalar is the compressed output of the full driver-monitoring pipeline. It already includes:

- face detection
- pose offset calibration
- blink detection
- phone detection
- model uncertainty fallback
- active vs passive monitoring mode
- standstill and always-on policy behavior
- recovery and depletion timing

The bar is therefore not measuring "current glance angle." It is showing "how close the DM system thinks the driver is to a distraction intervention."

---

## How The UI Converts Awareness Into A Bar

The conversion is intentionally simple:

```514:517:selfdrive/ui/onroad/disengage_bars.py
      # DM: invert awarenessStatus so bar fills UP as distraction increases.
      # awareness=1.0 (attentive) → 0.0 on bar. awareness=0.0 (terminal) → 1.0 on bar.
      awareness = sm['driverMonitoringState'].awarenessStatus
      self._dm_filter.update(1.0 - max(min(awareness, 1.0), 0.0))
```

Important details:

- The value is clamped to `[0.0, 1.0]`
- Then it is inverted with `1.0 - awareness`
- Then it is smoothed by a `FirstOrderFilter`

Behavior table:

| `awarenessStatus` | Clamped | Bar value | Meaning |
|-------------------|---------|-----------|---------|
| `1.0` | `1.0` | `0.0` | Fully attentive, empty bar |
| `0.72` | `0.72` | `0.28` | Mild DM depletion |
| `0.54` | `0.54` | `0.46` | Pre-alert region starting |
| `0.0` | `0.0` | `1.0` | Terminal, full bar |
| `-0.1` | `0.0` | `1.0` | Force-decel region, still full bar |

The clamp is an easy thing to overlook. If awareness goes negative, the UI does **not** show anything "more than full." It just saturates at full height.

---

## Why The Bar Is Filtered Again

The DM signal is already time-integrated by the monitoring state machine, but the UI still applies a second visual filter:

```397:400:selfdrive/ui/onroad/disengage_bars.py
    # SA: matches mici TorqueBar RC -- torque utilization is a physical signal
    self._torque_utilization_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    # DM: match B/S smoothness -- awarenessStatus is already time-integrated
    self._dm_filter = FirstOrderFilter(0.0, 0.5, 1 / gui_app.target_fps)
```

This second filter is there for UI feel, not system correctness:

- it reduces frame-to-frame jitter
- it makes the bar visually match the other LED bars
- it creates a smoother transition when awareness changes quickly

If another fork copies the DM concept but uses a different visual style, this filter can be tuned independently of the DM daemon itself.

---

## Label Behavior: `DM`, `P`, `E`, `Ph`

The bar label is dynamic:

```519:528:selfdrive/ui/onroad/disengage_bars.py
  def _dm_label(self) -> str:
    """Return distraction type label when active, else 'DM'."""
    dt = self._dm_distracted_type
    if dt & 4:
      return "Ph"
    if dt & 2:
      return "E"
    if dt & 1:
      return "P"
    return "DM"
```

Meaning:

- `DM` - no specific active distraction bit
- `P` - pose distraction
- `E` - eyes / blink distraction
- `Ph` - phone distraction

Important implementation nuance:

- `distractedType` is a bitmask, not an enum
- the UI prioritizes phone over eyes over pose
- if multiple bits are set, only one label is shown

That means the label is a lossy summary of the active distraction causes, not a full diagnostic view.

---

## Positioning And RHD Behavior

The DM bar is not positioned independently. The whole `DisengageBars` widget is mirrored left/right based on `isRHD`:

```535:540:selfdrive/ui/onroad/disengage_bars.py
    # Mirror DMoji position anchor: bottom-left (LHD) or bottom-right (RHD).
    # Anchor on the horizontal center of the widget so both edges stay on-screen.
    h_offset = UI_BORDER_SIZE + WIDTH // 2
    v_offset = UI_BORDER_SIZE + HEIGHT // 2
    cx = rect.x + (rect.width - h_offset if self._is_rhd else h_offset)
    cy = rect.y + rect.height - v_offset
```

What `isRHD` does:

- moves the entire widget from bottom-left to bottom-right
- mirrors the old tici DMoji anchor location

What `isRHD` does **not** do:

- it does not affect awareness math
- it does not change label logic
- it does not change fill amount

This matters when porting. If a fork only wants the DM bar and not the full widget, the RHD dependency may be unnecessary unless mirrored placement is still desired.

---

## Visibility Rules

The widget only appears when the car is started and `modelV2` has arrived after start:

```411:414:selfdrive/ui/onroad/disengage_bars.py
    self.set_visible(lambda: (
      ui_state.started and
      ui_state.sm.recv_frame['modelV2'] > ui_state.started_frame
    ))
```

Also, every widget render goes through `_update_state()` first:

```98:112:openpilot/system/ui/widgets/__init__.py
  def render(self, rect: rl.Rectangle | None = None) -> bool | int | None:
    if rect is not None:
      self.set_rect(rect)

    self._update_state()

    if self._click_release_time is not None and rl.get_time() >= self._click_release_time:
      self._click_release_time = None

    if not self.is_visible:
      return None

    self._layout()
    ret = self._render(self._rect)
```

So the DM bar is effectively updated every frame, but only when the enclosing widget is considered visible.

---

## Disengaged Behavior

One of the biggest implementation choices is what happens while openpilot is disengaged:

```426:435:selfdrive/ui/onroad/disengage_bars.py
    if ui_state.status == UIStatus.DISENGAGED:
      self._confidence_filter.update(-0.5)
      self._model_confidence_scaled = 0.0
      self._brake_filter.update(0.0)
      self._steer_filter.update(0.0)
      self._gas_filter.update(0.0)
      self._accel_filter.update(0.0)
      self._torque_utilization_filter.update(0.0)
      self._dm_filter.update(0.0)
```

This means:

- the DM bar is visually cleared when the UI status is disengaged
- it does not continue to show raw `awarenessStatus`
- the widget behaves like an "on-road openpilot state" panel, not a full passive DM dashboard

This is a policy decision, not a technical requirement. A fork can choose differently if it wants DM state visible even while disengaged.

---

## Where `awarenessStatus` Comes From

The DM daemon computes awareness in `DriverStateMachine`. The awareness value is depleted while the driver is distracted and replenished when the driver is attentive again:

```347:356:selfdrive/monitoring/helpers.py
    driver_attentive = self.driver_distraction_filter.x < 0.37
    awareness_prev = self.awareness

    if (driver_attentive and self.face_detected and self.pose.low_std and self.awareness > 0):
      if driver_engaged:
        self._reset_awareness()
        return
      # only restore awareness when paying attention and alert is not red
      self.awareness = min(self.awareness + ((self.settings._RECOVERY_FACTOR_MAX-self.settings._RECOVERY_FACTOR_MIN)*
                                             (1.-self.awareness)+self.settings._RECOVERY_FACTOR_MIN)*self.step_change, 1.)
```

```372:376:selfdrive/monitoring/helpers.py
    if certainly_distracted or maybe_distracted:
      # should always be counting if distracted unless at standstill (lowspeed for always-on) and reaching orange
      # also will not be reaching 0 if DM is active when not engaged
      if not (standstill_orange_exemption or always_on_red_exemption or (always_on_lowspeed_exemption and _reaching_audible)):
        self.awareness = max(self.awareness - self.step_change, -0.1)
```

That is why the DM bar behaves more like a battery or countdown than like a raw attention detector.

---

## Why The Bar Can Disagree With Alerts

This is the single most important behavior to call out to another engineer.

The DM bar is tied to `awarenessStatus`, but alert emission is controlled by additional policy logic:

```363:390:selfdrive/monitoring/helpers.py
    _reaching_audible = self.awareness - self.step_change <= self.threshold_prompt
    _reaching_terminal = self.awareness - self.step_change <= 0
    standstill_orange_exemption = standstill and _reaching_audible
    always_on_red_exemption = always_on_valid and not op_engaged and _reaching_terminal
    always_on_lowspeed_exemption = always_on_valid and not op_engaged and car_speed < self.settings._ALWAYS_ON_ALERT_MIN_SPEED

    certainly_distracted = self.driver_distraction_filter.x > 0.63 and self.driver_distracted and self.face_detected
    maybe_distracted = self.hi_stds > self.settings._HI_STD_FALLBACK_TIME or not self.face_detected

    if certainly_distracted or maybe_distracted:
      if not (standstill_orange_exemption or always_on_red_exemption or (always_on_lowspeed_exemption and _reaching_audible)):
        self.awareness = max(self.awareness - self.step_change, -0.1)

    alert = None
    if self.awareness <= 0.:
      alert = EventName.driverDistracted if self.active_monitoring_mode else EventName.driverUnresponsive
    elif self.awareness <= self.threshold_prompt:
      alert = EventName.promptDriverDistracted if self.active_monitoring_mode else EventName.promptDriverUnresponsive
    elif self.awareness <= self.threshold_pre and not always_on_lowspeed_exemption:
      alert = EventName.preDriverDistracted if self.active_monitoring_mode else EventName.preDriverUnresponsive
```

Implications:

- a low or rising DM bar does not always mean an alert is currently visible
- standstill behavior can suppress alert progression
- always-on mode can change red / orange behavior
- low-speed exemptions can affect banner behavior differently than the raw bar

If a fork implements a DM bar and expects it to match alert banners exactly, it will look wrong in edge cases.

---

## Another Non-Obvious Input: Model Brake Probability Affects DM Policy

The bar itself only directly consumes `driverMonitoringState`, but the DM daemon uses road-model output to tune DM strictness:

```439:443:selfdrive/monitoring/helpers.py
      brake_disengage_prob = sm['modelV2'].meta.disengagePredictions.brakeDisengageProbs[0] # brake disengage prob in next 2s
    self._set_policy(
      brake_disengage_prob=brake_disengage_prob,
      car_speed=highway_speed,
    )
```

This matters because the driver-monitoring pipeline is not purely inward-camera-only in behavior. The final `awarenessStatus` still depends mostly on driver camera inputs, but policy thresholds can loosen or tighten based on the model's expected need for human takeover.

For a fork, that means there are two possible implementation levels:

1. Reuse `driverMonitoringState` as-is and just draw it
2. Rebuild the full DM state machine and policy coupling

The first option is much easier and much safer.

---

## Practical Porting Guidance

If another engineer wants "something similar," the simplest robust implementation is:

1. Subscribe to `driverMonitoringState`
2. Read `awarenessStatus`
3. Convert it with `1.0 - clamp(awarenessStatus, 0.0, 1.0)`
4. Smooth it with a short UI-only filter
5. Optionally read `distractedType` for a compact label
6. Decide explicitly whether disengaged mode should clear the visual

Minimal pseudocode:

```python
awareness = sm["driverMonitoringState"].awarenessStatus
bar_value = 1.0 - max(min(awareness, 1.0), 0.0)
bar_value = visual_filter.update(bar_value)

dt = sm["driverMonitoringState"].distractedType
if dt & 4:
  label = "Ph"
elif dt & 2:
  label = "E"
elif dt & 1:
  label = "P"
else:
  label = "DM"
```

If the goal is fidelity to upstream behavior, also decide the following up front:

- Should the bar clear when disengaged?
- Should negative awareness be visually distinct, or just saturate full?
- Should the label show one cause or multiple causes?
- Should the bar follow `isRHD` for placement?
- Should the implementation mirror alert policy, or only underlying awareness?

---

## Common Mistakes To Avoid

### 1. Treating It Like A Pose Indicator

It is not a gaze or head-direction widget. If a fork wants that behavior, it needs DMoji-style rendering, not the DM bar.

### 2. Forgetting The Inversion

`awarenessStatus` goes down as distraction increases. The bar goes up. Without the inversion, the UI communicates the opposite meaning.

### 3. Forgetting The Clamp

Negative awareness exists. Without clamping, a custom fill formula can produce out-of-range rendering or "overfull" bars.

### 4. Assuming Label Equals Full Cause Set

`distractedType` is a bitmask, and the UI collapses it to a single short label. A debug view may want more detail than this compact implementation gives.

### 5. Assuming The Bar Must Match Alert Banners

It often will, but not always. The DM bar is closer to internal DM state than to final user-facing alert policy.

### 6. Copying Placement Without Understanding Context

The current implementation mirrors the old DMoji anchor and lives inside a larger multi-signal widget. A standalone DM bar on another fork may need completely different placement rules.

---

## Implementation Checklist

For an engineer rebuilding this on another fork, the minimum checklist is:

- Subscribe to `driverMonitoringState`
- Use `awarenessStatus` as the primary fill input
- Invert and clamp the value before rendering
- Add a short UI smoothing filter
- Read `distractedType` if a short categorical label is desired
- Decide whether disengaged mode should zero the display
- Decide whether placement should mirror by `isRHD`
- Test standstill, attentive recovery, and terminal distraction cases
- Validate that the visual meaning is documented as "awareness depletion," not "where the driver is looking"

---

## File Reference

| File | Role |
|------|------|
| `selfdrive/ui/onroad/disengage_bars.py` | DM bar rendering, filtering, label logic, placement |
| `selfdrive/ui/onroad/augmented_road_view.py` | Attaches `DisengageBars` to the road view |
| `selfdrive/ui/ui_state.py` | UI subscription to `driverMonitoringState` |
| `selfdrive/monitoring/helpers.py` | Source of `awarenessStatus`, `distractedType`, `isRHD` |
| `selfdrive/ui/onroad/DMOJI_DEEP_DIVE.md` | Background on why DM bar replaced tici on-road DMoji |
| `selfdrive/ui/onroad/DISENGAGE_BARS_RESEARCH.md` | Broader signal design rationale for the widget |

---

## Summary

The tici DM bar is a compact UI for one thing: visualizing the DM system's accumulated awareness state. Its apparent simplicity hides several important design decisions:

- it uses a single scalar, not raw head pose
- it inverts the signal so distraction fills upward
- it clamps negative awareness to full scale
- it smooths the display for visual stability
- it uses a compact, lossy label derived from a distraction bitmask
- it can disagree with alerts because alert policy adds more logic than the raw bar shows
- it is embedded in a larger widget whose visibility, placement, and disengaged behavior are all deliberate choices

If another fork wants a similar feature, the best reuse point is `driverMonitoringState`, not the full DM model pipeline.
