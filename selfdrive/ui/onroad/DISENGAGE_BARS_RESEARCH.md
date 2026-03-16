# Disengage Bars: Research & Signal Analysis

POC widget replacing the DMoji in the tici onroad view with a mici-style confidence ball plus segmented LED-style bars. Goal: give drivers real-time, decomposed visibility into what openpilot's models are predicting and what the car is doing, without collapsing everything into a single abstracted confidence indicator.

---

## How to Think About This Widget (Start Here)

There are two fundamentally different kinds of questions a driver might want answered:

1. **"What is the model worried about?"** — Predictions and probabilities. Neural net outputs. Things the camera and model believe *will* happen in the next few seconds. These are inherently uncertain and behavioral — they reflect what the model has seen happen in similar situations historically.

2. **"What is the car actually doing right now?"** — Physical measurements. Sensors. Present-tense reality: how hard is it braking, how hard is it steering, is the driver looking away. These have no uncertainty — they are ground truth about what's happening this instant.

The mici confidence ball conflates both into a single number. This widget separates them into two visual groups:

```
Prediction group  (C | B | G | S)  ← "What the model thinks is coming"
Reactive group    (BI | SA | DM)   ← "What is physically happening right now"
```

A third layer exists that's easy to confuse with either: **model intent** — things like `modelV2.acceleration.x` and `modelV2.action.desiredAcceleration`. These are not predictions about driver behavior, and not sensor readings. They are the model's *own plan* for what it wants the car to do. They live between "worried" and "acting."

```
Worried (model predictions)  →  Intending (model plan)  →  Acting (physical reality)
  B / G / S bars                modelV2.action            BI / SA / DM bars
  (will driver override?)       (what model wants to do)  (what's happening now)
```

Understanding where a signal falls in this chain tells you how to interpret it and what its limitations are.

---

## Problem Statement

The mici confidence ball combines brake-disengage and steer-override probabilities into a single number:

```python
confidence = (1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))
```

By itself, that loses two things: (1) which signal is driving the change (brake vs steer), and (2) any forward-looking or present-tense physical awareness of what the car is actually doing. The current widget keeps the familiar confidence ball, but breaks the rest of the information into separate bars for model predictions, vehicle reaction, steering saturation, and driver monitoring.

## Current Implementation

Current widget layout:

- Confidence ball column on the far left, using the same formula and color zones as mici
- Horizontal hard-brake rows (B3, B4) spanning the full widget width, showing probability over each time horizon
- Prediction group: `C | B | G | S`
- Reactive group: `BI | SA | DM`

| Bar | Label | Source | Question it answers |
|-----|-------|--------|---------------------|
| Confidence ball | `●` | `(1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))` | "Overall, how confident is the model that openpilot will stay comfortable?" |
| B3 (horizontal) | `B3` | `modelV2.meta.disengagePredictions.brake3MetersPerSecondSquaredProbs` × 5 horizons | "Across 2/4/6/8/10s, how likely is a >3 m/s² brake event?" |
| B4 (horizontal) | `B4` | `modelV2.meta.disengagePredictions.brake4MetersPerSecondSquaredProbs` × 5 horizons | "Across 2/4/6/8/10s, how likely is a >4 m/s² brake event?" |
| C | C | `modelV2.confidence` (green/yellow/red enum) | "How accurate have recent predictions been vs what actually happened?" |
| B | B | `modelV2.meta.disengagePredictions.brakeDisengageProbs` | "How likely is the driver to brake-override openpilot?" |
| G | G | `modelV2.meta.disengagePredictions.gasDisengageProbs` | "How likely is the driver to press gas to override the current plan?" |
| S | S | `modelV2.meta.disengagePredictions.steerOverrideProbs` | "How likely is the driver to grab the steering wheel?" |
| BI | BI | `carState.aEgo` (measured deceleration) | "How hard is the car braking right now?" |
| SA | SA | `controlsState.lateralControlState` / `carOutput.actuatorsOutput.torque` | "How close is lateral control to its steering limit?" |
| DM | DM / `P` / `E` / `Ph` | `driverMonitoringState.awarenessStatus` and `distractedType` | "How distracted is the driver, and what type of distraction is active?" |

The bars are visually grouped: `C/B/G/S` are predictive neural-net signals, while `BI/SA/DM` are reactive physical or monitoring signals. `SA` uses 10 blocks for finer steering-limit resolution, with the top red block reserved for actual lateral-controller saturation. The B3/B4 horizontal rows each display all 5 time horizons side by side as block columns, so the temporal structure of hard-brake risk is directly visible.

**Additional fields consumed by the SA bar (not shown in original table):**
- `carControl.latActive` — on angle-state cars, SA returns 0 when lateral control is not active
- `liveParameters.roll` — roll compensation for the angle-state lateral accel formula
- `controlsState.curvature` / `controlsState.desiredCurvature` — angle-state SA source
- `ui_state.CP.maxLateralAccel` — per-car lateral accel ceiling (fallback: `DEFAULT_MAX_LAT_ACCEL = 3.0 m/s²`)

**Key limitation with BI**: `carState.aEgo` is still a lagging indicator -- it only shows braking that is *already happening*, not braking that *may be needed*. The original BI goal was forward-looking: "a brake event may be needed." The current implementation intentionally favors universality and immediate physical truth over prediction.

## The BI Bar Problem: What We Tried and Why It Failed

### Attempt 1: `longitudinalPlan.accels[0]`
The longitudinal planner's commanded acceleration. Failed because it's only populated when openpilot has longitudinal control (`openpilotLongitudinalControl`). On cars where openpilot only handles steering, this is empty/zero.

### Attempt 2: `carState.aEgo`
Measured vehicle acceleration from wheel speed sensors. Always populated, always reflects real physics. But it's the **present/past**, not the **future**. When approaching a stopped car that openpilot handles correctly, the bar only rises once braking begins -- it can't warn ahead of time.

## Available Signals for Forward-Looking Braking Need

> **Plain English framing**: The BI bar's job is to answer "is a brake event developing?" — but "developing" can mean very different things depending on your source. A radar tells you the physics of the gap right now. The neural net tells you what it plans to do. The planner tells you what MPC has committed to (but only if the car has longitudinal control). There is no single answer; each source has a blindspot the others cover.

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

Additional related signal: `meta.hardBrakePredicted` (bool) -- the FCW trigger. It fires when two independent conditions are simultaneously met across rolling frame buffers (computed in `fill_model_msg.py`):

- **5 m/s² check**: last 5 frames of `brake5MetersPerSecondSquaredProbs[t=2s]` all exceed `[.05, .05, .15, .15, .15]`
- **3 m/s² check**: last 2 frames of `brake3MetersPerSecondSquaredProbs[t=2s]` both exceed `[.7, .7]`

Both must be true simultaneously. The 3 m/s² threshold is intentionally very high (0.7) so the model must have near-certainty that at least moderate braking is needed; the 5 m/s² thresholds are lower but require sustained prediction across 5 frames. Very high signal when it fires, but binary, and suppressed in `selfdrived.py` when the driver is already braking or stock ACC is braking (`aEgo < -1.25 m/s²`).

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

## Additional Available Signals (Not Yet Covered)

These signals are all available in `ui_state.sm` and were not in the original research doc. They are grouped by source. The key question for each is: *where does it sit in the Worried → Intending → Acting chain?*

### Signal Group 6: `modelV2.action` — e2e Planner Intent (Single Values)

Source: computed in `modeld.py` from the plan trajectory via `get_accel_from_plan()` and `get_curvature_from_plan()`.

