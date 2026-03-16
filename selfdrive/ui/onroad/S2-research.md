# S2 Research: Combining S and SA into a Single Lateral Warning Signal

This document explores whether `steerOverrideProbs` (S bar) and the steering actuator utilization (SA bar) should be combined into a single lateral warning signal, what that combination should look like, and what the implications are. It is grounded in codebase evidence throughout.

**The driver goal this serves**: give the earliest possible heads-up that a situation requires more driver focus on steering — before a disengage or saturation event has already happened.

---

## 1. What S and SA Actually Measure (Ground Truth)

Before any combination is possible, it is essential to understand precisely what each signal measures — not at an abstract level, but at the code level.

### S bar — `steerOverrideProbs`

**Source**: `modelV2.meta.disengagePredictions.steerOverrideProbs` (5 values at t=[2,4,6,8,10]s)

**What it was trained on**: Real-world moments when `CS.steeringPressed` became true. Not abstract "driver discomfort" or "visual complexity" — the specific physical event of a driver putting torque on the steering column above a per-brand threshold.

`steeringPressed` is determined from the car's torsion bar torque sensor and varies significantly by platform:

| Brand | Detection method | Threshold |
|-------|-----------------|-----------|
| Toyota | Raw: `abs(steeringTorque) > 100` | 100 (unitless CAN value) |
| Honda | Raw: car-specific map | 400–1200 |
| Hyundai | Filtered: `update_steering_pressed(..., min_count=5)` | 150–250 |
| Tesla | Filtered: `update_steering_pressed(..., min_count=5)` | 1.0 Nm |
| Ford | Filtered: `update_steering_pressed(..., min_count=5)` | 1.0 Nm |
| Rivian | Filtered: `update_steering_pressed(..., min_count=5)` | 1.0 Nm |
| GM | Raw: `abs(steeringTorque) > 1.0` | 1.0 Nm |
| Nissan | 12-sample average > threshold | 1.0 Nm |
| VW | Raw: `abs(steeringTorque) > STEER_DRIVER_ALLOWANCE` | 60–80 Nm |

The `update_steering_pressed()` base class (`opendbc/car/interfaces.py:315-319`) uses a counter that increments when above threshold and decrements otherwise, clamped to `[0, min_count*2+1]`. `steeringPressed=True` requires several consecutive frames above threshold — this filters out brief column vibrations.

**What S actually predicts**: "Will the driver put sustained torque on the steering column within the next N seconds?" This is trained on driver behavior, not road physics. A driver grabs the wheel when they feel uncomfortable or want to correct — not purely when the physics demands it.

**How it is consumed by the bar** (`disengage_bars.py:413`):

```python
self._steer_filter.update(max(predictions.steerOverrideProbs or [0.0]))
```

`max()` across 5 horizons always returns the 10-second cumulative probability since the values are monotonically increasing. The bar never shows the tighter 2-second window. Filter RC = 0.5s.

**Display scaling**: `steer_filter.x / PROB_SENSITIVITY_CEILING (0.5)` — the bar saturates at 50% raw probability. Each of 5 blocks represents ~10% raw probability.

---

### SA bar — Steering Actuator Utilization

**Source**: `carOutput.actuatorsOutput.torque` (torque cars) or curvature-based lateral acceleration (angle cars)

**Torque cars** (`disengage_bars.py:455`):

```python
torque_util = abs(sm['carOutput'].actuatorsOutput.torque)
```

`actuatorsOutput.torque` is normalized to [-1, 1] by the car controller (e.g. `apply_torque / STEER_MAX`). `abs()` maps it to [0, 1] direction-agnostic utilization.

**Angle cars** (`disengage_bars.py:440-453`):

