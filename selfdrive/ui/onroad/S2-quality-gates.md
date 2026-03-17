# S2 Bar: Quality Gates and Implementation Specification

The S2 bar is a new vertical bar placed to the right of S in the prediction group, combining `steerOverrideProbs` (S — behavioral prediction) and steering actuator utilization (SA — physical reality) into a single **"should the driver be ready to steer?"** signal. Goal: give the earliest possible warning before a steering situation requires driver override, while suppressing the signal when it would be misleading.

This document covers everything needed to implement S2: what gates are applied, why, how the combination works, and the UI specification.

---

## 1. S2 Bar Purpose

**One question it answers**: "Is something developing that may require me to grab the wheel?"

S and SA each answer this from different vantage points:
- **S** (`steerOverrideProbs[0]` or `max()`): "The camera sees a scene where drivers historically grabbed the wheel"
- **SA** (`actuatorsOutput.torque` or lateral accel): "The car is physically working hard to steer right now"

Neither alone is sufficient:
- SA is blind to visual scene complexity and can't see what's coming
- S is 5× slower to respond (RC=0.5s, 20Hz) vs SA (RC=0.1s, 100Hz) and can't see hardware stress

S2 = `max(S_gated, SA_gated)` — whichever fires first, at whatever level is highest. This mirrors the established BI Option C pattern (`max(model_decel, radar_decel)`) already in the same widget.

---

## 2. Widget Layout Context

Current layout (from `disengage_bars.py`):

```
[●]  [C | B | G | S]   [BI | SA | DM]
ball  prediction group   reactive group
      ← 54% of bars →   ← 46% of bars →
```

S2 goes into the prediction group:

```
[●]  [C | B | G | S | S2]   [BI | SA | DM]
```

**Width math with current `WIDTH = 646`:**

```
bar_area_w = 646 - 2*24 - 60 - 16 = 522 px
sections_total_w = 522 - 34 = 488 px
predict_section_w = int(488 * 0.54) = 263 px

5 bars: predict_bar_w = (263 - 4*12) // 5 = (263 - 48) // 5 = 43 px
4 bars: predict_bar_w = (263 - 3*12) // 4 = (263 - 36) // 4 = 56 px
```

Adding S2 reduces prediction bar width from 56px to 43px — workable but tight. Options:

| Option | Change | Result |
|--------|--------|--------|
| A | `WIDTH = 646` (no change) | 43px bars in prediction group |
| B | `WIDTH = 700` | `predict_bar_w ≈ 51px` — closer to current |
| C | `PREDICT_SECTION_RATIO = 0.60` | Gives prediction group more of the fixed width |

**Recommended**: Option B — bump `WIDTH` to `700`. The comment on line 34 says "bars shrink slightly to fit 7; increase WIDTH if more space is needed." We now have 8 bars.

---

## 3. Suppression Gates (When to Silence S2 or a Component)

These gates zero out a signal when the data is invalid, meaningless, or would create a false positive. They are applied before the `max()` combination.

### Gate 1: DISENGAGED state

```python
if ui_state.status == UIStatus.DISENGAGED:
    s2_filter.update(0.0)
    return
```

**Applies to**: both S and SA components, therefore S2 = 0  
**Why**: Same as every other bar — no lateral engagement, no relevant signal  
**Code reference**: `disengage_bars.py:386-398` (same block that zeros all other bars)

---

### Gate 2: `latActive = False`

```python
if not sm['carControl'].latActive:
    sa_component = 0.0
```

**Applies to**: SA component only  
**Why**: If the lateral controller is off, the torque output is not openpilot steering — it is zero or noise. Showing it would imply the car is working toward a steering limit that doesn't apply.

`latActive` becomes false when (`controlsd.py:95-97`):
- `not selfdriveState.active` — system not enabled
- `CS.steerFaultTemporary` — EPS temporary fault
- `CS.steerFaultPermanent` — EPS permanent fault
- `standstill and not CP.steerAtStandstill` — stopped, and car doesn't support steering at standstill

