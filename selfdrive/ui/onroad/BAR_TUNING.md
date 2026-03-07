# Disengage Bars — Tuning Reference

This document covers how each bar's signal is sourced, what openpilot event or alert it
leads into, the math behind block lighting thresholds, and the open design questions around
per-bar sensitivity ceilings. It is a companion to `disengage_bars.py`.

---

## 1. Bar Summary

| Bar | Label | Measures | Type | Upstream alert if extreme |
|-----|-------|----------|------|--------------------------|
| B | Brake disengage prob | How likely driver brakes and fully disengages OP within 10s | Predictive (neural net) | FCW "BRAKE!" alert |
| BI | Braking intensity | Actual vehicle deceleration right now | Reactive (physical) | None — already happening |
| G | Gas override prob | How likely driver presses gas to override OP within 10s | Predictive (neural net) | None |
| S | Steer override prob | How likely driver grabs wheel to override OP within 10s | Predictive (neural net) | None |
| SA | Steer actuation | How close steering is to its torque/accel limit | Reactive (physical) | "Take Control / Turn Exceeds Steering Limit" |
| DM | Driver monitoring | How distracted the driver is (inverted awareness) | Reactive (computed) | Driver attention alerts |

---

## 2. Signal Sources

### B / G / S — Neural Net Meta Head Probabilities

The model outputs a flat 88-element `meta` vector every frame. Disengage predictions are
stored in interleaved groups of 6, one group per time horizon (t = 2, 4, 6, 8, 10s):

```
# selfdrive/modeld/constants.py  lines 74-87
class Meta:
  ENGAGED          = slice(0, 1)
  GAS_DISENGAGE    = slice(1, 31, 6)   # indices 1, 7, 13, 19, 25
  BRAKE_DISENGAGE  = slice(2, 31, 6)   # indices 2, 8, 14, 20, 26
  STEER_OVERRIDE   = slice(3, 31, 6)   # indices 3, 9, 15, 21, 27
  HARD_BRAKE_3     = slice(4, 31, 6)
  HARD_BRAKE_4     = slice(5, 31, 6)
  HARD_BRAKE_5     = slice(6, 31, 6)
```

Time horizon mapping:
```
# selfdrive/modeld/constants.py  line 13
META_T_IDXS = [2., 4., 6., 8., 10.]  # seconds into the future
```

How they are filled into the cereal message:
```
# selfdrive/modeld/fill_model_msg.py  lines 134-141
disengage_predictions.brakeDisengageProbs = net_output_data['meta'][0, Meta.BRAKE_DISENGAGE].tolist()
disengage_predictions.gasDisengageProbs   = net_output_data['meta'][0, Meta.GAS_DISENGAGE].tolist()
disengage_predictions.steerOverrideProbs  = net_output_data['meta'][0, Meta.STEER_OVERRIDE].tolist()
```

Cereal schema (what the bar reads):
```
# cereal/log.capnp  lines 1155-1165
struct DisengagePredictions {
  t                  @0 :List(Float32);
  brakeDisengageProbs @1 :List(Float32);
  gasDisengageProbs   @2 :List(Float32);
  steerOverrideProbs  @3 :List(Float32);
  ...
}
```

**Our bars use `max()` across all 5 time horizons** — the worst-case prediction across the
full 10s window. This is a deliberate choice: we want the bar to rise as soon as *any*
horizon looks risky, not only the nearest one.

### BI — Braking Intensity

Source: `carState.aEgo` — the IMU-measured vehicle longitudinal acceleration in m/s².
Negative when decelerating. We flip the sign and clamp so only deceleration registers:

```python
# disengage_bars.py
self._accel_filter.update(max(-sm['carState'].aEgo, 0.0))
```

Scaled by `MAX_DECEL = 3.5 m/s²` — chosen as "firm but not emergency" braking.
For reference, 9.81 m/s² is 1g (panic stop).

### SA — Steering Actuation

Two code paths depending on lateral controller type:

**Torque-controlled cars** (most cars):
```python
torque_util = abs(sm['carOutput'].actuatorsOutput.torque)  # range 0.0–1.0
```