```python
actual_lateral_accel = controlsState.curvature * vEgo²
desired_lateral_accel = controlsState.desiredCurvature * vEgo²
accel_diff = desired_lateral_accel - actual_lateral_accel
roll_compensation = liveParameters.roll * g * interp(vEgo, [5,15], [0.0, 1.0])
lateral_acceleration = actual_lateral_accel - roll_compensation
torque_util = clip(abs(lateral_acceleration + accel_diff) / maxLateralAccel, 0, 1)
```

`maxLateralAccel` varies by car (from `CarParams`): Tesla 2.5, Nissan 1.5, Ford 1.5, Toyota most cases 2.2–3.0, Hyundai 2.6–2.7. Fallback is `DEFAULT_MAX_LAT_ACCEL = 3.0 m/s²`.

**The top block (saturation)** — reserved for `lat_saturated` (`disengage_bars.py:460-463`):

```python
if lat_saturated:
    self._torque_utilization_filter.update(1.0)
else:
    self._torque_utilization_filter.update(min(torque_util, (SA_BLOCK_COUNT-1)/SA_BLOCK_COUNT))
```

Non-saturated utilization is capped at 0.9 (9 of 10 blocks). Block 10 only lights when `lat_saturated=True`.

`lat_saturated` comes from `_check_saturation()` in `latcontrol.py:22-29` — a timer that accumulates only when:
- Controller output is at its software limit (raw saturation)
- `vEgo > sat_check_min_speed` (10 m/s for PID/torque, 5 m/s for angle)
- NOT `steer_limited_by_safety` (panda clip doesn't count)
- NOT `CS.steeringPressed` (driver touching wheel clears the timer)

The timer must exceed `CP.steerLimitTimer` seconds before saturation is reported. Common values: 0.1s (PSA), 0.4s (Toyota, Hyundai, GM, Tesla), 0.8s (Honda, Mazda), 1.0s (Ford, Nissan).

**Display scaling**: 10 blocks, top block reserved. Filter RC = 0.1s.

---

## 2. The Critical Difference: Timing

This is the core argument for combining S and SA. They respond at completely different speeds to the same underlying event.

| Signal | Source rate | Filter RC | 63% step response | 95% step response |
|--------|------------|-----------|------------------|------------------|
| S | 20 Hz (model) | 0.5s | ~0.55s | ~1.5s |
| SA (continuous torque) | 100 Hz (carOutput) | 0.1s | ~0.1s | ~0.3s |
| SA (saturation top block) | 100 Hz | 0.1s + steerLimitTimer | ~0.5–1.1s | ~0.7–1.3s |

**SA is 5x faster than S for continuous torque changes.** When a crosswind hits or the car enters a tight curve, `actuatorsOutput.torque` changes within the next control frame (~10ms). SA reaches 63% of its new value in ~0.1s. S cannot respond faster than ~0.55s even if the model immediately changes its predictions at the next 20Hz frame.

**But SA's saturation block is slower** — the `steerLimitTimer` delay (0.4s on most cars) means the top block takes longer to light than S to respond to the same event on a tight curve. For saturation-level events, they are comparable in response time.

**The timeline of a crosswind event on a curve:**

```
t = 0.0s   Crosswind hits
           SA starts rising (controller feels it immediately)
           S still low (camera sees a normal curve)

t = 0.1s   SA has risen to ~63% of its new level
           S still near zero

t = 0.5s   SA well-established
           S begins to respond if the visual scene is shifting

t = 0.4-1.0s  (car-dependent) SA saturation block lights
              S approaching midrange if drift is now visible

t = 1.0-2.0s  Car drifting visibly toward lane edge
              S building toward high values
              Driver grabs wheel → steeringPressed=True (the label S was trained on)
```

**Implication**: SA provides meaningful signal 0.5–1.5 seconds before S catches up in a physically-driven lateral stress scenario. Showing only S misses the early warning window entirely.

---

## 3. Existing Combination Patterns in the Codebase (Precedent)

The codebase has six existing examples of combining signals from different pipeline layers. Each establishes precedent for the S+SA combination.

### Pattern 1: Confidence Ball — Independence Formula

**Formula** (`confidence_ball.py:39-42`, `disengage_bars.py:422-425`):

```python
confidence = (1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))
```

Two different disengage types (brake and steer), both behavioral predictions, combined as independent events. Neither is a physical measurement. Note: gas is deliberately excluded — the combination is selective, not exhaustive.

**Formula meaning**: P(no brake disengage) × P(no steer override). Either going high drops confidence fast.

### Pattern 2: modelV2.confidence — Sophisticated Multi-Type Combination

**Formula** (`fill_model_msg.py:153-166`):

```python
# Step 1: combine all three disengage types
any_disengage_probs = 1 - ((1-brake) * (1-gas) * (1-steer))

# Step 2: per-slice hazard probabilities
ind_disengage_probs = [p0, (p1-p0)/(1-p0), (p2-p1)/(1-p1), ...]

# Step 3: rolling 5-snapshot buffer, read anti-diagonal
score = mean(buffer[0,4], buffer[1,3], buffer[2,2], buffer[3,1], buffer[4,0])
```

The anti-diagonal score measures "how consistently did past predictions for this moment turn out to be correct?" All three disengage types (brake, gas, steer) are combined into a union probability before the rolling buffer. This is the most principled combination in the codebase.

### Pattern 3: FCW — OR of Prediction and Physical

**Formula** (`selfdrived.py:379-381`):

```python
model_fcw = modelV2.meta.hardBrakePredicted and not brakePressed
planner_fcw = longitudinalPlan.fcw and enabled
if planner_fcw or model_fcw:
    events.add(EventName.fcw)
```

Camera-based prediction (`hardBrakePredicted`) OR radar/MPC-based physical calculation (`longitudinalPlan.fcw`). Structurally identical to what we want for S+SA: two independent sources, different pipeline layers, combined with OR (equivalent to `max()` for booleans).

### Pattern 4: BI Option C — `max()` of Prediction and Physics

**Formula** (DISENGAGE_BARS_RESEARCH.md recommended strategy):

```python
model_decel = max(-modelV2.acceleration.x[0], 0.0)        # camera prediction
radar_decel = vRel**2 / (2*dRel) if lead and closing else 0  # physical physics
combined = max(model_decel, radar_decel)
```

`max()` across a model prediction and a physical measurement on the **same axis** (longitudinal deceleration). This is the most direct structural precedent for S+SA — same axis, different layers, `max()` picks whichever is more urgent.

### Pattern 5: `steerSaturated` Alert — Prediction vs Reality Comparison on the Lateral Axis

**Formula** (`selfdrived.py:368-375`):

```python
actual_lateral_accel = controlsState.curvature * vEgo**2
desired_lateral_accel = modelV2.action.desiredCurvature * vEgo**2
undershooting = abs(desired_lateral_accel) / abs(actual_lateral_accel) > 1.2
if undershooting and turning and lac.saturated:
    events.add(EventName.steerSaturated)
```

The system itself fires a `steerSaturated` event by comparing model intent (`desiredCurvature`) vs physical reality (`curvature`) on the lateral axis. It requires both a prediction element (desired > actual by 20%) AND a physical measurement element (saturation timer). This is already a production combination of prediction and physics on the same lateral axis. Our proposed S+SA combination mirrors exactly this logic at the UI level.

### Pattern 6: DM Policy — Cross-Pipeline Coupling

`brakeDisengageProbs[0]` (road model prediction) directly modulates DM pose thresholds. When the road model predicts an imminent brake override, DM loosens its distraction thresholds. This is a cross-pipeline combination where one prediction feeds into another system's parameters.

---

## 4. Three Candidate Combination Formulas

Given the goal of **earliest warning**, three formulas are worth evaluating.

### Formula A: `max(S, SA)` — Earliest Warning

```python
lateral_warning = max(S_norm, SA_norm)
```

- `S_norm  = min(steer_filter.x / PROB_SENSITIVITY_CEILING, 1.0)`
- `SA_norm = torque_utilization_filter.x`

**Behavior**: The bar rises to whichever signal is higher. SA fires first in physically-driven scenarios; S fires first in visually-ambiguous scenarios.

**Scenario table:**

| S | SA | `max(S,SA)` | Interpretation |
|---|---|-------------|----------------|
| 0.0 | 0.0 | 0.00 | Both calm |
| 0.5 | 0.0 | 0.50 | Model worried, car fine |
| 0.0 | 0.8 | 0.80 | Car struggling, model calm (SA gave early warning) |
| 0.5 | 0.5 | 0.50 | Both moderate, same level |
| 0.9 | 0.9 | 0.90 | Crisis, convergence |
| 0.2 | 0.9 | 0.90 | SA dominant — SA is driving the bar |

**The BI Precedent**: This is exactly `max(model_decel, radar_decel)` from BI Option C. The pattern is already established in the same widget for the longitudinal axis.

**Best for**: Maximum early warning. The bar responds to whichever source sees the problem first, without requiring both to agree.

---

### Formula B: `1 - (1-S) * (1-SA)` — Independence / Union Formula

```python
lateral_warning = 1 - (1 - S_norm) * (1 - SA_norm)
```

**Behavior**: Amplifies convergence. When both signals are elevated, the result jumps above either individual value.

**Scenario table:**

| S | SA | `1-(1-S)(1-SA)` | Interpretation |
|---|---|----------------|----------------|
| 0.0 | 0.0 | 0.00 | Both calm |
| 0.5 | 0.0 | 0.50 | Only S contributing |
| 0.0 | 0.8 | 0.80 | Only SA contributing |
| 0.5 | 0.5 | 0.75 | Convergence amplification (+0.25 over either alone) |
| 0.9 | 0.9 | 0.99 | Crisis amplification |

**The Confidence Ball Precedent**: This is `1 - (1-brake)(1-steer)` rearranged. The confidence ball uses this pattern for combining two behavioral predictions.

**Best for**: Detecting when both signals agree — i.e., the physical and behavioral evidence both point to the same developing crisis. Less sensitive to SA-only early warnings (same as `max()` for SA=0.8, but diverges as both rise).

---

### Formula C: `S * SA` — Crisis Detection Only

```python
lateral_warning = S_norm * SA_norm
```

**Behavior**: Both must be elevated for the result to be significant. Low if either is near zero.

**Scenario table:**

| S | SA | `S * SA` | Interpretation |
|---|---|---------|----------------|
| 0.0 | 0.0 | 0.00 | |
| 0.5 | 0.0 | 0.00 | SA is zero, result is zero — SA early warning is invisible |
| 0.0 | 0.8 | 0.00 | S is zero, result is zero |
| 0.5 | 0.5 | 0.25 | Both moderate, result is low |
| 0.9 | 0.9 | 0.81 | Both high, result is high |

**Best for**: Identifying the rare case where both signals are simultaneously high — the highest urgency scenario. **Not suitable for earliest warning** because it suppresses all single-source signals.

---

## 5. Why `max()` is the Right Formula for the Stated Goal

The goal is **earliest warning**. `max()` is the correct formula because:

1. **It never delays**: the bar rises the moment either source detects something. `1-(1-S)(1-SA)` gives the same result as `max()` when only one signal is elevated (they are algebraically equivalent when the other is 0), but `max()` is computationally simpler and semantically clearer.

2. **It is already the established pattern for this problem**: BI Option C (`max(model_decel, radar_decel)`) and FCW (`model_fcw OR planner_fcw`) both use `max()`/OR to combine a prediction signal with a physical signal on the same axis. S and SA are structurally identical: prediction (S) vs physical measurement (SA) on the lateral axis.

3. **It respects SA's timing advantage**: SA responds in ~0.1s vs S in ~0.55s. `max()` lets SA's speed show up in the bar immediately. The independence formula also does this, but `max()` makes the semantics explicit: "whichever is more urgent."

4. **It mirrors the system's own behavior**: `steerSaturated` in `selfdrived.py` already fires based on a combined check of desired vs actual lateral acceleration AND controller saturation. The bar using `max(S, SA)` surfaces the same information before the alert threshold is crossed.

---

## 6. What You Lose and Whether It Matters

### The decomposition loss

With S and SA combined, the bar cannot independently show:
- "Model nervous, car fine" — the early-warning visual that distinguishes ambiguous scenes from genuine physical stress
- "Car struggling, model calm" — the SA-only early warning that S can't yet see

**Whether this matters depends on the user.** For a driver who only wants to know "should I pay attention to steering?" — it does not matter. For a developer running test drives to understand model behavior — it may matter a lot.

### Mitigation: Color encoding without separate bars

The decomposition can be preserved without showing two separate bars by encoding which source is driving the current bar level in color:

```
Bar height = max(S, SA)

Color when SA > S:  red/orange (physical, hardware-driven)
Color when S > SA:  cyan/blue  (prediction, behavior-driven)
Color when equal:   blend
```

This gives the driver a single number to watch (one action: be ready to steer) while giving a power user visible information about which layer is active.

### The precedent argument supports losing the decomposition

The confidence ball combines brake and steer without decomposing which is driving it. FCW fires without indicating whether it came from model or planner. In both cases, openpilot decided that one combined signal was more useful than two decomposed signals for the driver's action. The same logic applies to S+SA.

---

## 7. Other Axis Pairs Worth the Same Analysis

The S+SA combination is one of three natural pairs where a behavioral prediction and a physical measurement share the same axis.

### Pair 1: B + BI (Longitudinal axis — already partially addressed)

| Signal | Type | What it measures |
|--------|------|-----------------|
| B | Prediction | P(driver will brake-override) — trained on `brakePressed` events |
| BI | Physical | `abs(aEgo)` — actual measured deceleration |

**`max(B, BI)` for early longitudinal warning**: Same structure as `max(S, SA)`. BI responds faster (RC=0.15s vs B's RC=0.5s). B fires before BI when the model sees a visual threat that hasn't caused braking yet. BI fires instantly when the car is already braking.

**Critical difference from S+SA**: BI is `carState.aEgo`, which is always the present tense. There is no saturation concept on the longitudinal axis — BI is purely the current physical state. So `max(B, BI)` answers "is braking happening or predicted?" cleanly, with no timer delays.

**The BI Option C recommendation** is already `max(model_decel, radar_decel)` which is a three-source version of this same idea. Adding B directly into that max gives a four-source earliest-warning longitudinal bar:

```python
longitudinal_warning = max(
    max(brakeDisengageProbs) / PROB_SENSITIVITY_CEILING,  # behavioral prediction
    max(-modelV2.acceleration.x[0], 0) / MAX_DECEL,       # model acceleration plan
    radar_required_decel / MAX_DECEL,                      # physics
    max(-aEgo, 0) / MAX_DECEL,                             # ground truth
)
```

### Pair 2: DM + S (Driver attention axis — different nature)

| Signal | Type | What it measures |
|--------|------|-----------------|
| DM | Physical (camera-based) | Driver's current attention level — `awarenessStatus` |
| S | Prediction | P(driver will grab wheel) |

This pair is structurally different. S predicts driver intervention on steering; DM measures whether the driver is paying attention. Their combination would answer: "is the driver about to need to steer AND not currently watching?"

`max(DM, S)` would show the bar rising if either the driver is distracted OR a steering intervention is coming. `DM * S` would show risk only when both are simultaneously true — "needs to steer but not watching."

`DM * S` is arguably the more useful combination: a high S with a fully attentive driver is low risk. A high S with a distracted driver is the dangerous case. But this creates a compound UI signal that is hard to interpret at a glance and is better expressed as an alert condition than a continuous bar.

**Recommendation**: Keep DM separate. The DM bar answers "how distracted is the driver?" — a standalone question with its own distinct driver action (look at the road). Combining DM with S obscures both signals.

---

## 8. Implementation: What `max(S, SA)` Would Look Like

### Input sources (already available in `disengage_bars.py`)

```python
# Already computed:
S_norm  = min(self._steer_filter.x / PROB_SENSITIVITY_CEILING, 1.0)
SA_norm = min(self._torque_utilization_filter.x, 1.0)

# Combined:
lateral_warning = max(S_norm, SA_norm)
```

### Filter considerations

S and SA have different filter RCs (0.5s vs 0.1s). In a combined bar, the faster RC dominates for the physical signal (SA) and the slower RC smooths the prediction signal (S). This is actually correct behavior — you want the physical signal to respond fast and the prediction signal to be smooth.

No new filters are needed. Both signals are already filtered before the `max()` is applied.

### The S bar's horizon choice

Currently `max(steerOverrideProbs)` always returns the 10-second cumulative probability. For the combined bar, using the **2-second horizon** (`steerOverrideProbs[0]`) instead of max() would make S respond to near-term situations more sharply and reduce the gap between S and SA's response windows. Worth testing: `steerOverrideProbs[0] / PROB_SENSITIVITY_CEILING` vs `max(steerOverrideProbs) / PROB_SENSITIVITY_CEILING`.

---

## 9. Open Questions

**Q1: Which S horizon should feed the combined bar?**

`max(steerOverrideProbs)` = 10-second cumulative value. `steerOverrideProbs[0]` = 2-second probability. The 2-second value would make S more reactive and reduce its lag below 0.55s in fast-developing scenarios. But it would also reduce the "early warning" nature of S — the longer horizon is specifically useful for alerting before the 2-second window.

**Q2: Should continuous SA utilization (blocks 1-9) feed the combined bar, or only the saturation event (block 10)?**

For earliest warning, continuous utilization should feed the combined bar. Saturation is a lagging indicator (requires `steerLimitTimer` delay). If only saturation fed the combination, you lose SA's primary timing advantage.

**Q3: Does the color-encoding mitigation add enough information to justify the added visual complexity?**

A single bar with color-encoded source (orange=physical, cyan=prediction) gives power users decomposition without requiring them to track two bars. But color is harder to read at a glance than bar height. This is a UX tradeoff worth testing in real drives.

**Q4: Does combining remove a useful diagnostic signal for developers?**

When S is high but SA is low, that tells a developer the model is uncertain about a visually ambiguous scene while the car is physically comfortable. This is valuable training insight — it suggests the model's prediction is not well-calibrated for that scenario. If S and SA are combined, this signal is invisible. A developer mode that shows them separated while the driver-facing mode shows the combined signal may be the right tradeoff.

---

## Summary

| | `max(S, SA)` | `1-(1-S)(1-SA)` | `S*SA` |
|--|-------------|----------------|--------|
| Earliest warning | Best | Same as max when one is 0 | Worst |
| Crisis emphasis | Equal | Amplified | Best |
| SA-only early warning | Full | Full | None |
| S-only warning | Full | Full | None |
| Precedent | BI Option C, FCW | Confidence ball | None |
| Interpretability | "Higher of the two" | "Either would worry me" | "Both must be high" |

**For the stated goal of earliest warning**: `max(S, SA)` is the correct formula. It is the same pattern already used for BI Option C (longitudinal axis) and FCW, applied to the lateral axis. It gives SA's hardware-level speed advantage to the combined bar while preserving S's ability to catch visually-driven scenarios SA cannot see.

The combination mirrors what `steerSaturated` in `selfdrived.py` already does in the alerting system — combining model intent and physical reality on the lateral axis to detect when the car cannot execute what the model wants. The bar would surface the same information continuously, before the threshold for alerting is crossed.