**Note**: This gate already exists for angle-state cars in the current SA bar code (`disengage_bars.py:450-451`). For S2, it should apply universally — both torque and angle cars.

**Impact on S component**: S remains active even when `latActive=False` because the model's prediction of driver behavior can still be meaningful. However, it's worth noting that `steerOverrideProbs` may be poorly calibrated in low-speed standstill scenarios. Consider also zeroing S when `latActive=False AND vEgo < 2 m/s`.

---

### Gate 3: Speed threshold for SA saturation

```python
# _check_saturation() in latcontrol.py:22-29
vEgo > sat_check_min_speed   # 5 m/s for angle, 10 m/s for torque/PID
```

**Applies to**: SA's top block (saturation indicator) only  
**Why**: At low speed, steering effort is high but the physics are different — tight parking maneuvers produce maximum torque that isn't meaningful as a "saturation warning" in the safety sense  
**Implementation**: The `lat_saturated` boolean already encodes this gate — the saturation timer only accumulates above the speed threshold. No additional code needed in S2.

For the **continuous SA utilization** (blocks 1-9), consider a softer speed gate: scale SA contribution by `np.interp(vEgo, [0, 5], [0.0, 1.0])` so that at very low speed the bar fades out rather than jumping.

---

### Gate 4: `steeringPressed` — driver already overriding

```python
# _check_saturation() in latcontrol.py:24
not CS.steeringPressed
```

**Applies to**: SA saturation block (top block)  
**Why**: If the driver is touching the wheel, the saturation timer stops accumulating. The driver is already solving the problem S2 would be warning about.

**Design question for S component**: Should `steeringPressed` suppress S too? Options:
- **Suppress S**: The driver is already intervening — warning is moot. Reduces clutter.
- **Keep S active**: The override may be brief. S2 showing what the model was predicting (and is still predicting) provides context.
- **Recommended**: Keep S active, suppress SA saturation (mirrors `_check_saturation` behavior exactly).

---

### Gate 5: `steer_limited_by_safety` — panda clipping

```python
# controlsd.py:172-175
steer_limited_by_safety = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2
```

**Applies to**: SA saturation block only  
**Why**: When the panda safety chip clips the torque command, the car's output doesn't reflect what the controller requested. Counting this as "saturation" would be misleading — the controller may not be at its own limit, it's just being overridden by hardware safety.  
**Note**: `steer_limited_by_safety` is already gated inside `_check_saturation`. No additional code in S2. But this is why the top block (saturation) behaves differently from the continuous torque signal.

---

### Gate 6: `steerFault` states

Cascades through Gate 2 (both cause `latActive=False`). But worth calling out separately:
- `steerFaultTemporary`: temporary EPS issue — SA=0, S remains active (model still predicts based on scene)
- `steerFaultPermanent`: permanent EPS fault — consider also dimming S (the system cannot steer at all, so the warning is misleading)

---

### Gate 7: Standstill (non-`steerAtStandstill` cars)

Cascades through Gate 2 for most cars. Cars that DO support `steerAtStandstill` (Tesla, Nissan, Ford, PSA) have `latActive=True` at a stop, so S2 stays active for them.

---

### Gate 8: `CP.notCar` — simulator

```python
if ui_state.CP and ui_state.CP.notCar:
    # Optionally suppress S2 entirely in simulator
```

**Optional** — the simulator may have meaningless steering signals. The existing `steerSaturated` alert already gates on `not self.CP.notCar` (`selfdrived.py:367`).

---

### Gate Summary: What S2 Receives

```
S_component  = steerOverrideProbs  ×  bool(not DISENGAGED)  ×  bool(latActive OR vEgo > 2)
SA_component = torque_util         ×  bool(latActive)        ×  speed_ramp(vEgo)
S2_raw       = max(S_component, SA_component)
```

---

## 4. Contextual Gates (When to Amplify or Modify S2)

These are signals that modify S2's sensitivity based on what is actually happening on the road. All are available in `ui_state.sm` with no new subscriptions needed.

### Context Group A: Road Geometry

**On a curve** — `abs(controlsState.curvature) * vEgo² > threshold_lateral_accel`