**Angle-controlled cars** (e.g. some Toyota hybrids):
```python
actual_lateral_accel  = controlsState.curvature * vEgo²
desired_lateral_accel = controlsState.desiredCurvature * vEgo²
accel_diff            = desired - actual  # controller correction term
roll_comp             = roll * g * interp(vEgo, [5,15], [0,1])
lateral_acceleration  = actual_lateral_accel - roll_comp
torque_util = clip(abs(lateral_acceleration + accel_diff) / max_lat_accel, 0, 1)
```

Top block (block 10) is reserved exclusively for when `lateralControlState.*.saturated`
is True — it never lights from the continuous utilization calculation.

### DM — Driver Monitoring

Source: `driverMonitoringState.awarenessStatus` — a float in [0, 1] where 1.0 is fully
attentive and 0.0 is terminal. Inverted so the bar fills upward as distraction increases:

```python
self._dm_filter.update(1.0 - max(min(awareness, 1.0), 0.0))
```

The label changes from "DM" to "P" (pose), "E" (eyes/blink), or "Ph" (phone) when
`driverMonitoringState.distractedType` flags are set.

---

## 3. Severity Hierarchy

The three probability bars predict events of meaningfully different severity:

| Bar | Physical trigger | openpilot event | State transition | Audible |
|-----|-----------------|-----------------|-----------------|---------|
| B | Driver brakes while moving | `USER_DISABLE` | → **disabled** | Disengage chime |
| G | Driver presses gas | `OVERRIDE_LONGITUDINAL` | → **overriding** | Silent |
| S | Driver touches steering wheel | `OVERRIDE_LATERAL` | → **overriding** | Silent |

Sources:
- Event triggers: `selfdrive/car/car_specific.py` lines 135-142
- Event definitions: `selfdrive/selfdrived/events.py` lines 719-733
- State machine transitions: `selfdrive/selfdrived/state.py` lines 27-45

Key implication: **B predicts a full openpilot death. G and S predict recoverable overrides
where OP stays alive.** This is an argument for B having a lower (more sensitive) ceiling
than G and S — see section 7.

Note: brake *at standstill* fires `preEnableStandstill`, not a disengage. The B bar is only
meaningful as a warning signal while the car is moving.

---

## 4. Upstream Alert Thresholds

These are the openpilot alerts that fire when situations become critical. The bars are
designed to show rising values *before* these fire.

### FCW — "BRAKE! / Risk of Collision"

Source: `selfdrive/selfdrived/selfdrived.py` lines 377-382

```python
model_fcw = sm['modelV2'].meta.hardBrakePredicted and not CS.brakePressed
```

`hardBrakePredicted` is True only when ALL of:
- `brake5ms2` probs > `[.05, .05, .15, .15, .15]` for 5 consecutive model frames
- `brake3ms2` probs > `[.7, .7]` for 2 consecutive model frames

```
# selfdrive/modeld/constants.py  lines 29-30
FCW_THRESHOLDS_5MS2 = [.05, .05, .15, .15, .15]
FCW_THRESHOLDS_3MS2 = [.7, .7]
```

The B bar uses `brakeDisengageProbs` (different signal from `HARD_BRAKE_*`). B is a
leading indicator of driver *intent* to brake-disengage; FCW is triggered by the model
predicting a specific deceleration magnitude. They are related but not identical.

### "Take Control / Turn Exceeds Steering Limit"

Source: `selfdrive/selfdrived/selfdrived.py` lines 368-375

```python
undershooting = abs(desired_lateral_accel) / abs(actual_lateral_accel) > 1.2
turning       = abs(desired_lateral_accel) > 1.0  # m/s²
if undershooting and turning and lac.saturated:
    events.add(EventName.steerSaturated)
```

Three conditions must all be true simultaneously:
1. Controller is requesting 20% more lateral accel than the car is achieving
2. The desired lateral accel is above 1.0 m/s² (actually turning, not straight)
3. The lateral controller reports `saturated = True`

**The SA bar's top block (block 10) fires from condition 3 alone.** The "Take Control"
alert requires all three. This means the SA bar can show saturation (top block lit) in
situations where the alert does not fire — e.g., at low speed or when the car is
tracking well despite saturation.

### Confidence Ball Green/Yellow/Red

Source: `selfdrive/modeld/fill_model_msg.py` lines 151-172
Constants: `selfdrive/modeld/constants.py` lines 60-62

```python
RYG_GREEN  = 0.01165
RYG_YELLOW = 0.06157
```

