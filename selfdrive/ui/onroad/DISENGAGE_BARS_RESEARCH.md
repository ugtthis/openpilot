# Disengage Bars: Research & Signal Analysis

POC widget replacing the DMoji in the tici onroad view with segmented LED-style bars. Goal: give drivers real-time, decomposed visibility into what openpilot's models are predicting and what the car is doing, instead of a single abstracted confidence indicator.

## Problem Statement

The mici confidence ball combines brake-disengage and steer-override probabilities into a single number:

```python
confidence = (1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))
```

This loses two things: (1) which signal is driving the change (brake vs steer), and (2) any forward-looking physical awareness of braking need. The POC splits these apart and adds a third bar for braking intent.

## Current Implementation

Three bars: `B | BI | S`

| Bar | Label | Source | Question it answers |
|-----|-------|--------|---------------------|
| B | BRAKE | `modelV2.meta.disengagePredictions.brakeDisengageProbs` | "How likely is the driver to brake-override openpilot?" |
| BI | BI | `carState.aEgo` (measured deceleration) | "How hard is the car braking right now?" |
| S | STEER | `modelV2.meta.disengagePredictions.steerOverrideProbs` | "How likely is the driver to grab the steering wheel?" |

**Key limitation with BI**: `carState.aEgo` is a lagging indicator -- it only shows braking that is *already happening*, not braking that *may be needed*. The original goal was forward-looking: "a brake event may be needed."

## The BI Bar Problem: What We Tried and Why It Failed

### Attempt 1: `longitudinalPlan.accels[0]`
The longitudinal planner's commanded acceleration. Failed because it's only populated when openpilot has longitudinal control (`openpilotLongitudinalControl`). On cars where openpilot only handles steering, this is empty/zero.

### Attempt 2: `carState.aEgo`
Measured vehicle acceleration from wheel speed sensors. Always populated, always reflects real physics. But it's the **present/past**, not the **future**. When approaching a stopped car that openpilot handles correctly, the bar only rises once braking begins -- it can't warn ahead of time.

## Available Signals for Forward-Looking Braking Need

### Signal Group 1: Radar-Based (Physics)

Source: `radarState.leadOne` (fused radar + vision lead detection)

| Field | Type | Description |
|-------|------|-------------|
| `dRel` | float | Distance to lead vehicle (m) |
| `vRel` | float | Relative velocity (m/s, negative = closing) |
| `aRel` | float | Relative acceleration (m/s²) |
| `status` | bool | Whether a lead is detected |

**Derivable metrics:**

- **Time to Collision (TTC)**: `dRel / -vRel` when `vRel < 0`. Universally intuitive. 10s = comfortable, 5s = notable, 2s = urgent. Undefined when not closing.
- **Required deceleration**: `vRel² / (2 * dRel)`. How hard you'd need to brake right now to avoid collision. Directly physical. 0.5 m/s² = gentle, 2 m/s² = moderate, 4+ m/s² = emergency.

**Pros**: Instant reaction to closing speed changes, no model dependency, simple physics.
**Cons**: Zero signal when no radar target (clear road, stop signs, road geometry).

### Signal Group 2: Neural Network Acceleration Plan (Model Intent)

Source: `modelV2.acceleration.x`

33 values sampled over the next 10 seconds (non-uniform quadratic spacing via `ModelConstants.T_IDXS`). Already used by the path renderer for experimental mode coloring.

| Index | Approx time | Use |
|-------|-------------|-----|
| `x[0]` | 0s (now) | Model's planned acceleration right now |
| `x[5]` | ~0.25s | Very near-term intent |
| `x[15]` | ~2.2s | Medium-term |
| `x[32]` | 10s | Far look-ahead |

**Key property**: This is the neural net's own acceleration output from camera analysis. It incorporates everything the model sees: lane geometry, lead cars, traffic signals, road curvature, construction. Negative values = model expects/wants deceleration.

**Pros**: Camera-based, sees non-lead threats (stop signs, curves, slow traffic without radar lock), same data that colors the path visualization, forward-looking.
**Cons**: Depends on model accuracy, slightly less reactive than raw radar physics for sudden cut-ins.

### Signal Group 3: Model Disengagement Predictions (Already on B Bar)

Source: `modelV2.meta.disengagePredictions.brakeDisengageProbs`