Lateral accel = `curvature × vEgo²`. At 10 m/s on a 0.02 rad/m curve: `0.02 × 100 = 2.0 m/s²`.

```python
actual_lat_accel = abs(sm['controlsState'].curvature * sm['carState'].vEgo**2)
curve_factor = np.interp(actual_lat_accel, [0.5, 2.0], [1.0, 1.3])
# S2 = S2_raw * curve_factor
```

**Why**: Steering stress on a curve is more relevant as a warning — the car is already committed to a path that requires continuous torque. SA being high on a straight road (crosswind) is different from SA being high on a curve (geometry demands it).

**Alternative**: Use `carState.steeringAngleDeg` as a simpler proxy. LKW-style threshold: `abs(steeringAngleDeg) > 5°`.

---

**Lane confidence low** — `min(laneLineProbs[1], laneLineProbs[2]) < 0.5`

LDW uses `0.5` as the visibility threshold (`ldw.py:25-26`). The mici model renderer uses `0.25` as "low" for color changes.

```python
left_prob = sm['modelV2'].laneLineProbs[1]
right_prob = sm['modelV2'].laneLineProbs[2]
lane_confidence = min(left_prob, right_prob)
lane_factor = np.interp(lane_confidence, [0.25, 0.5], [1.2, 1.0])
# S2 = S2_raw * lane_factor (amplify when lines unclear)
```

**Why**: When the model can't see lane lines, its steer-override prediction is less certain but also less "safe" — the car may genuinely be operating near the road edge without good geometry.

---

**Road edge uncertainty high** — `max(roadEdgeStds[0], roadEdgeStds[1]) > threshold`

```python
edge_uncertainty = max(sm['modelV2'].roadEdgeStds[0], sm['modelV2'].roadEdgeStds[1])
edge_factor = np.interp(edge_uncertainty, [0.3, 0.8], [1.0, 1.2])
```

**Why**: High `roadEdgeStds` = model doesn't know where the road boundary is. Combined with any SA signal, this is more urgent.

---

### Context Group B: Nearby Vehicles

**Blind spot occupied** — `carState.leftBlindspot or carState.rightBlindspot`

```python
blindspot_active = sm['carState'].leftBlindspot or sm['carState'].rightBlindspot
blindspot_amplifier = 1.25 if blindspot_active else 1.0
```

**Why**: During a lane change or curve, if a BSM radar detects an adjacent vehicle, a steering excursion is more dangerous. This is already used in `desire_helper.py` to block lane changes entirely. For S2, it amplifies the warning when the stakes are higher.

**Gate vs Amplifier**: This is contextual, not suppressive — the signal is still meaningful, just more urgent. Use amplification.

---

**Lead lateral offset** — `abs(radarState.leadOne.yRel) > 1.0`

`yRel` is the lateral offset of the lead vehicle. Non-zero `yRel` suggests the lead is in an adjacent lane or cutting across the path.

```python
lead = sm['radarState'].leadOne
if lead.status and abs(lead.yRel) > 1.0:
    cut_in_risk = np.interp(abs(lead.yRel), [1.0, 3.0], [1.1, 1.0])
    # slightly amplify — cut-in could require evasive steering
```

**Why**: A lead with high lateral offset is either a cut-in candidate or a car in the adjacent lane. Either scenario increases the relevance of steering readiness.

---

**Close lead distance** — `radarState.leadOne.dRel < 15m and leadOne.status`

```python
if lead.status and lead.dRel < 15.0 and lead.modelProb > 0.5:
    proximity_factor = np.interp(lead.dRel, [5, 15], [1.2, 1.0])
```

**Why**: Very close following leaves less room for lateral error. A steering event at close range is more critical.

---

### Context Group C: Lane Change State

**Lane change active** — `modelV2.meta.laneChangeState`

```python
from cereal import log
LCS = log.ModelDataV2.Meta.LaneChangeState
lcs = sm['modelV2'].meta.laneChangeState
```