These thresholds apply to a rolling disengage score derived from the *combined*
probability `1 - (1-brake)*(1-gas)*(1-steer)` over a 5-frame buffer, not to individual
bar signals. The confidence ball's scale is not directly comparable to our bar ceilings.

---

## 5. Block Lighting Math

The core function:
```python
# disengage_bars.py  line 82
def _lit_levels(scaled_0_to_1: float, block_count: int) -> int:
    return round(min(max(scaled_0_to_1, 0.0), 1.0) * block_count)
```

Python's `round()` uses banker's rounding (round-half-to-even). For threshold purposes:
block N lights when `scaled >= (N - 0.5) / block_count`.

Since `scaled = raw_prob / ceiling`, block N lights when:

```
raw_prob >= ceiling × (N - 0.5) / block_count
```

### Reference tables

**B / G / S with current shared ceiling = 0.4, 5 blocks:**

| Block | Color | Lights at raw prob |
|-------|-------|-------------------|
| 1 | green | 4% |
| 2 | yellow-green | 12% |
| 3 | yellow | 20% |
| 4 | orange | 28% |
| 5 | red | 36% |

Bar saturates (stays full) above 36%. Ceiling of 40% is the clamp point, not the
top-block threshold.

**BI with MAX_DECEL = 3.5 m/s², 5 blocks:**

| Block | Lights at deceleration |
|-------|----------------------|
| 1 | 0.35 m/s² |
| 2 | 1.05 m/s² |
| 3 | 1.75 m/s² |
| 4 | 2.45 m/s² |
| 5 | 3.15 m/s² |

**SA with 10 blocks (blocks 1-9 = utilization, block 10 = saturation only):**

Blocks 1-9 each cover ~10% torque utilization. Block 10 only lights when
`lateralControlState.*.saturated = True`, regardless of the continuous utilization value.

---

## 6. Current Tuning Constants

All live in `selfdrive/ui/onroad/disengage_bars.py` at the top of the file.

| Constant | Value | Controls |
|----------|-------|----------|
| `PROB_SENSITIVITY_CEILING` | 0.4 | Raw prob at which B, G, S bars saturate |
| `MAX_DECEL` | 3.5 m/s² | Deceleration that fills the BI bar completely |
| `DEFAULT_MAX_LAT_ACCEL` | 3.0 m/s² | SA normalization for angle-controlled cars without CP data |
| `SA_BLOCK_COUNT` | 10 | Number of SA blocks (finer resolution than other bars) |
| `BLOCK_COUNT` | 5 | Number of blocks for B, BI, G, S, DM |
| B/S/G/DM filter RC | 0.5s | Smoothing time constant for probability/awareness signals |
| BI/SA filter RC | 0.15s | Smoothing time constant for physical actuation signals |

**RC time constant rationale:** Physical signals (BI, SA) are fast because they reflect
immediate mechanical state. Probability signals (B, G, S, DM) use 0.5s because the model
output has frame-to-frame noise and the signals are already predictive (future-looking).

---

## 7. Open Design Question: Should B, G, S Share One Ceiling?

Currently all three share `PROB_SENSITIVITY_CEILING = 0.4`. But they predict events of
different severity (see section 3). The severity argument says:

- **B** predicts a full disengage — most consequential, worth detecting early → lower ceiling (more sensitive)
- **G / S** predict recoverable overrides — less urgent, avoid false alarm → higher ceiling (less sensitive)

Candidate split:

| Bar | Proposed ceiling | Block 1 | Block 3 | Block 5 (full) |
|-----|-----------------|---------|---------|---------------|
| B | 0.3 | 3% | 15% | 27% |
| G | 0.6 | 6% | 30% | 54% |
| S | 0.6 | 6% | 30% | 54% |
| Current (all) | 0.4 | 4% | 20% | 36% |

To implement a split, replace `PROB_SENSITIVITY_CEILING` with three separate constants
and update the `bars` list in `_render()` accordingly.

The argument *against* splitting: the model's probability scale may not be well-calibrated
across event types. A gas-override prob of 0.3 might not be "equivalent urgency" to a
brake-disengage prob of 0.3. Without real-drive probability distribution data it is hard
to know whether the model assigns higher or lower raw values to one event type vs another.
Observing bar behavior on actual drives is the most reliable calibration method.