5 cumulative probabilities at `t = [2, 4, 6, 8, 10]` seconds. Trained on real-world driver brake-override events. Predicts driver behavior, not physical need.

Additional related signal: `meta.hardBrakePredicted` (bool) -- fires when the model consistently predicts hard braking (>3 m/s² and >5 m/s²) across multiple frames. Used for Forward Collision Warning (FCW). Very high signal when it triggers, but binary.

### Signal Group 4: Longitudinal Planner Output

Source: `longitudinalPlan`

| Field | Type | Description |
|-------|------|-------------|
| `aTarget` | float | Single target acceleration |
| `accels` | float[] | Planned acceleration trajectory (~2.5s) |
| `speeds` | float[] | Planned speed trajectory |
| `shouldStop` | bool | Whether planner wants a full stop |
| `allowBrake` | bool | Whether braking is permitted |

**Critical limitation**: Only meaningful when `openpilotLongitudinalControl == true`. On steering-only cars, these are default/zero. Cannot be the sole source for a universal bar.

### Signal Group 5: Vehicle State (Present-Tense)

Source: `carState`

| Field | Type | Description |
|-------|------|-------------|
| `aEgo` | float | Measured vehicle acceleration (m/s², negative = braking) |
| `vEgo` | float | Vehicle speed (m/s) |
| `brakePressed` | bool | Driver foot on brake |
| `gasPressed` | bool | Driver foot on gas |

Useful for confirming braking is happening but not for predicting it.

## Recommended BI Bar Strategy

### Option A: Model Acceleration (Camera-Based)

```python
model_accel = modelV2.acceleration.x[0]
scaled = min(max(-model_accel, 0.0) / MAX_DECEL, 1.0)
```

Best for: general awareness, handles all road scenarios, same signal the path renderer uses.

### Option B: Radar Required Deceleration (Physics-Based)

```python
lead = radarState.leadOne
if lead.status and lead.vRel < 0:
    required_decel = lead.vRel ** 2 / (2 * lead.dRel)
else:
    required_decel = 0
scaled = min(required_decel / MAX_DECEL, 1.0)
```

Best for: direct, physical, immediate reaction to closing lead vehicles.

### Option C: Combined (Recommended)

```python
model_decel = max(-modelV2.acceleration.x[0], 0.0)

lead = radarState.leadOne
if lead.status and lead.vRel < 0:
    radar_decel = lead.vRel ** 2 / (2 * lead.dRel)
else:
    radar_decel = 0

combined = max(model_decel, radar_decel)
scaled = min(combined / MAX_DECEL, 1.0)
```

Radar catches sudden cut-ins instantly. Model covers stop signs, curves, and everything without a radar target. `max()` ensures the bar always shows the more urgent signal.

## Tuning Parameters

| Parameter | Current | Effect |
|-----------|---------|--------|
| `PROB_SENSITIVITY_CEILING` | 0.4 | B/S bars saturate at 40% raw probability |
| `MAX_DECEL` | 3.5 m/s² | BI bar saturates at ~0.35g |
| `FirstOrderFilter RC (B/S)` | 0.5s | Smoothing for probability signals |
| `FirstOrderFilter RC (BI)` | 0.15s | Smoothing for physical decel signal |
| `BLOCK_COUNT` | 5 | Number of discrete levels per bar |

## Neural Network Meta Head Detail

The driving model outputs a `meta` tensor (88 floats) sliced as:

```
Index layout (repeating pattern of 6 values × 5 time horizons):
[0]      : engagedProb
[1,7,13,19,25]  : gasDisengageProbs     at t=[2,4,6,8,10]s
[2,8,14,20,26]  : brakeDisengageProbs   at t=[2,4,6,8,10]s
[3,9,15,21,27]  : steerOverrideProbs    at t=[2,4,6,8,10]s
[4,10,16,22,28] : hardBrake3Probs       at t=[2,4,6,8,10]s
[5,11,17,23,29] : hardBrake4Probs       at t=[2,4,6,8,10]s
[6,12,18,24,30] : hardBrake5Probs       at t=[2,4,6,8,10]s
[31-54]  : gasPress, brakePress, leftBlinker, rightBlinker at t=[0,2,4,6,8,10]s
```

All disengage probs are **cumulative** (P(by t=10s) >= P(by t=2s)). Using `max()` always yields the 10-second value. For a tighter window, index directly: `[0]` = 2s, `[1]` = 4s, etc.