| State | S2 behavior | Rationale |
|-------|-------------|-----------|
| `off` | Normal | Standard prediction |
| `preLaneChange` | Suppress SA component | Driver intends to steer — SA torque is driver-commanded, not a warning |
| `laneChangeStarting` | Suppress both | Active lane change — driver is steering intentionally |
| `laneChangeFinishing` | Restore gradually | Returning to normal |

```python
if lcs == LCS.laneChangeStarting:
    s2_value = 0.0  # suppress entirely during active lane change
elif lcs == LCS.preLaneChange:
    s2_value = S_component  # only model prediction, not physical torque
```

**Alternative view — amplify instead of suppress**: A lane change is a high-risk maneuver. Blindspot + lane change could be a scenario where S2 should be higher, not lower. The right call depends on the use case:
- If S2 is a "passive heads-up" bar: suppress during intentional maneuvers
- If S2 is a "is the system struggling" bar: keep active, since lane changes are when saturation is more common

**Recommended**: Suppress during `laneChangeStarting` since the driver is committed to the maneuver and no additional alert is useful. Keep during `preLaneChange` so the driver can see if the system is already stressed before committing.

---

### Context Group D: Model State

**Model confidence** — `modelV2.confidence`

```python
conf = sm['modelV2'].confidence
confidence_factor = {
    ConfidenceClass.green:  1.0,   # model has been accurate recently
    ConfidenceClass.yellow: 1.1,   # slightly amplify — model accuracy degrading
    ConfidenceClass.red:    1.2,   # model has been wrong recently — be more cautious
}[conf]
```

**Why**: If the model's rolling prediction accuracy is poor (C bar is red), its `steerOverrideProbs` may be less calibrated. However, this could cut either way — sometimes a red C bar means the model is predicting well but things are happening, not that it's predicting badly. Use with caution.

---

**Trajectory uncertainty** — `modelV2.acceleration.xStd[0]`

```python
accel_std = sm['modelV2'].acceleration.xStd[0] if len(sm['modelV2'].acceleration.xStd) > 0 else 0.0
# High std = model is uncertain about its own plan
uncertainty_gate = accel_std < 0.5   # suppress S component contribution if very uncertain
```

**Why**: If the model has high uncertainty about its acceleration plan, its `steerOverrideProbs` may also be unreliable. Rather than amplifying, consider gating S when `xStd` is very high. This is the analogue of DM's `isLowStd` gate for pose.

---

### Context Group E: Driver State

**Driver distracted** — `driverMonitoringState.awarenessStatus`

A high S2 warning is more urgent when the driver is not paying attention.

```python
awareness = sm['driverMonitoringState'].awarenessStatus
# Combined urgency: steering warning × driver inattention
# This is a cross-pipeline combination like DM policy ← brakeDisengageProbs
if awareness < 0.545:   # DM "prompt" threshold
    urgency_factor = 1.3
elif awareness < 0.727: # DM "pre-alert" threshold
    urgency_factor = 1.15
else:
    urgency_factor = 1.0
```

**Why**: The steerSaturated alert already gates on `recent_steer_pressed` to avoid false alarms. The analogous contextual gate for S2 is: "if the driver isn't watching, the warning matters more." This mirrors DM policy's use of `brakeDisengageProbs[0]` to adjust distraction thresholds.

**Important caveat**: This creates a compound signal across two pipelines. The DM system already issues alerts when `awarenessStatus` drops. Adding another visual signal on S2 may duplicate. Consider only using this to change color, not the bar height.

---

**Driver fighting the controller** — `carState.steeringTorque` direction vs model curvature

```python
car_torque = sm['carState'].steeringTorque      # driver torque on column
desired_curv = sm['controlsState'].desiredCurvature
# If driver torque is in opposite direction to desired curvature, they're fighting
fighting = (car_torque * desired_curv < -0.05)  # opposite signs
```

**Why**: If the driver is actively applying torque counter to the model's desired curvature, this is a "soft override" that may escalate to `steeringPressed`. It's an earlier signal than `steeringPressed` itself. Use as amplifier rather than gate.

---

### Context Group F: Environment

**Low light / night** — `wideRoadCameraState.exposureValPercent`