| Field | Type | Description |
|-------|------|-------------|
| `desiredCurvature` | float | Target curvature (rad/m) smoothed with `LAT_SMOOTH_SECONDS=0.0` |
| `desiredAcceleration` | float | Target acceleration (m/s²) smoothed with `LONG_SMOOTH_SECONDS=0.3` |
| `shouldStop` | bool | Model wants to come to a full stop |

**Key difference from `acceleration.x[0]`**: `desiredAcceleration` is derived from the velocity plan (rate of speed change) rather than from the raw acceleration model head, and applies a 0.3s smoothing. It is a cleaner, already-computed single scalar for use in the BI bar rather than indexing into the raw trajectory array.

### Signal Group 7: `modelV2.leadsV3` — Model-Only Lead Detection (3 Leads)

Source: model-only vision lead detection, not fused with radar. `radarState.leadOne` is the radar+vision fusion; `leadsV3` is what the camera sees before radar fusion.

Each of 3 leads has: `prob`, `probTime`, and per-timestep `(x, y, v, a)` with `xStd`, `yStd`, `vStd`, `aStd`.

| Field | Description |
|-------|-------------|
| `prob` | Lead detection confidence (used for MPC `modelProb`) |
| `x[0]` | Current longitudinal distance (m) |
| `v[0]` | Current lead speed (m/s) |
| `a[0]` | Current lead acceleration (m/s²) |
| `xStd`, `vStd` | Uncertainty — high std = ghosty detection |

**Pro vs radar**: no radar hardware required, sees more scenarios. **Con vs radar**: model-based so can miss cut-ins; std fields show when the detection is uncertain.

### Signal Group 8: `longitudinalPlan` Extended Fields

Beyond what is already documented, the planner publishes:

| Field | Type | Description |
|-------|------|-------------|
| `longitudinalPlanSource` | enum | `cruise \| lead0 \| lead1 \| lead2 \| e2e` — WHY the planner is doing what it's doing |
| `jerks` | float[] | Jerk trajectory (rate of acceleration change). High jerk = sudden onset of braking |
| `allowThrottle` | bool | Whether the planner permits throttle. False = path turns gray in standard mode. Controlled by `throttle_prob > 0.4 or vEgo <= 2.5` |
| `hasLead` | bool | Lead vehicle detected by `radarState.leadOne.status` |
| `fcw` | bool | Planner-level FCW: MPC predicts crash within 5s (separate from `hardBrakePredicted`) |

**`longitudinalPlanSource` is particularly useful for the BI bar**: it distinguishes between "the planner is braking because of a lead car" (`lead0`/`lead1`) vs "because the e2e model sees a non-radar threat" (`e2e`) vs "just cruise control" (`cruise`). Only populated when `openpilotLongitudinalControl == true`.

### Signal Group 9: Trajectory Uncertainty Fields

All `modelV2` trajectory outputs (`position`, `velocity`, `acceleration`, `orientation`, `orientationRate`) include per-point standard deviation arrays:

| Field | Description |
|-------|-------------|
| `acceleration.xStd[0]` | Uncertainty in the model's planned acceleration right now |
| `velocity.xStd[0]` | Uncertainty in predicted speed |
| `position.xStd` | Path position uncertainty (grows with distance) |

High `acceleration.xStd[0]` means the model is uncertain about its own acceleration plan — this could be used to **modulate the BI bar's confidence**: the bar value is still shown, but its opacity or color could reflect how much to trust it.

### Signal Group 10: `radarState` Extended Fields

Beyond `leadOne.dRel / vRel / status`, the radar state exposes:

| Field | Description |
|-------|-------------|
| `leadOne.modelProb` | How confident the model is that this is a real lead (range 0–1). MPC requires `> 0.9` for FCW. Low modelProb = possible ghost target |
| `leadOne.yRel` | Lateral offset of lead (m). Non-zero yRel means lead is not directly in front |
| `leadOne.vLead` | Absolute speed of lead (m/s) — different from vRel which is relative to ego |
| `leadOne.radar` | True if confirmed by hardware radar; false = vision-only detection |
| `leadTwo` | Second lead vehicle, used by MPC as obstacle 2. Only populated when `modelV2.leadsV3` has > 1 detection. Uses stricter confirmation (`low_speed_override=False`) |

**leadOne vs leadTwo important distinction**: leadOne allows low-speed radar tracks without model confirmation (`low_speed_override=True`), making it more sensitive to close obstacles in traffic but also more prone to ghost targets at low speed. leadTwo requires model+radar agreement.

### Signal Group 11: Meta Head — Untapped Probability Fields

Several meta head outputs are not currently used by any bar:

| Field | Time horizons | What it predicts |
|-------|---------------|-----------------|
| `gasPressProbs` | t=[0,2,4,6,8,10]s | P(driver physically presses gas pedal) — distinct from `gasDisengageProbs` which is about overriding openpilot |
| `brakePressProbs` | t=[0,2,4,6,8,10]s | P(driver physically presses brake pedal) — distinct from driver discomfort |
| `leftBlinkerProbs` | t=[0,2,4,6,8,10]s | P(left blinker activates) — model predicts lane change intent |
| `rightBlinkerProbs` | t=[0,2,4,6,8,10]s | P(right blinker activates) |
| `desireState` | 8-value vector | Current desire state: straight, lane change left/right, keep left/right |
| `engagedProb` | scalar | P(driver is currently engaged) — model's instantaneous belief about present state |

**Key semantic distinction** between the `gasPressProbs`/`brakePressProbs` fields and the disengage probs:
- `brakeDisengageProbs` — predicts driver *override behavior* (takeover)
- `brakePressProbs` — predicts physical *pedal press* (could happen while openpilot is still engaged, e.g., driver taps brake to reduce set speed)

### Signal Group 12: `carState` — Untapped Physical Signals

| Field | Description | Potential use |
|-------|-------------|---------------|
| `leftBlindspot` | BSM: object in left blind spot | Lane change safety warning |
| `rightBlindspot` | BSM: object in right blind spot | Lane change safety warning |
| `steeringAngleDeg` | Current steering wheel angle | Compare to desired angle for independent saturation estimate |
| `steeringRateDeg` | Rate of steering change (deg/s) | Rapid steering rate = driver override starting |
| `steeringTorque` | Driver torque on wheel | Non-zero = driver touching wheel |
| `steeringPressed` | Driver touching steering wheel | Gate for distraction, corroborates DM |
| `leftBlinker` / `rightBlinker` | Current blinker state | Corroborate model blinker predictions |
| `standstill` | Vehicle stopped | Context for DM (standstill exempts green pre-alert) |

## Recommended BI Bar Strategy

> **Decision framework**: The three options below trade off between universality, responsiveness, and scenario coverage. Radar is fastest for closing-lead scenarios but blind to everything else. Model acceleration covers everything the camera sees but is only as good as the model. Combining them with `max()` means the bar always shows the more urgent signal from either source — neither can hide a real threat. The current implementation uses `carState.aEgo` instead (present-tense reality), which is the most honest but the least forward-looking.

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

## Nuances, Quirks, and Cross-Signal Context

These are the easy-to-miss details that matter most when building or interpreting the bars. Many of them only become obvious by reading the actual controller code rather than the signal names alone.

### SA Bar: Saturation is Timer-Based, Not Instantaneous

> **In plain terms**: The SA bar's top red block only lights up after the steering controller has been pegged at its limit *continuously* for a car-specific duration. A single hard steering input on a winding road won't trigger it — it has to be sustained. This is intentional: it distinguishes between "the car is working hard" and "the car genuinely cannot steer any harder."

The `saturated` field on all lateral controllers is **not** a simple "am I at max torque right now" flag. It uses an accumulating timer (`sat_time`) in the base `LatControl` class:

```python
if (saturated or curvature_limited) and vEgo > sat_check_min_speed and not steer_limited_by_safety and not CS.steeringPressed:
    sat_time += dt
else:
    sat_time -= dt
sat_time = clip(sat_time, 0.0, steerLimitTimer)
return sat_time > (steerLimitTimer - 1e-3)
```

`steerLimitTimer` is a car-specific parameter. Saturation is only reported **true** after being continuously at-limit for `steerLimitTimer` seconds, and it **decays** when the condition clears. This means brief torque spikes on a straight road don't fire `saturated`. The SA bar's top block (reserved for saturation) reflects sustained, confirmed saturation.

Additional gating conditions:
- `vEgo > sat_check_min_speed` (10 m/s for PID/torque controllers, 5 m/s for angle)
- Not `steer_limited_by_safety` (panda safety clip doesn't count as controller saturation)
- Not `steeringPressed` (driver touching the wheel)

### SA Bar: Two Distinct "At the Limit" Concepts

> **In plain terms**: There are two different ways the car can be "at its steering limit." One is software — the controller internally maxes out its own output. The other is hardware safety — the panda chip refuses to pass on the full command. These are tracked separately and behave differently. The SA bar only shows the software limit today.

The SA bar tracks controller saturation (`saturated` bool), but there is a separate concept called `steer_limited_by_safety` computed in `controlsd.py`:

| Concept | Meaning | When true |
|---------|---------|-----------|
| `saturated` | Controller's own output is at its software limit | After sustained near-max output (timer-based) |
| `steer_limited_by_safety` | Panda hardware safety layer is clipping the command | `\|requested - actual\| > 2.5°` (angle) or `> 0.01` (torque) |

These are orthogonal. A car can be `saturated=true` while `steer_limited_by_safety=false` (controller thinks it's at its limit but panda agrees), or `steer_limited_by_safety=true` while `saturated=false` (a sudden safety clip that hasn't been sustained long enough). `steer_limited_by_safety` actually *suppresses* the saturation timer — the logic explicitly excludes safety-clipped frames from the saturation accumulation.

### SA Bar: Angle Controller Saturation Varies by Car

The angle controller (`angleState`) has **two different saturation definitions** depending on the car:

- **Tesla and cars using `use_steer_limited_by_safety`**: `angle_control_saturated = steer_limited_by_safety` — the car's controller calculates its own max lateral accel, so panda safety output is the correct saturation signal
- **Nissan, Toyota, Ford Q3**: `angle_control_saturated = |desiredAngle - actualAngle| > 2.5°` — these cars use torque-based or EPS-limited angle control where panda doesn't capture the true limit, so the angular error is used instead

### FCW: Two Independent Paths, Different Suppression Rules

> **In plain terms**: The camera-based model and the radar-based MPC can both raise a forward collision warning, but through completely different logic. The model FCW says "based on what I see, braking like this has historically preceded crashes." The planner FCW says "I ran the math and we will actually hit something in under 5 seconds." They often agree, but not always — and their suppression rules are different too.

The document previously only mentioned `hardBrakePredicted`. There are actually two FCW paths, and both are needed because they catch different scenarios:

| FCW Source | Mechanism | Requires |
|------------|-----------|---------|
| **Model FCW** (`modelV2.meta.hardBrakePredicted`) | Neural net: sustained high brake3 + brake5 probs | 5-frame + 2-frame rolling persistence |
| **Planner FCW** (`longitudinalPlan.fcw`) | MPC: predicts crash distance < 0.25m within 5s | `radarState.leadOne.modelProb > 0.9`, `crash_cnt > 2` |

Both trigger `EventName.fcw` in `selfdrived.py`. Suppression rules differ:
- Model FCW is suppressed when `brakePressed` OR stock ACC is braking hard (`aEgo < -1.25 m/s²`)
- Planner FCW is suppressed when standstill OR not enabled
- Planner FCW requires a confirmed radar lead (`modelProb > 0.9`) — it won't fire on vision-only ghost targets

### `longitudinalPlan.fcw` vs B4/B5 bars: Complementary Signals

The hard-brake probability bars (B3, B4) show the model's *continuous* probabilistic estimate. The planner FCW (`longitudinalPlan.fcw`) fires discretely when the MPC actually predicts a crash scenario. They measure different things:
- B4 bar high → model is worried about >4 m/s² braking happening within 10s
- Planner FCW → the closed-loop MPC trajectory (accounting for car response) predicts an actual collision

A high B4 bar does not guarantee planner FCW; planner FCW can fire with low B4 (sudden cut-in where probs haven't built up yet).

### `modelV2.acceleration.x` vs `modelV2.action.desiredAcceleration` vs `longitudinalPlan.accels[0]`

> **In plain terms**: Three different places in the code answer "how much does openpilot want to brake?" — but they are computed at different points in the pipeline, with different smoothing, different availability, and subtly different meanings. Knowing which one to use for a given bar requires knowing what each one actually represents.

These three represent the same concept (desired braking) at different pipeline stages:

```
Camera → NN acceleration head            → modelV2.acceleration.x[0]    (raw NN output, 33 values)
       → get_accel_from_plan() + smooth   → modelV2.action.desiredAcceleration  (single float, 0.3s smoothed)
       → longitudinalPlan.accels[0]       → MPC-commanded acceleration    (only when longControl=true)
```

- `acceleration.x[0]`: rawest, no smoothing, captures fast changes, also used for path coloring
- `action.desiredAcceleration`: already a single float with 0.3s smoothing, easier to use directly
- `longitudinalPlan.accels[0]`: MPC output incorporating lead constraints and comfort limits, only exists for long-control cars

### Path Coloring Depends on `experimentalMode`

> **In plain terms**: The green path on screen and `modelV2.acceleration.x` feel connected, but they aren't always. In standard mode the path is colored by whether the planner is willing to throttle — a binary yes/no. Only in experimental mode does the path shade through the full acceleration gradient. This means a gray path in standard mode doesn't mean "braking" — it means "holding back," which could just be following a slow car.

The doc previously implied `modelV2.acceleration.x` is always used for path coloring. This is only true in experimental mode. In standard mode, the model_renderer uses `longitudinalPlan.allowThrottle` blended through a `FirstOrderFilter(RC=0.25s)`:

| Mode | Path color source | When green | When gray |
|------|------------------|-----------|----------|
| Standard | `longitudinalPlan.allowThrottle` | Throttle permitted | Throttle not allowed |
| Experimental | `modelV2.acceleration.x` | Positive/neutral accel | Negative accel (braking expected) |

`allowThrottle` is `true` when `throttle_prob > 0.4 OR vEgo <= 2.5`. Experimental mode only activates when `openpilotLongitudinalControl == true`.

### laneLineProbs / roadEdgeStds as Scene Complexity Proxies

The model_renderer uses these for visual alpha, but they also encode model confidence about scene structure:

- `laneLineProbs[0]` (left inner), `[1]` (right inner): capped at 0.7 for rendering. Low value = model can't find the lane line
- `roadEdgeStds[0,1]`: higher std = model is more uncertain about where the road edge is

Low `laneLineProbs` and high `roadEdgeStds` together indicate a scene the model finds geometrically ambiguous — similar situations correlate with drops in the confidence ball. These could serve as a scene-complexity indicator independent of the disengage predictions.

### DM Standstill Exemption — The Bar Behaves Differently at Rest

> **In plain terms**: Looking at your phone at a red light is treated differently than looking at your phone at 60 mph. The DM system intentionally freezes its countdown at stops so that a glance down at an intersection doesn't cascade into a red alert. The raw `awarenessStatus` number still changes, but the alerts don't fire. The DM bar will show the underlying value — so the bar can be low while no alert banner appears. This is expected behavior, not a bug.

At a standstill, the DM system has a `standstill_orange_exemption` that prevents the awareness countdown from reaching orange (even if the driver is distracted). The green pre-alert is also suppressed. This means the DM bar effectively "pauses" at a red light — a distracted driver at a stop won't see the bar progress the same way as at speed. This exemption is not reflected in the raw `awarenessStatus` value sent to the UI; the bar will still show the underlying number, but alerts won't fire.

## Tuning Parameters

| Parameter | Value | Effect |
|-----------|-------|--------|
| `PROB_SENSITIVITY_CEILING` | 0.5 | B/G/S bars saturate at 50% raw probability; with 5 blocks this is ~10% per block |
| `MAX_DECEL` | 3.5 m/s² | BI bar saturates at ~0.35g |
| `DEFAULT_MAX_LAT_ACCEL` | 3.0 m/s² | SA bar lateral accel ceiling for cars without `CP.maxLateralAccel` |
| `H_BAR_B3_CEILING` | 0.60 | B3 horizontal bar: block is fully red at 60% raw probability |
| `H_BAR_B4_CEILING` | 0.30 | B4 horizontal bar: block is fully red at 30% raw probability (more sensitive) |
| `FirstOrderFilter RC (B/G/S)` | 0.5s | Smoothing for disengage prediction signals |
| `FirstOrderFilter RC (B3/B4)` | 0.5s | Smoothing for each of 10 hard-brake horizon filters (5 per bar) |
| `FirstOrderFilter RC (BI)` | 0.15s | Smoothing for physical decel signal (faster to track real braking) |
| `FirstOrderFilter RC (SA)` | 0.1s | Slightly faster response, matching mici TorqueBar behavior |
| `FirstOrderFilter RC (DM)` | 0.5s | Smoothing for awareness-based distraction display |
| `BLOCK_COUNT` | 5 | Number of discrete levels for B/G/S/BI/DM bars |
| `SA_BLOCK_COUNT` | 10 | Number of discrete levels for steering utilization; top block reserved for saturation |
| `CONF_BLOCK_COUNT` | 3 | Number of discrete levels for C (confidence classification) bar |
| `GROUP_GAP` | 34 px | Extra visual separation between prediction and reactive bar groups |

## Neural Network Meta Head Detail

> **In plain terms**: The road model outputs a single flat array of numbers called the "meta tensor." Each number is a probability learned from millions of real-world miles. When a human driver pressed the brake, grabbed the wheel, or activated a blinker, those moments became training data. The model learned to recognize the visual patterns that preceded those events. So when `brakeDisengageProbs[t=2s]` is high, it doesn't mean physics demands braking — it means the camera is seeing something that historically made drivers press the brake within 2 seconds.

The driving model outputs a `meta` tensor (55 floats, indices 0–54) sliced as:

```
Index layout:
[0]           : engagedProb
[1,7,13,19,25]  : gasDisengageProbs                    at t=[2,4,6,8,10]s
[2,8,14,20,26]  : brakeDisengageProbs                  at t=[2,4,6,8,10]s
[3,9,15,21,27]  : steerOverrideProbs                   at t=[2,4,6,8,10]s
[4,10,16,22,28] : brake3MetersPerSecondSquaredProbs     at t=[2,4,6,8,10]s
[5,11,17,23,29] : brake4MetersPerSecondSquaredProbs     at t=[2,4,6,8,10]s
[6,12,18,24,30] : brake5MetersPerSecondSquaredProbs     at t=[2,4,6,8,10]s
[31,35,39,43,47,51] : gasPressProbs                    at t=[0,2,4,6,8,10]s
[32,36,40,44,48,52] : brakePressProbs                  at t=[0,2,4,6,8,10]s
[33,37,41,45,49,53] : leftBlinkerProbs                 at t=[0,2,4,6,8,10]s
[34,38,42,46,50,54] : rightBlinkerProbs                at t=[0,2,4,6,8,10]s
```

Note: `gasPressProbs` / `brakePressProbs` predict physical pedal presses (6 horizons starting at t=0), which is distinct from the disengage probs (5 horizons starting at t=2s). `leftBlinkerProbs` / `rightBlinkerProbs` predict driver signaling intent.

All disengage probs are **cumulative** (P(by t=10s) >= P(by t=2s)). Using `max()` always yields the 10-second value. For a tighter window, index directly: `[0]` = 2s, `[1]` = 4s, etc.

These predictions are trained on real-world driver interventions -- the model learned from millions of miles of data when human drivers actually pressed the brake or grabbed the wheel. They measure **driver distrust/discomfort**, not physical threat level.

## Confidence Classification System (C Bar)

> **In plain terms**: The confidence ball asks "how worried is the model *right now*?" The C bar asks something different: "has the model been *right* lately?" A model can be calm and wrong (calm scene, but its prior predictions have been consistently off), or nervous and right (high disengage probs, and those probs accurately predicted what actually happened). The C bar is a historical accuracy check — it looks backward at whether the model's predictions from a few seconds ago matched reality.

The `modelV2.confidence` field (green/yellow/red enum) uses a more sophisticated rolling-buffer diagonal-score method over 5 snapshots. It combines all three disengage types and checks whether past predictions about the current moment were accurate. Thresholds: green < 0.01165, yellow < 0.06157, red >= 0.06157.

This system is computed in `fill_model_msg.py` and is now **actively used** by the widget as the `C` bar with 3 blocks (`CONF_BLOCK_COUNT = 3`). The C bar reads the enum directly without any filter.

**C bar vs the confidence ball**: these are two different things displayed simultaneously.
- **Confidence ball** (left column): the original mici formula — `(1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))` — a forward-looking behavioral prediction of driver discomfort.
- **C bar**: the rolling-buffer diagonal score — a retrospective accuracy check asking "were past predictions correct about what's happening right now?" It is a measure of **model track record**, not immediate risk.

They can diverge: the ball can be green (model is currently calm) while C is red (model has been wrong recently), or vice versa.

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

> **The key insight**: the ball reflects driver psychology, not physics. It learned "when do humans feel uncomfortable enough to take over?" — not "when is there actually danger?" These are correlated but not the same thing. A skilled driver in a tough scenario may never override, keeping the ball green even in a genuinely dangerous situation openpilot is managing well.

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
# torque_bar.py
actual_lateral_accel = controls_state.curvature * car_state.vEgo ** 2
desired_lateral_accel = controls_state.desiredCurvature * car_state.vEgo ** 2
accel_diff = desired_lateral_accel - actual_lateral_accel

# Roll compensation ramps in gradually between 5–15 m/s (ignored at low speed)
roll_compensation = np.interp(car_state.vEgo, [5, 15], [0.0, 1.0]) \
                    * live_parameters.roll * ACCELERATION_DUE_TO_GRAVITY
lateral_acceleration = actual_lateral_accel - roll_compensation

self._torque_filter.update(
    min(max(lateral_acceleration / max_lateral_acceleration + accel_diff, -1), 1)
)
```

This computes lateral acceleration from `curvature * v²`, subtracts road roll with a speed-dependent ramp (so banking on highways doesn't appear as a steering command, but the correction is suppressed at low speed where roll estimation is noisier), then adds an error correction term for how far actual curvature deviates from desired. Normalized by `max_lateral_acceleration = 3 m/s²`.

**On angle-control cars the torque bar uses the curvature-based formula above instead of raw torque, but it is always rendered** — there is no `angleState` visibility gate in `hud_renderer.py`. The torque bar is shown regardless of controller type.

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

> **In plain terms**: DM is an independent "is the driver paying attention?" system. It has its own camera (pointing at the driver), its own neural net, and its own state machine. It knows nothing about what the road is doing — only about the driver's face and head. The one exception is that the road model feeds a single value into DM (the 2-second brake-disengage probability) to loosen distraction thresholds when a human takeover appears to be intentional. Everything else in DM is camera-only. The `awarenessStatus` float it produces is the entire DM system compressed into one number: 1.0 means fully attentive, 0.0 means the car will start braking for you.

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

> **In plain terms**: The road model and the driver camera model run completely independently — but they talk to each other through one value. When the road model predicts the driver is likely to brake soon (high `brakeDisengageProbs[t=2s]`), DM loosens its head-angle thresholds. This prevents the DM bar from firing a distraction alert right as the driver is deliberately glancing down to brake-override. Without this coupling, any intentional takeover would look like distraction. It's a cooperative design where "the driver is probably about to take over" gives the driver more head-movement latitude.

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

### The `awarenessStatus` Abstraction — How Distraction Becomes a Number

> **In plain terms**: Think of `awarenessStatus` as a battery that drains while you're distracted and charges when you look at the road. It drains slowly (11 seconds to empty in active mode) but charges faster the more depleted it is — so brief distraction has little effect, but sustained distraction accumulates. Once it hits zero the car brakes for you. The DM bar in the widget directly visualizes this drain/charge cycle. The bar going lower means the battery is depleting.

`awarenessStatus` is a single float, `1.0 → 0.0 → -0.1`, that is the **entire output of the driver monitoring system compressed into one scalar**. Every alert, every forceDecel, every re-engagement block derives from this one number.

#### What it represents

```
1.0   driver is fully paying attention
0.72  (threshold_pre)   pre-alert: green banner starts here
0.54  (threshold_prompt) prompt alert: orange banner
0.0   terminal: red fullscreen + forceDecel command to car
-0.1  max negative (clamp floor) — counts time spent at red
```

The thresholds are computed from the timing constants, not hardcoded:
```python
threshold_pre    = _DISTRACTED_PRE_TIME_TILL_TERMINAL / _DISTRACTED_TIME   # 8/11  ≈ 0.727
threshold_prompt = _DISTRACTED_PROMPT_TIME_TILL_TERMINAL / _DISTRACTED_TIME # 6/11  ≈ 0.545
```

#### How it drains

Each frame (20 Hz = every 0.05s), `step_change` is subtracted when the driver is distracted:

```python
# active mode:  step_change = 0.05 / 11  ≈ 0.00455 per frame
# passive mode: step_change = 0.05 / 30  ≈ 0.00167 per frame
self.awareness = max(self.awareness - self.step_change, -0.1)
```

Draining only happens under two conditions (`certainly_distracted` OR `maybe_distracted`):

```python
certainly_distracted = (
    self.driver_distraction_filter.x > 0.63   # smoothed flag crossed 63% threshold
    and self.driver_distracted                  # raw distracted flag is set
    and self.face_detected                      # face must be visible
)
maybe_distracted = (
    self.hi_stds > _HI_STD_FALLBACK_TIME       # model uncertain for >10s
    or not self.face_detected                   # face not detected
)
```

The `driver_distraction_filter` is a `FirstOrderFilter(ts=0.25s)` on the raw bool — so brief glances away don't immediately drain awareness, they have to cross the 63% smoothed threshold.

#### How it recovers

When the driver IS paying attention, awareness refills — but **faster** than it drained, using a variable recovery rate that speeds up the more depleted the awareness is:

```python
recovery_rate = (
    (_RECOVERY_FACTOR_MAX - _RECOVERY_FACTOR_MIN) * (1.0 - self.awareness)
    + _RECOVERY_FACTOR_MIN
) * self.step_change
# _RECOVERY_FACTOR_MAX = 5x, _RECOVERY_FACTOR_MIN = 1.25x
# so: at awareness=1.0 → recover at 1.25x drain rate
#     at awareness=0.0 → recover at 5.0x drain rate
self.awareness = min(self.awareness + recovery_rate, 1.0)
```

Recovery **stops** when alert is orange or red (`awareness <= threshold_prompt`) — the driver must physically engage (steer/gas) to reset from orange/red, they can't just look forward.

#### Hard resets

Three things instantly reset `awareness` back to `1.0`:
1. Openpilot **disengages** (always-off mode)
2. Driver presses **steering wheel** or **gas** while paying attention
3. Mode switch from **passive → active** (face reacquires) — awareness is restored from the saved `awareness_active` buffer

#### Standstill exemptions

At a standstill:
- Green pre-alert is **suppressed entirely** — `standstill_orange_exemption` prevents draining if reaching orange
- The countdown effectively pauses while stopped, so a distracted driver at a red light won't alarm

#### The two independent awareness tracks

The system runs **two parallel awareness values**:

```python
self.awareness_active   # for when face is tracked (active mode)
self.awareness_passive  # for when falling back to wheel-touch (passive mode)
```

When the mode switches (face detected/lost), the current value is saved to the appropriate track and the other is restored. So losing and regaining the face doesn't reset your progress — it continues from where it left off in that mode.

#### What feeds into `certainly_distracted`

The full chain from camera → distracted bool:

```
driverStateV2.leftDriverData.faceProb > 0.7          → face_detected
driverStateV2.leftDriverData.faceOrientationStd < 0.3 → pose.low_std

# POSE check (relative to learned natural offset):
abs(pitch - pitch_offset) > 0.3133 * cfactor_pitch  → DISTRACTED_POSE
abs(yaw   - yaw_offset)   > 0.4020 * cfactor_yaw    → DISTRACTED_POSE

# BLINK check (only if eyes visible and no sunglasses):
(leftBlinkProb + rightBlinkProb)/2 > 0.865           → DISTRACTED_BLINK

# PHONE check:
phoneProb > 0.5                                       → DISTRACTED_PHONE

# Combined:
driver_distracted = (any distracted_type)
                    AND face_detected
                    AND pose.low_std

# Then smoothed:
driver_distraction_filter.update(driver_distracted)  # FirstOrderFilter(ts=0.25s)
# Drains awareness when: filter.x > 0.63
```

#### Alert event names (what selfdrived receives)

```python
# Active mode (face tracked):
EventName.preDriverDistracted       # green banner, awareness in (threshold_prompt, threshold_pre]
EventName.promptDriverDistracted    # orange alert, awareness in (0, threshold_prompt]
EventName.driverDistracted          # red fullscreen, awareness <= 0  → also forceDecel

# Passive mode (wheel-touch / no face):
EventName.preDriverUnresponsive
EventName.promptDriverUnresponsive
EventName.driverUnresponsive

# Always-on mode block:
EventName.tooDistracted             # blocks re-engagement after 3 terminal alerts or 30s at red
```

`forceDecel` is computed in `controlsd`, not in the DM daemon:
```python
cs.forceDecel = bool(driverMonitoringState.awarenessStatus < 0.0)
```

#### What the DisengageBars would need to add an awareness bar

To show `awarenessStatus` as a bar, subscribe to `driverMonitoringState`:

```python
# In disengage_bars.py:
sm = ui_state.sm

# The awareness level (1.0 = full, 0.0 = red):
awareness = sm['driverMonitoringState'].awarenessStatus

# Clamp to [0, 1] and invert so "full bar = danger":
danger_level = 1.0 - max(min(awareness, 1.0), 0.0)

# Or show it the other way (full bar = safe):
safe_level = max(min(awareness, 1.0), 0.0)

# Distraction type for color coding:
dtype = sm['driverMonitoringState'].distractedType
is_pose  = bool(dtype & 1)
is_blink = bool(dtype & 2)
is_phone = bool(dtype & 4)

# Active vs passive mode (different thresholds and timers):
is_active_mode = sm['driverMonitoringState'].isActiveMode
```

The color mapping would naturally follow the alert thresholds:
- `awareness > 0.727` → green (below pre-alert threshold)
- `0.545 < awareness <= 0.727` → yellow (in pre-alert zone)
- `0.0 < awareness <= 0.545` → orange (in prompt zone)
- `awareness <= 0.0` → red (terminal)

The `distractedType` bitmask lets you show sub-indicators: a tiny icon or color shift per distraction type (pose vs blink vs phone).

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

## Available DM Resources for UI Consumption

Everything below is **read-only from the UI's perspective**. We never touch the DM pipeline — we only subscribe to its published messages via `ui_state.sm`.

### Message 1: `driverStateV2` — Raw Neural Net Output (20 Hz)

Published by `dmonitoringmodeld`. This is what the camera model actually sees. Two parallel `DriverData` structs (LHD + RHD) — use `isRHD` to pick the right one.

| Field | Type | What it tells you | Currently used by UI? |
|-------|------|-------------------|-----------------------|
| `faceOrientation` | float[3] | `[pitch, yaw, roll]` in radians — live head angles | Yes — `DriverStateRenderer` (DMoji arcs + 3D outline) |
| `faceOrientationStd` | float[3] | Per-axis uncertainty. `> 0.3` = unreliable | Yes — `driver_camera_dialog.py` |
| `facePosition` | float[2] | Normalized `[x, y]` of face center in frame | Yes — `driver_camera_dialog.py`, `DriverStateRenderer` |
| `facePositionStd` | float[2] | Uncertainty on face position | **No** — untapped |
| `faceProb` | float | P(face visible). System gates at `> 0.7` | Yes — `driver_camera_dialog.py` |
| `leftEyeProb` | float | P(left eye open) | Yes — `driver_camera_dialog.py` (mici) |
| `rightEyeProb` | float | P(right eye open) | Yes — `driver_camera_dialog.py` (mici) |
| `leftBlinkProb` | float | P(left eye blinking). Threshold: `> 0.865` | **No** — only used by `dmonitoringd` policy |
| `rightBlinkProb` | float | P(right eye blinking) | **No** — only used by `dmonitoringd` policy |
| `sunglassesProb` | float | P(sunglasses). If `> 0.9`, blink check skipped | Yes — `driver_camera_dialog.py` (mici) |
| `phoneProb` | float | P(phone visible). Threshold: `> 0.5` | **No** — only used by `dmonitoringd` policy |
| `wheelOnRightProb` | float | P(steering wheel is on right side) | Yes — `driver_camera_dialog.py` |
| `frameId` | uint32 | Camera frame number | **No** |
| `modelExecutionTime` | float | Model inference time (seconds) | **No** |
| `gpuExecutionTime` | float | GPU time (seconds) | **No** |
| `rawPredictions` | bytes | Full model output tensor (only when `SEND_RAW_PRED`) | **No** — debug only |

**Key untapped signals**: `leftBlinkProb`, `rightBlinkProb`, `phoneProb`, `facePositionStd`. These are the raw per-frame probabilities *before* the policy layer integrates them. They could be used for fine-grained sub-indicators or real-time visualization.

### Message 2: `driverMonitoringState` — Policy Layer Output (20 Hz)

Published by `dmonitoringd`. This is the **processed, integrated** state after the policy logic runs.

| Field | Type | What it tells you | Currently used by UI? |
|-------|------|-------------------|-----------------------|
| `awarenessStatus` | float | `1.0`=attentive → `0.0`=terminal → `<0`=forceDecel | Yes — `DisengageBars` (DM bar), `driver_camera_dialog.py` |
| `isDistracted` | bool | Smoothed distraction state (after 0.25s filter) | **No** — we use `distractedType` instead |
| `distractedType` | uint32 | Bitmask: `POSE=1, BLINK=2, PHONE=4` | Yes — `DisengageBars` (DM label) |
| `faceDetected` | bool | `faceProb > 0.7` | Yes — `DriverStateRenderer` (mici), `onboarding.py` |
| `isActiveMode` | bool | `true`=camera-tracked, `false`=wheel-touch fallback | Yes — `DriverStateRenderer` (arc colors) |
| `isRHD` | bool | Right-hand drive detected | Yes — `DisengageBars`, `DriverStateRenderer` |
| `stepChange` | float | Per-frame awareness drain/refill rate | **No** — could show drain speed |
| `awarenessActive` | float | Active-mode awareness (separate from passive) | **No** — could show both tracks |
| `awarenessPassive` | float | Passive-mode awareness (separate from active) | **No** — could show both tracks |
| `posePitchOffset` | float | Learned "natural forward" pitch for this driver | **No** — could show calibration state |
| `poseYawOffset` | float | Learned "natural forward" yaw for this driver | **No** — could show calibration state |
| `posePitchValidCount` | uint32 | Samples accumulated for pitch calibration | **No** — could show calibration progress |
| `poseYawValidCount` | uint32 | Samples accumulated for yaw calibration | **No** — could show calibration progress |
| `isLowStd` | bool | Model is confident (`std < 0.3`). `false` = passive mode | **No** — could indicate model quality |
| `hiStdCount` | uint32 | Frames with high pose uncertainty | **No** — could show model struggle |
| `uncertainCount` | uint32 | Frames with uncertain pose overall | **No** — could show model struggle |
| `events` | List(OnroadEvent) | DM-specific alert events | Used by `selfdrived` for alert banners |

**Key untapped signals**: `stepChange` (drain rate — could animate differently), `awarenessActive`/`awarenessPassive` (dual-track), `isLowStd` (model confidence), `hiStdCount`/`uncertainCount` (model struggle duration), `posePitchValidCount`/`poseYawValidCount` (calibration progress).

### DM-Related Params (persistent key-value store)

| Param | Type | What it controls |
|-------|------|-----------------|
| `AlwaysOnDM` | bool | DM runs even when openpilot is not engaged |
| `DriverTooDistracted` | bool | Set after 3 terminal alerts or 30s at red — blocks re-engagement |
| `IsDriverViewEnabled` | bool | Demo/preview mode for driver camera |
| `IsRhdDetected` | bool | Persisted RHD detection (survives reboots) |

### DM Alert Event Names (fired by `dmonitoringd` → consumed by `selfdrived`)

| Event | Mode | Awareness range | Banner |
|-------|------|-----------------|--------|
| `preDriverDistracted` | Active (face tracked) | `0.545 < x ≤ 0.727` | Green |
| `promptDriverDistracted` | Active | `0.0 < x ≤ 0.545` | Orange |
| `driverDistracted` | Active | `x ≤ 0.0` | Red + forceDecel |
| `preDriverUnresponsive` | Passive (no face) | `0.2 < x ≤ 0.5` | Green |
| `promptDriverUnresponsive` | Passive | `0.0 < x ≤ 0.2` | Orange |
| `driverUnresponsive` | Passive | `x ≤ 0.0` | Red + forceDecel |
| `tooDistracted` | Any | — | Blocks re-engagement entirely |

### All DM Constants (from `helpers.py` → `DRIVER_MONITOR_SETTINGS`)

**Timing:**

| Constant | Value | Meaning |
|----------|-------|---------|
| `_DT_DMON` | `0.05` (20 Hz) | DM tick interval |
| `_DISTRACTED_TIME` | `11.0s` | Active mode: total time to terminal |
| `_DISTRACTED_PRE_TIME_TILL_TERMINAL` | `8.0s` | Active: pre-alert fires at this remaining |
| `_DISTRACTED_PROMPT_TIME_TILL_TERMINAL` | `6.0s` | Active: prompt alert fires at this remaining |
| `_AWARENESS_TIME` | `30.0s` | Passive mode: total time to terminal |
| `_AWARENESS_PRE_TIME_TILL_TERMINAL` | `15.0s` | Passive: pre-alert fires at this remaining |
| `_AWARENESS_PROMPT_TIME_TILL_TERMINAL` | `6.0s` | Passive: prompt alert fires at this remaining |

**Detection thresholds:**

| Constant | Value | What it gates |
|----------|-------|---------------|
| `_FACE_THRESHOLD` | `0.7` | Minimum `faceProb` to consider face visible |
| `_EYE_THRESHOLD` | `0.65` | Minimum `eyeProb` to check blinks |
| `_SG_THRESHOLD` | `0.9` | `sunglassesProb` above this = skip blink check |
| `_BLINK_THRESHOLD` | `0.865` | Average blink prob above this = distracted |
| `_PHONE_THRESH` | `0.5` | `phoneProb` above this = phone distraction |
| `_POSE_PITCH_THRESHOLD` | `0.3133 rad (~18°)` | Pitch below natural = distracted |
| `_POSE_PITCH_THRESHOLD_SLACK` | `0.3237 rad` | Lenient pitch (calm road) |
| `_POSE_YAW_THRESHOLD` | `0.4020 rad (~23°)` | Yaw from center = distracted |
| `_POSE_YAW_THRESHOLD_SLACK` | `0.5042 rad` | Lenient yaw (calm road) |
| `_POSESTD_THRESHOLD` | `0.3` | Orientation std above this = model uncertain |
| `_DISTRACTED_FILTER_TS` | `0.25s` | FirstOrderFilter RC for smoothing raw distraction bool |

**Recovery and limits:**

| Constant | Value | What it does |
|----------|-------|--------------|
| `_RECOVERY_FACTOR_MAX` | `5.0` | Max recovery speed multiplier (at awareness=0) |
| `_RECOVERY_FACTOR_MIN` | `1.25` | Min recovery speed multiplier (at awareness=1) |
| `_HI_STD_FALLBACK_TIME` | `200 frames (10s)` | After this many high-std frames → passive fallback |
| `_MAX_TERMINAL_ALERTS` | `3` | Terminal alerts before lockout |
| `_MAX_TERMINAL_DURATION` | `600 frames (30s)` | Cumulative terminal frames before lockout |
| `_ALWAYS_ON_ALERT_MIN_SPEED` | `11 m/s` | Min speed for always-on DM alerts |

**Calibration:**

| Constant | Value | What it does |
|----------|-------|--------------|
| `_POSE_CALIB_MIN_SPEED` | `13 m/s` | Min speed to accumulate calibration |
| `_POSE_OFFSET_MIN_COUNT` | `1200 frames (60s)` | Min samples before using learned offset |
| `_POSE_OFFSET_MAX_COUNT` | `7200 frames (360s)` | Max calibration window |
| `_PITCH_NATURAL_OFFSET` | `0.011 rad` | Default pitch offset (before learning) |
| `_YAW_NATURAL_OFFSET` | `0.075 rad` | Default yaw offset |
| `_WHEELPOS_CALIB_MIN_SPEED` | `11 m/s` | Min speed for RHD calibration |
| `_WHEELPOS_THRESHOLD` | `0.5` | Probability threshold for RHD decision |
| `_WHEELPOS_FILTER_MIN_COUNT` | `300 frames (15s)` | Min samples before committing RHD |

### DM Source Files

| File | Role |
|------|------|
| `selfdrive/monitoring/dmonitoringd.py` | Main daemon — glue between model output and policy |
| `selfdrive/monitoring/helpers.py` | `DriverMonitoring` class: state machine, `_update_states()`, `_update_events()`, `get_state_packet()` |
| `selfdrive/modeld/dmonitoringmodeld.py` | Neural net inference on driver camera → `driverStateV2` |
| `cereal/log.capnp` | Message definitions (`DriverStateV2` at line ~2156, `DriverMonitoringState` at line ~2219) |
| `selfdrive/ui/ui_state.py` | SubMaster subscription — confirms both `driverMonitoringState` and `driverStateV2` are subscribed |
| `selfdrive/controls/controlsd.py` | Reads `awarenessStatus < 0` → sets `forceDecel` |
| `selfdrive/selfdrived/selfdrived.py` | Reads `events` from DM → triggers alert banners |
| `selfdrive/selfdrived/events.py` | Event definitions and alert text for all DM events |
| `selfdrive/monitoring/test_monitoring.py` | Unit tests for DM state machine |

### What Our DisengageBars Currently Use vs What's Available

```
CURRENTLY USED                          AVAILABLE BUT UNTAPPED
─────────────────────                   ──────────────────────────
driverMonitoringState:                  driverMonitoringState:
  ✓ awarenessStatus  → DM bar fill       ○ stepChange         → drain/refill speed
  ✓ distractedType   → DM label          ○ awarenessActive    → camera-mode track
  ✓ isRHD            → bar anchor        ○ awarenessPassive   → wheel-touch track
                                          ○ isDistracted       → smoothed bool
                                          ○ faceDetected       → face visibility
                                          ○ isActiveMode       → camera vs wheel mode
                                          ○ isLowStd           → model confidence
                                          ○ hiStdCount         → model struggle duration
                                          ○ uncertainCount     → pose uncertainty duration
                                          ○ posePitchOffset    → learned driver offset
                                          ○ poseYawOffset      → learned driver offset
                                          ○ posePitchValidCount → calibration progress
                                          ○ poseYawValidCount   → calibration progress

                                        driverStateV2 (raw model):
                                          ○ faceOrientation    → live head angles
                                          ○ faceOrientationStd → model certainty
                                          ○ facePosition       → face location
                                          ○ faceProb           → face detection raw
                                          ○ leftBlinkProb      → per-eye blink
                                          ○ rightBlinkProb     → per-eye blink
                                          ○ phoneProb          → phone raw score
                                          ○ sunglassesProb     → sunglasses raw score
                                          ○ leftEyeProb        → eye visibility
                                          ○ rightEyeProb       → eye visibility
```

### Potential Future Enhancements Using Untapped Signals

| Idea | Signal source | Visual |
|------|--------------|--------|
| Show active vs passive mode on DM bar | `isActiveMode` | Different border color or icon |
| Show model confidence/struggle | `isLowStd`, `hiStdCount` | Dim or blink the DM bar when model uncertain |
| Dual-track awareness | `awarenessActive` + `awarenessPassive` | Two sub-bars or split indicator |
| Drain speed indicator | `stepChange` | Animation speed or glow intensity |
| Calibration progress | `posePitchValidCount` / `_POSE_OFFSET_MIN_COUNT` | Progress bar during first drive |
| Per-eye blink visualization | `leftBlinkProb`, `rightBlinkProb` | Eye icons that blink with driver |
| Phone probability bar | `phoneProb` raw | Sub-indicator on DM bar |
| Live head pose mini-display | `faceOrientation` from `driverStateV2` | Small 3D head angle indicator |

---

## Reference: ExperimentalMode and Personality

### experimentalMode

> **In plain terms**: Standard mode uses a physics-based MPC (model predictive controller) to follow vehicles and cruise — the neural net's acceleration wishes are an *input* to the MPC, which may override them for comfort or safety. Experimental mode lets the neural net's plan be the *more conservative* of the two choices: if the e2e model wants to brake harder than the MPC, it wins. This makes the car more reactive to what the camera sees and less dependent on radar lead tracking. It also changes the path color from a binary green/gray into a full acceleration gradient so you can see what the model intends moment-to-moment.

`selfdriveState.experimentalMode` is a bool that fundamentally changes how the longitudinal stack and UI behave. It is only active when `openpilotLongitudinalControl == true`; on steering-only cars the param is removed.

| | Standard mode | Experimental mode |
|-|--------------|------------------|
| **aTarget** | MPC only | `min(e2e, MPC)` — e2e model can be more conservative |
| **shouldStop** | MPC only | `e2e OR MPC` |
| **planSource** | `cruise / lead0 / lead1 / lead2` | can be `e2e` |
| **Path coloring** | `allowThrottle` (green/gray) | `acceleration.x` gradient (green=accel, red=decel) |
| **Camera** | road cam always | switches to wide cam at low speed (hysteresis) |

When interpreting the B bar or BI bar in context: in experimental mode, the e2e model is directly influencing `aTarget`, so `modelV2.acceleration.x[0]` and `modelV2.action.desiredAcceleration` are more directly causal than in standard mode where the MPC may override the model's preference.

### personality (`selfdriveState.personality`)

> **In plain terms**: Personality changes how much space the car tries to maintain behind the lead and how abruptly it's allowed to change acceleration (jerk). A high B bar reading on aggressive personality means less than the same reading on relaxed — in aggressive mode the car is running a tighter gap by design, so brake-disengage predictions naturally run higher. Personality is context for interpreting the B and BI bars, not a signal to display directly.

The longitudinal personality sets following distance and jerk tolerance for the MPC:

| Personality | `T_FOLLOW` | `JERK_GAIN` | Effect |
|-------------|-----------|------------|--------|
| Relaxed | 1.75s | 1.0× | Larger gap, gentler braking |
| Standard | 1.45s | 1.0× | Default behavior |
| Aggressive | 1.25s | 0.5× | Tighter following, snappier response |

This affects how the B bar should be interpreted: in aggressive mode, the model may trigger brake-disengage predictions at shorter TTC because the system is running a tighter following window. A high B bar on aggressive mode is less alarming than on relaxed mode. The `T_FOLLOW` values apply to the MPC comfort constraints.

---

## Reference: SelfdriveState and System Context

`selfdriveState` (published by `selfdrived.py`) provides UI-relevant system context beyond just enabled/active:

| Field | Type | Description |
|-------|------|-------------|
| `state` | OpenpilotState | `disabled / preEnabled / enabled / softDisabling / overriding` |
| `enabled` | bool | Openpilot is engaged |
| `active` | bool | Actively controlling (enabled and not in override) |
| `engageable` | bool | Can engage (no blocking conditions) |
| `experimentalMode` | bool | e2e longitudinal active |
| `personality` | LongitudinalPersonality | aggressive / standard / relaxed |
| `alertText1` | text | Primary alert message |
| `alertText2` | text | Secondary alert message |
| `alertStatus` | AlertStatus | normal / userPrompt / critical |
| `alertHudVisual` | VisualAlert | `steerRequired / fcw / brakePressed / ...` |

`softDisabling` is particularly relevant: this is the state where openpilot is shutting down due to a detected condition (e.g., DM alert at terminal, brake override) but hasn't fully disengaged yet. During `softDisabling`, `controlsState.forceDecel` is also set to true (in addition to the DM-triggered path), which is a nuance the doc previously missed.

`alertHudVisual` provides the semantic type of the current alert — `steerRequired` fires when the SA bar saturates, `fcw` fires from either FCW path, `brakePressed` fires when the driver presses the brake while engaged. This could be used to drive alert-specific bar animations.

---

## Reference: Device Health and System Reliability Signals

> **In plain terms**: All the bars assume the model is running correctly and the sensors are valid. That assumption can be wrong. If the camera isn't calibrated, the model's lane-line geometry is skewed. If `liveParameters.valid` is false, the roll compensation in the SA bar is using a stale estimate. These health signals don't get displayed anywhere in the current widget — but they matter when reasoning about why a bar is showing an unexpected value.

These signals are available in `ui_state.sm` and are relevant for understanding when model/control outputs should be trusted:

| Signal | Source | Relevant threshold |
|--------|--------|--------------------|
| `liveCalibration.calStatus` | `liveCalibration` | `uncalibrated` / `calibrated` / `invalid` / `recalibrating` — model_renderer returns early if not calibrated |
| `liveCalibration.calPerc` | `liveCalibration` | 0–100, calibration progress |
| `liveParameters.valid` | `liveParameters` | False when sensor parameters have not converged — affects roll compensation accuracy |
| `liveParameters.roll` | `liveParameters` | Road bank angle — used by torque bar and SA bar for angle-control cars |
| `deviceState.thermalStatus` | `deviceState` | `green / yellow / red / danger` — red/danger triggers overheat alert |
| `deviceState.memoryUsagePercent` | `deviceState` | > 90% triggers `lowMemory` event |
| `deviceState.freeSpacePercent` | `deviceState` | < 7% triggers `outOfSpace` event |

**Calibration state and the model**: when `calStatus != calibrated`, the model may be operating with incorrect camera-to-road geometry. `model_renderer.py` returns early if `liveCalibration` has not been received, and uses `liveCalibration.height[0]` (or a fallback) for path z-offset. An uncalibrated system can still run but accuracy is reduced.

**`liveParameters.valid`**: the live parameters estimator (GNSS + IMU fusion) estimates road roll, steer ratio, and tire stiffness in real time. When `valid=false`, the roll used by the SA bar's angle-state formula may be stale or wrong, meaning the SA bar's roll compensation is unreliable. The disengagement bars do not currently check `liveParameters.valid` before applying roll.

---

## File Locations

| File | Purpose |
|------|---------|
| `selfdrive/ui/onroad/disengage_bars.py` | Bar widget implementation |
| `selfdrive/ui/onroad/augmented_road_view.py` | Tici onroad view (renders bars) |
| `selfdrive/ui/mici/onroad/confidence_ball.py` | Mici confidence ball (reference) |
| `selfdrive/ui/mici/onroad/torque_bar.py` | Mici torque bar (reference for SA bar design) |
| `selfdrive/ui/mici/onroad/hud_renderer.py` | Mici HUD (torque bar rendering, alert visual gating) |
| `selfdrive/ui/onroad/model_renderer.py` | Path/lane rendering (uses `acceleration.x` in experimental, `allowThrottle` in standard) |
| `selfdrive/ui/ui_state.py` | UIState singleton, SubMaster subscriptions |
| `selfdrive/modeld/fill_model_msg.py` | Model output → cereal packing, `hardBrakePredicted` logic, confidence diagonal score |
| `selfdrive/modeld/constants.py` | Meta tensor slice indices (0–54), `T_IDXS`, FCW thresholds |
| `selfdrive/modeld/modeld.py` | `get_accel_from_plan()`, `get_curvature_from_plan()` → `modelV2.action` |
| `selfdrive/controls/controlsd.py` | `forceDecel` logic, `steer_limited_by_safety`, lateral controller dispatch |
| `selfdrive/controls/lib/latcontrol.py` | Base `_check_saturation()` timer logic (applies to all controller types) |
| `selfdrive/controls/lib/latcontrol_torque.py` | Torque controller saturation definition |
| `selfdrive/controls/lib/latcontrol_pid.py` | PID controller saturation definition |
| `selfdrive/controls/lib/latcontrol_angle.py` | Angle controller saturation (two car-specific paths) |
| `selfdrive/controls/lib/longitudinal_planner.py` | Longitudinal planner: MPC vs e2e selection, `allowThrottle`, `shouldStop` |
| `selfdrive/controls/lib/longitudinal_mpc_lib/long_mpc.py` | MPC FCW logic, `crash_cnt`, `T_FOLLOW` / `JERK_GAIN` per personality |
| `selfdrive/controls/radard.py` | Radar state publisher, `leadOne` / `leadTwo` fusion, `low_speed_override` |
| `selfdrive/selfdrived/selfdrived.py` | FCW event assembly, DM-aware `forceDecel`, `experimentalMode` publishing |
| `selfdrive/monitoring/dmonitoringd.py` | DM daemon: model output → policy |
| `selfdrive/monitoring/helpers.py` | DM state machine, saturation timer, calibration, awareness tracks |