These predictions are trained on real-world driver interventions -- the model learned from millions of miles of data when human drivers actually pressed the brake or grabbed the wheel. They measure **driver distrust/discomfort**, not physical threat level.

## Separate Confidence System (Not Used by Bars)

The `modelV2.confidence` field (green/yellow/red enum) uses a more sophisticated rolling-buffer diagonal-score method over 5 snapshots. It combines all three disengage types and checks whether past predictions about the current moment were accurate. Thresholds: green < 0.01165, yellow < 0.06157, red >= 0.06157.

This system is computed in `fill_model_msg.py` but neither the confidence ball nor the disengage bars currently use it.

---

## Reference: Confidence Ball (Mici UI)

**File**: `selfdrive/ui/mici/onroad/confidence_ball.py`

The confidence ball is the direct predecessor to this POC. Understanding it precisely is essential context.

### What it communicates

A single gradient circle on the right-side panel (60px wide, outside the camera scissor region). It answers: **"how worried should the driver be that openpilot is about to struggle?"** It is entirely a behavioral prediction signal -- it says nothing about the physical state of the road or car.

### Data source

```python
# confidence_ball.py lines 41-42
self._confidence_filter.update(
    (1 - max(ui_state.sm['modelV2'].meta.disengagePredictions.brakeDisengageProbs or [1])) *
    (1 - max(ui_state.sm['modelV2'].meta.disengagePredictions.steerOverrideProbs or [1]))
)
```

`max()` always resolves to the t=10s value since the probs are cumulative. The multiplication treats the two events as independent -- if either has high probability, confidence drops fast.

### Smoothing

```python
self._confidence_filter = FirstOrderFilter(-0.5, 0.5, 1 / gui_app.target_fps)
```

RC = 0.5s. Initialized at -0.5 so on engagement the ball animates up from off-screen. On disengage it's driven back to -0.5 so it slides downward out of view.

### Vertical position

```python
dot_height = (1 - self._confidence_filter.x) * (content_rect.height - 2 * radius) + radius
```

The filter value ranges from -0.5 (off bottom) to 1.0 (top of panel). High confidence = ball near the top. Low confidence = ball near the bottom. The vertical position and color both encode the same underlying value.

### Color zones

| Filter value | Color | Meaning |
|-------------|-------|---------|
| > 0.5 | Cyan `(0,255,204)` → Green `(0,255,38)` | Low disengagement risk, model confident |
| 0.2 – 0.5 | Yellow `(255,200,0)` → Orange `(255,115,0)` | Moderate risk, pay attention |
| < 0.2 | Red `(255,0,21)` → Dark Red `(255,0,89)` | High risk, situation is challenging |
| Override state | White → Gray | Driver is actively overriding, neutral display |
| Disengaged | Dark Gray → Near Black | System not engaged, ball animates off-screen |

### What makes it go low (red / bottom)

The model's meta head was trained on real driver interventions. The ball drops when the camera sees patterns historically associated with drivers taking over: ambiguous lane markings, tight curves, construction zones, slow merging traffic, poor lighting. Critically -- it drops based on **visual scene complexity**, not physical urgency. A car could be 2s from a collision and the ball may stay green if openpilot is handling it correctly.

### Rendering position

Rendered after `end_scissor_mode()` in `augmented_road_view.py`, meaning it always paints on top of everything including alerts. Located in the 60px `SIDE_PANEL_WIDTH` strip on the right of the mici screen.

### What it does NOT show

- It does not show current steering or braking effort
- It does not react to the car's physical deceleration
- It does not separate brake vs steer signals (they are multiplied together)
- It is the same value regardless of which threat type is dominant

---

## Reference: Torque Bar (Mici UI)

**File**: `selfdrive/ui/mici/onroad/torque_bar.py`  
**Rendered by**: `selfdrive/ui/mici/onroad/hud_renderer.py`

### What it communicates

A curved arc at the bottom center of the camera view showing **how hard openpilot is steering right now**. Unlike the confidence ball (future/predictive) and the B/S bars (behavioral prediction), the torque bar is a **present-tense actuation signal** -- it shows the physical output of the lateral control loop.

### Geometry

The bar is a thick arc segment of a circle with radius 1200px, centered far below the screen. This makes it appear as a subtle curve at the screen's bottom edge. The arc spans `TORQUE_ANGLE_SPAN = 12.7` degrees centered at the top of that circle (-90°, pointing straight up). It has two layers:

- **Background track**: faint white arc spanning the full 12.7° -- always visible as the "range"
- **Active fill**: sweeps left or right from center, proportional to current torque

### Two control modes with different data sources

The torque bar switches data source depending on the car's lateral control type:

**Torque-based control** (majority of cars):
```python
# torque_bar.py line 179
self._torque_filter.update(-ui_state.sm['carOutput'].actuatorsOutput.torque)
```
Direct normalized torque command to the EPS (Electric Power Steering). Range -1 to +1. Sign indicates direction. This is what actually went to the car's steering motor.

**Angle-based control** (some Toyota/Lexus platforms):
```python
# torque_bar.py lines 168-177
lateral_acceleration = (controls_state.curvature * car_state.vEgo ** 2
                        - live_parameters.roll * ACCELERATION_DUE_TO_GRAVITY)
actual_lateral_accel = controls_state.curvature * car_state.vEgo ** 2
desired_lateral_accel = controls_state.desiredCurvature * car_state.vEgo ** 2
accel_diff = desired_lateral_accel - actual_lateral_accel

self._torque_filter.update(
    min(max(lateral_acceleration / max_lateral_acceleration + accel_diff, -1), 1)
)
```

This computes lateral acceleration from `curvature * v²`, subtracts road roll (so banking doesn't appear as a steering command), then adds an error correction term for how far actual curvature deviates from desired. Normalized by `max_lateral_acceleration = 3 m/s²`.

**On angle-control cars the torque bar is hidden entirely** (hud_renderer.py line 175-176):
```python
if ui_state.sm['controlsState'].lateralControlState.which() != 'angleState':
    self._torque_bar.render(rect)
```

### Visual behavior

| Condition | Visual change |
|-----------|--------------|
| Torque < 0.5 | Center gray dot visible, bar stays close to center |
| Torque 0.5-0.75 | Bar fills, height 14-56px, white color |
| Torque 0.75-1.0 | Color fades white → yellow → orange (urgency signal) |
| Not engaged | Dims to 15% alpha, animates out |
| Override | Dims to 35% alpha |

The height of the active fill also grows with torque magnitude (14px → 56px), giving two simultaneous cues: angular spread and thickness.

### Color gradient during high torque

```python
# torque_bar.py lines 226-234
start_color = blend_colors(
    rl.Color(255, 255, 255, ...),   # white (normal)
    rl.Color(255, 200,   0, ...),   # yellow (high torque)
    max(0, abs(torque) - 0.75) * 4
)
end_color = blend_colors(
    rl.Color(255, 255, 255, ...),
    rl.Color(255, 115,   0, ...),   # orange (high torque)
    max(0, abs(torque) - 0.75) * 4
)
```

The transition from white to yellow/orange begins at 75% of max torque and completes at 100%. This gives a clear visual alarm when the controller is working near its limits.

### What it does NOT show

- It does not predict future steering effort
- It shows direction of steering but not speed of change
- It does not react to model confidence or road conditions
- It does not show driver steering input (only openpilot's command)

### Relationship to the disengage bars

The torque bar and the confidence ball/disengage bars occupy completely different layers of the stack:

```
Camera → Neural Network → Lateral Planner → Lateral Controller → EPS Torque Command
              ↑                                                          ↑
    confidence ball / B / S bars                                   torque bar
    (what the model predicts about the future)          (what was actually sent to the car)
```

They can diverge significantly:

| Scenario | Torque bar | Confidence ball / B bar |
|----------|-----------|------------------------|
| Clean highway curve | Deflects (steering effort) | Stays green (model is confident) |
| Tight curve at limit | Deflects hard, turns orange | May also drop (model uncertain) |
| Ambiguous lane markings on straight road | Stays centered | Drops (model sees complex scene) |
| Construction zone, straight | Minimal deflection | Drops (model predicts driver distrust) |
| Lane change | Deflects during maneuver | B bar may stay calm |

The torque bar tells you **what the car is doing to steer**. The B/S bars tell you **what the model predicts the driver will do**. The BI bar (goal state) should tell you **what physical braking situation is developing**.

---

## Reference: Driver Monitoring (DM) System

Driver Monitoring is a completely separate pipeline from the road model. It uses the **inward-facing IR driver camera** and outputs an awareness score and distraction classification.

### Architecture

```
Driver camera (IR fisheye, VISION_STREAM_DRIVER)
  → dmonitoringmodeld   (TinyGrad neural net, 20 Hz)
        inputs:  camera frame + liveCalibration.rpyCalib
        outputs: driverStateV2
  → dmonitoringd        (policy/state machine, 20 Hz)
        inputs:  driverStateV2, carState, selfdriveState,
                 modelV2.meta.disengagePredictions.brakeDisengageProbs[0]  ← road model feeds here
        outputs: driverMonitoringState
  → UI (DriverStateRenderer / DisengageBars)
  → selfdrived / controlsd (events, forceDecel)
```

### What the DM Neural Net Outputs (`driverStateV2`)

The model always outputs **two parallel driver data sets simultaneously** — one for LHD and one for RHD — and a `wheelOnRightProb` to pick which to use.

Each `DriverData` struct contains:

| Field | Type | Description |
|-------|------|-------------|
| `faceOrientation` | float[3] | `[pitch, yaw, roll]` in radians (head pose) |
| `faceOrientationStd` | float[3] | Uncertainty for each axis — `> 0.3` = unreliable |
| `facePosition` | float[2] | Normalized `[x, y]` image position of face center |
| `faceProb` | float | P(face visible). DM gates on this: needs `> 0.7` |
| `leftEyeProb` | float | P(left eye open). Blink only checked if `> 0.65` |
| `rightEyeProb` | float | P(right eye open) |
| `leftBlinkProb` | float | P(left eye blinking) |
| `rightBlinkProb` | float | P(right eye blinking) |
| `sunglassesProb` | float | P(sunglasses). If `> 0.9`, blink check is skipped entirely |
| `phoneProb` | float | P(driver looking at phone). Threshold: `> 0.5` |

### What the DM Policy Outputs (`driverMonitoringState`)

| Field | Type | Description |
|-------|------|-------------|
| `awarenessStatus` | float | `1.0` = fully aware, `0.0` = red alert, `< 0` = forced decel |
| `isDistracted` | bool | Smoothed distraction state |
| `distractedType` | uint32 | Bitmask: `POSE=1`, `BLINK=2`, `PHONE=4` |
| `faceDetected` | bool | `faceProb > 0.7` |
| `isActiveMode` | bool | `true` = camera-tracked, `false` = wheel-touch only |
| `isRHD` | bool | Right-hand drive — mirrors DMoji and selects which `DriverData` to use |
| `posePitchOffset` | float | Learned "natural looking forward" pitch for this driver |
| `poseYawOffset` | float | Learned "natural looking forward" yaw for this driver |
| `isLowStd` | bool | Model is confident (`std < 0.3`). False = falls back to passive mode |
| `stepChange` | float | Per-frame awareness drain/refill rate |
| `awarenessActive` | float | Awareness in active (camera) mode |
| `awarenessPassive` | float | Awareness in passive (wheel-touch) mode |

### Distraction Classification

Three independent sources:

**POSE (`distractedType & 1`)** — head pitch too far down OR yaw too far sideways, relative to the driver's learned natural offset. Key asymmetry: **looking up is never penalized**, only down or sideways. Thresholds:
- `_POSE_PITCH_THRESHOLD = 0.3133 rad (~18°)` below natural
- `_POSE_YAW_THRESHOLD = 0.4020 rad (~23°)` from center

**BLINK (`distractedType & 2`)** — average of left and right `blinkProb > 0.865`. Only checked if eyes are visible (`eyeProb > 0.65`) and no sunglasses (`sunglassesProb < 0.9`).

**PHONE (`distractedType & 4`)** — `phoneProb > 0.5`. Separate from pose — you can be looking at the right angle but still be flagged for phone use.

Final distraction requires: at least one type AND `faceProb > 0.7` AND `isLowStd`. Then filtered through `FirstOrderFilter(ts=0.25s)` to avoid flickering.

### Awareness State Machine

```
awarenessStatus:   1.0  ──── 0.72 ──── 0.54 ──── 0.0 ──── < 0
                   OK    pre-alert  prompt    red      forceDecel
                          green     orange   full      (car brakes)
```

**Active mode** (face tracked, `isLowStd=true`): 11s total timeout
- Pre-alert at 8s remaining → `preDriverDistracted` (green banner)
- Prompt at 6s remaining → `promptDriverDistracted` (orange alert)
- Terminal → `driverDistracted` + red fullscreen

**Passive mode** (face lost or uncertain): 30s total timeout
- Same cascade but longer: pre at 15s, prompt at 6s

Awareness **recovers** when driver pays attention (at up to 5× faster than it drains). Resets to `1.0` on disengage or physical driver input (steering/gas press).

After 3 terminal alerts OR 30s cumulative at terminal → `DriverTooDistracted` param set → blocks re-engagement entirely.

### How the Road Model Feeds Into DM (`_set_policy`)

The `brakeDisengageProbs[0]` (2-second window) from `modelV2` directly adjusts DM's pose thresholds:

```python
k1 = max(-0.00156 * ((car_speed - 16)**2) + 0.6, 0.2)  # peaks at 16 m/s (~36 mph)
bp_normal = max(min(brake_disengage_prob / k1, 0.5), 0)
# bp_normal=0 → use slack thresholds (lenient)
# bp_normal=0.5 → use strict thresholds (tight)
```

When the road model predicts the driver is likely to brake (approaching a stop), DM **loosens** its pose thresholds to avoid false distraction alerts during an intentional takeover. When the road looks calm, DM is **stricter**. This makes the two completely separate cameras and models work cooperatively.

### Pose Calibration (Driver Personalization)

DM **learns each driver's natural "looking forward" head position** using a running stat filter:
- Accumulates data only while: `vEgo > 13 m/s`, face detected, not distracted, model confident
- After ~1–6 minutes of calibration, replaces generic offsets with driver-specific ones
- Persisted across drives via params (`IsLdwEnabled` context)
- Published as `posePitchOffset` and `poseYawOffset` in `driverMonitoringState`

This is why DM doesn't false-alarm for drivers who naturally sit slightly tilted — it learns their baseline.

### RHD Detection (`isRHD`)

The DM model always outputs two parallel predictions (LHD + RHD). `wheelOnRightProb` accumulates over 15s of highway driving before committing. Once decided:
- Selects `rightDriverData` vs `leftDriverData` in `dmonitoringd`
- Mirrors the DMoji position in the UI (bottom-right instead of bottom-left)
- Detection is **locked during engagement** to prevent mid-drive flips
- Persisted to `IsRhdDetected` param every 5 minutes

### Connection to the DisengageBars

The `isRHD` field is the only `driverMonitoringState` field currently used by the DisengageBars widget — it controls whether the bar panel anchors to the bottom-left (LHD) or bottom-right (RHD) of the camera view, mirroring where the driver is seated.

The `awarenessStatus` and `distractedType` fields are the obvious candidates for a future "A" (awareness) bar, showing the drain/recovery cycle visually. The `distractedType` bitmask would let you show separate sub-indicators for pose vs blink vs phone.

### What the DMoji Was Rendering (Replaced by DisengageBars)

The `DriverStateRenderer` (now replaced) rendered:
- A 192px semi-transparent circle button in the bottom corner
- A static driver face icon (`icons/driver_face.png`, 65% opacity when active)
- A **live 3D face outline**: 33 keypoints projected from model space using the full `faceOrientation` rotation matrix, drawn as a spline — the outline actually rotated to track the driver's head in real time
- Pitch arc (vertical) and yaw arc (horizontal) indicating head rotation direction
- Arc color: green when engaged, gray when disengaged
- Arc thickness grew with rapid head movement (`driver_pose_diff`)

This was real-time head pose visualization. The DisengageBars replace the widget slot but currently show prediction bars, not head tracking.

---

## File Locations

| File | Purpose |
|------|---------|
| `selfdrive/ui/onroad/disengage_bars.py` | Bar widget implementation |
| `selfdrive/ui/onroad/augmented_road_view.py` | Tici onroad view (renders bars) |
| `selfdrive/ui/mici/onroad/confidence_ball.py` | Mici confidence ball (reference) |
| `selfdrive/modeld/fill_model_msg.py` | Model output → cereal message packing |
| `selfdrive/modeld/constants.py` | Meta tensor slice definitions, time indices |
| `selfdrive/ui/onroad/model_renderer.py` | Path/lane rendering (uses acceleration.x for coloring) |
| `selfdrive/ui/ui_state.py` | UIState singleton, SubMaster subscriptions |