`ui_state.light_sensor = max(100 - exposureValPercent, 0)` — high value means brighter scene.

```python
exposure = sm['wideRoadCameraState'].exposureValPercent
low_light = exposure < 20   # very low light
light_factor = 1.15 if low_light else 1.0
```

**Why**: At night or in low-visibility conditions, model predictions are less reliable. S2 from the SA component remains valid (hardware), but S from the model may be less calibrated.

---

**Calibration state** — `liveCalibration.calStatus`

```python
from cereal import log
CalStatus = log.LiveLocationKalman.Status
cal_ok = sm['liveCalibration'].calStatus == CalStatus.calibrated
s_scaling = 1.0 if cal_ok else 0.7   # reduce S contribution when uncalibrated
```

**Why**: `model_renderer.py` returns early when calibration hasn't been received. S2's model-based S component should be similarly discounted when calibration is incomplete.

---

### Context Group G: Speed-Dependent Sensitivity

The DM system uses a speed-dependent parabola to adjust pose thresholds (`k1 = max(-0.00156*(speed-16)² + 0.6, 0.2)`, peaking at 16 m/s). The roll compensation in both the SA bar and the torque bar uses `np.interp(vEgo, [5, 15], [0.0, 1.0])`.

For S2, a simpler approach:

```python
vEgo = sm['carState'].vEgo
# Highway: S2 more meaningful (higher speeds = less margin for error)
# City / parking: S2 less meaningful (SA is noisy at low speed)
speed_scale = np.interp(vEgo, [2.0, 10.0, 30.0], [0.3, 0.8, 1.0])
```

| Speed | `speed_scale` | Interpretation |
|-------|-------------|----------------|
| < 2 m/s (standstill) | 0.3 | Almost no S2 signal — parking is expected to max out steering |
| 5 m/s (city) | ~0.65 | Moderate S2 |
| 15 m/s (suburban) | ~0.93 | Near-full S2 |
| 30+ m/s (highway) | 1.0 | Full sensitivity |

---

## 5. Which S Horizon to Use

The S component of S2 can draw from `steerOverrideProbs` at different time horizons.

**`max()` = always the 10s value** (current S bar behavior — inherited from confidence ball without independent decision):
- Pros: wider awareness, first to start rising in a slow-developing situation
- Cons: constant low-level noise on mildly complex roads; never spikes hard for imminent events

**`steerOverrideProbs[0]` = 2s horizon** (what DM uses for its policy gate):
- Pros: much quieter in normal driving; spikes sharply when something is genuinely imminent
- Cons: less early-warning characteristic; may feel like it only fires "too late"

**Recommendation for S2**: Use `steerOverrideProbs[0]` (2s). The rationale:
1. SA already provides the early-warning early-ramp signal (responds in ~0.1s)
2. S in S2 should add sharp near-term spikes for visually-driven urgency, not ambient scene complexity
3. The ambient scene complexity role is already covered by the existing S bar
4. DM's independent decision to use `[0]` for its actionable gate is the strongest precedent

```python
# S component for S2
s_raw = sm['modelV2'].meta.disengagePredictions.steerOverrideProbs[0]  # 2s horizon
s_norm = min(s_raw / PROB_SENSITIVITY_CEILING, 1.0)
```

---

## 6. Full Computation — Pseudocode

```python
def _compute_s2(sm, ui_state) -> float:
    """Returns S2 value in [0, 1] for filter input."""

    # ── Suppression gates ──────────────────────────────────────────────────
    if ui_state.status == UIStatus.DISENGAGED:
        return 0.0

    lat_state = sm['controlsState'].lateralControlState
    lat_which = lat_state.which()
    lat_controller = getattr(lat_state, lat_which, None)
    lat_saturated = getattr(lat_controller, 'saturated', False)
    lat_active = sm['carControl'].latActive
    vEgo = sm['carState'].vEgo

    # ── S component (behavioral prediction, 2s horizon) ────────────────────
    probs = sm['modelV2'].meta.disengagePredictions.steerOverrideProbs
    s_raw = probs[0] if probs else 0.0                 # 2s horizon
    s_norm = min(s_raw / PROB_SENSITIVITY_CEILING, 1.0)

    # Suppress S when uncalibrated
    if sm['liveCalibration'].calStatus != CalibratedStatus:
        s_norm *= 0.7

    # ── SA component (physical utilization) ────────────────────────────────
    if not lat_active:
        sa_norm = 0.0
    elif lat_which == 'angleState':
        cs = sm['controlsState']
        lp = sm['liveParameters']
        actual_la = cs.curvature * vEgo**2
        desired_la = cs.desiredCurvature * vEgo**2
        roll_comp = lp.roll * ACCELERATION_DUE_TO_GRAVITY * np.interp(vEgo, [5, 15], [0.0, 1.0])
        lateral_accel = actual_la - roll_comp
        max_la = ui_state.CP.maxLateralAccel if ui_state.CP else DEFAULT_MAX_LAT_ACCEL
        torque_util = float(np.clip(abs(lateral_accel + (desired_la - actual_la)) / max_la, 0.0, 1.0))
        sa_norm = 1.0 if lat_saturated else min(torque_util, (SA_BLOCK_COUNT-1)/SA_BLOCK_COUNT)
    else:
        torque_util = abs(sm['carOutput'].actuatorsOutput.torque)
        sa_norm = 1.0 if lat_saturated else min(torque_util, (SA_BLOCK_COUNT-1)/SA_BLOCK_COUNT)

    # Speed gate: fade SA at very low speed
    speed_scale = np.interp(vEgo, [2.0, 10.0, 30.0], [0.3, 0.8, 1.0])
    sa_norm *= speed_scale

    # ── Combine ────────────────────────────────────────────────────────────
    s2_raw = max(s_norm, sa_norm)

    # ── Contextual amplification ────────────────────────────────────────────
    # Lane change: suppress during active maneuver
    lcs = sm['modelV2'].meta.laneChangeState
    if lcs == LCS.laneChangeStarting:
        s2_raw = 0.0
    elif lcs == LCS.preLaneChange:
        s2_raw = s_norm  # model only, no physical torque confusion

    # Curve: amplify when laterally loaded
    actual_lat_accel = abs(sm['controlsState'].curvature * vEgo**2)
    curve_factor = np.interp(actual_lat_accel, [0.5, 2.5], [1.0, 1.3])
    s2_raw = min(s2_raw * curve_factor, 1.0)

    # Blind spot: amplify when adjacent vehicle present
    if sm['carState'].leftBlindspot or sm['carState'].rightBlindspot:
        s2_raw = min(s2_raw * 1.2, 1.0)

    # Lane confidence: amplify when lines unclear
    lp = sm['modelV2'].laneLineProbs
    if len(lp) >= 3:
        lane_conf = min(lp[1], lp[2])
        lane_factor = np.interp(lane_conf, [0.25, 0.5], [1.2, 1.0])
        s2_raw = min(s2_raw * lane_factor, 1.0)

    return s2_raw
```

**Which gates to implement first** (in priority order):
1. DISENGAGED — mandatory, same as all bars
2. `latActive` SA zero — prevents misleading signal, low cost
3. Speed scaling — prevents parking noise
4. Lane change suppression — prevents driver-action false positives
5. Curve amplification — highest signal value, defensible with physics
6. Blind spot amplification — clear safety case
7. Lane confidence — good signal, needs threshold tuning
8. Distracted driver, model confidence — complex cross-pipeline, test carefully

---

## 7. UI Specification

### Bar placement

```
predict_bars = [
    (confidence_scaled,    "C",  CONF_BLOCK_COUNT, ...),
    (brake_norm,           "B",  BLOCK_COUNT,      ...),
    (gas_norm,             "G",  BLOCK_COUNT,      ...),
    (steer_norm,           "S",  BLOCK_COUNT,      ...),
    (s2_filter.x,          "S2", S2_BLOCK_COUNT,   S2_BLOCK_COLORS_LIT, S2_BLOCK_COLORS_DIM),  # NEW
]
```

### Block count

**5 blocks** — matches S and the other behavioral bars for visual consistency. The fine resolution of SA's 10-block system is SA's feature, not something S2 needs to replicate. S2 is a combined warning level, not a fine-grained utilization readout.

### Filter

```python
self._s2_filter = FirstOrderFilter(0.0, 0.2, 1 / gui_app.target_fps)
# RC = 0.2s: between S's 0.5s and SA's 0.1s
# SA fires fast, S filters slowly. 0.2s gives S2 a quick but not jarring response.
```

### Color scheme

S2 should be visually distinct from S. S uses the standard green→orange→red gradient (`BLOCK_COLORS_LIT`). S2 should use a **blue-to-amber** gradient to communicate "combined physical + prediction alert" differently from pure behavioral prediction.

```python
S2_BLOCK_COUNT = 5
S2_BLOCK_COLORS_LIT = [
    rl.Color( 60, 148, 200, 235),   # level 1 – steel blue
    rl.Color( 80, 164, 180, 235),   # level 2 – teal
    rl.Color(120, 168, 130, 235),   # level 3 – transition (teal-green)
    rl.Color(210, 150,  60, 238),   # level 4 – amber
    rl.Color(214,  58,  44, 242),   # level 5 – red (same as S top block)
]
S2_BLOCK_COLORS_DIM = [
    rl.Color(16,  36,  52, 112),
    rl.Color(18,  38,  44, 112),
    rl.Color(26,  38,  28, 112),
    rl.Color(56,  38,  16, 116),
    rl.Color(58,  18,  18, 118),
]
```

**Rationale for blue-to-amber**: Blue visually differentiates from the existing green-red bars. The top block matches S's red (level 5) to communicate the same maximum urgency level regardless of bar. The transition through teal-green at level 3 creates a natural midpoint.

### Optional: Dynamic color by dominant source

If the active context shows which component (S or SA) is driving S2, the bar color could shift:

```python
# In _render, use different color array based on which is dominant
if sa_norm > s_norm:
    colors_lit = S2_SA_COLORS_LIT    # more orange/amber tones — physical
    colors_dim = S2_SA_COLORS_DIM
else:
    colors_lit = S2_S_COLORS_LIT     # more blue/cyan tones — prediction
    colors_dim = S2_S_COLORS_DIM
```

This requires storing `sa_dominant` as state, but no new cereal reads.

### Label

`"S2"` — clear and compact. At 43px wide, the label fits without truncation at `LABEL_FONT_SIZE = 32`.

---

## 8. Code Changes Required

Only one file requires changes. All cereal messages are already subscribed.

### [`selfdrive/ui/onroad/disengage_bars.py`](selfdrive/ui/onroad/disengage_bars.py)

**Constants to add** (top of file):

```python
# S2 bar: combination of steerOverrideProbs (2s) and SA utilization
S2_BLOCK_COUNT = 5
S2_BLOCK_COLORS_LIT = [...]  # blue-to-amber palette (see Section 7)
S2_BLOCK_COLORS_DIM  = [...]

# Width increase to accommodate 5th prediction bar
WIDTH = 700   # was 646
```

**`__init__` changes** (add one filter):

```python
self._s2_filter = FirstOrderFilter(0.0, 0.2, 1 / gui_app.target_fps)
self._s2_sa_dominant = False   # for color selection
```

**`_update_state` changes** (add DISENGAGED zero and active computation):

```python
# In DISENGAGED block:
self._s2_filter.update(0.0)
self._s2_sa_dominant = False

# In active block (after SA computation):
s2_val = _compute_s2(sm, ui_state)   # full gate + combination logic
self._s2_sa_dominant = (sa_norm > s_norm)
self._s2_filter.update(s2_val)
```

**`predict_bars` list** (add one entry):

```python
predict_bars = [
    (self._model_confidence_scaled,                              "C",  CONF_BLOCK_COUNT, ...),
    (min(self._brake_filter.x / PROB_SENSITIVITY_CEILING, 1.0), "B",  BLOCK_COUNT,      ...),
    (min(self._gas_filter.x / PROB_SENSITIVITY_CEILING, 1.0),   "G",  BLOCK_COUNT,      ...),
    (min(self._steer_filter.x / PROB_SENSITIVITY_CEILING, 1.0), "S",  BLOCK_COUNT,      ...),
    (min(self._s2_filter.x, 1.0),                               "S2", S2_BLOCK_COUNT,   # NEW
     S2_SA_COLORS_LIT if self._s2_sa_dominant else S2_S_COLORS_LIT,
     S2_SA_COLORS_DIM if self._s2_sa_dominant else S2_S_COLORS_DIM),
]
```

**That's the complete change set.** No other files need modification.

---

## 9. Visual Differentiation Summary

| Property | S bar | S2 bar |
|----------|-------|--------|
| Source | `steerOverrideProbs[max]` (10s) | `max(steerOverrideProbs[0], SA)` with gates |
| Colors | Green → Orange → Red | Blue → Teal → Amber → Red |
| Blocks | 5 | 5 |
| Filter RC | 0.5s | 0.2s |
| Width | ~56px (current) | ~43px (+S2) at WIDTH=646, ~51px at WIDTH=700 |
| Label | `S` | `S2` |
| Top block | Red at P=50% | Red at max urgency (SA saturated or S_2s near ceiling) |
| Speed behavior | Active at all speeds | Fades below 2 m/s, full sensitivity at 30+ m/s |
| Lane change | Active (driver signal?) | Suppressed during `laneChangeStarting` |
| Blind spot | Unaware | Amplified 1.2× when BSM active |
| Curve | Unaware | Amplified up to 1.3× by lateral accel |

---

## 10. Tuning Parameters

| Parameter | Suggested value | Effect |
|-----------|----------------|--------|
| S2 S horizon | `[0]` (2s) | Quiet background, sharp near-term spike |
| S2 Filter RC | 0.2s | Between SA (0.1s) and S (0.5s) |
| S2 speed floor | `np.interp(vEgo, [2, 10, 30], [0.3, 0.8, 1.0])` | Suppress at low speed |
| Curve amplify ceiling | 1.3× at 2.5 m/s² lat accel | Boosts on tight curves |
| Curve onset | 0.5 m/s² lat accel | Starts amplifying at gentle curves |
| Blind spot amplify | 1.2× | Moderate boost for adjacent vehicle |
| Lane confidence low | `laneLineProbs < 0.5` | LDW threshold — well-established |
| Lane confidence amplify | 1.2× max | Matches other amplifiers |
| Calibration S scaling | 0.7× when uncalibrated | 30% reduction, not full suppression |

---

## 11. Open Questions

**Q1: Should the contextual amplifiers be capped differently?**

Current design: each amplifier is applied multiplicatively, then `min(result, 1.0)`. A 1.3× curve factor and 1.2× blind spot factor together could push S2 above 1.0 even when the raw signal is modest. Consider an additive approach instead (add a small fixed amount when condition is true) rather than multiplicative, to avoid runaway amplification from multiple simultaneous contexts.

**Q2: How do contextual amplifiers interact with the filter?**

The filter smooths the output. If a contextual gate fires suddenly (blind spot appears), the filter will slow the response. Should amplification be applied to the filter input, or to the filter output? Applying to input: the 0.2s RC delays the effect. Applying to output: immediate visual change but may feel jarring.

**Q3: Is `steerOverrideProbs[0]` reliably different from `max()` in real drives?**

The research implies it should be, but the quantitative difference in real drives on the mici hardware is unknown. The horizontal B3/B4 bars show that the per-horizon structure is visible — a similar horizontal row for `steerOverrideProbs` at all 5 horizons would make this visible without requiring a decision.

**Q4: How should S2 behave when SA is in override state?**

`UIStatus.OVERRIDE` dims all bars to gray. S2 should follow the same pattern — dim when override, same as S and SA today.

**Q5: Does S2 make S redundant?**

If S2 uses `steerOverrideProbs[0]` (2s), and S uses `max()` (10s), they are complementary: S gives ambient awareness, S2 gives near-term urgency + physical measurement. They are not redundant. If S2 used the 10s window, S would be mostly redundant.
