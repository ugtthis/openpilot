# `modelV2.confidence` Research

This note is a focused companion to `DISENGAGE_BARS_RESEARCH.md`. Its purpose is to document exactly what `modelV2.confidence` is, how it is computed, what it is related to, and what realistic UI integration options exist today.

The goal here is precision:

- Distinguish the enum `modelV2.confidence` from the older mici-style confidence ball.
- Explain the time math and rolling-buffer logic accurately.
- Separate signals that are currently used from signals that merely exist on the message.
- Highlight the practical options if we want to expose this in the onroad UI.

## TL;DR

`modelV2.confidence` is a 3-level enum (`green`, `yellow`, `red`) published on every `modelV2` message, but its score is built from a rolling 5-snapshot history of disengage predictions and only updates its underlying buffer every 2 seconds.

It is **not** the same signal as the current mici confidence ball:

- `modelV2.confidence`:
  - combines `brakeDisengageProbs`, `gasDisengageProbs`, and `steerOverrideProbs`
  - converts cumulative forecasts into per-slice probabilities
  - compares successive past forecasts about the same approximate present moment
  - outputs only `green/yellow/red`

- current confidence ball in `mici` and `disengage_bars.py`:
  - uses only brake + steer
  - uses the worst-case 10s cumulative values via `max()`
  - computes a continuous scalar:
    - `(1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))`
  - drives both ball color and vertical position

In the current tree, `modelV2.confidence` appears to be **produced but not consumed by any onroad UI widget**.

## Canonical Definition

### Where the field lives

Schema:

- File: `cereal/log.capnp`
- Field: `ModelDataV2.confidence`
- Type: `ConfidenceClass`

Enum definition:

```text
red    @0
yellow @1
green  @2
```

That ordering matters if any future code compares raw enum integers instead of names.

### Where the field is computed

Producer:

- File: `selfdrive/modeld/fill_model_msg.py`
- Function: `fill_model_msg(...)`

Threshold constants:

- File: `selfdrive/modeld/constants.py`
- `RYG_GREEN = 0.01165`
- `RYG_YELLOW = 0.06157`

Buffer geometry:

- `DISENGAGE_WIDTH = 5`
- `CONFIDENCE_BUFFER_LEN = 5`
- effective buffer shape: `5 snapshots x 5 horizon slices = 25 floats`

Time horizons for the disengage predictions:

- `META_T_IDXS = [2., 4., 6., 8., 10.]`

Model rate:

- `MODEL_RUN_FREQ = 20`

Confidence buffer update cadence:

- buffer is updated when `vipc_frame_id % (2 * MODEL_RUN_FREQ) == 0`
- at 20 Hz, that is every 40 frames
- therefore the rolling confidence history advances every **2 seconds**

## Exact Algorithm

The code path in `fill_model_msg.py` does the following.

### 1. Read the three cumulative disengage forecasts

From the model meta head:

- `brakeDisengageProbs`
- `gasDisengageProbs`
- `steerOverrideProbs`

Each is a length-5 cumulative probability series at:

- `t = [2, 4, 6, 8, 10]` seconds

Important semantic point:

- these values are cumulative probabilities of the event happening **by** each future time
- they are not five independent per-slice probabilities
- they should be monotonic non-decreasing in a well-behaved output

### 2. Combine the three event types into "any disengage"

Code:

```python
any_disengage_probs = 1 - ((1 - brake) * (1 - gas) * (1 - steer))
```

Interpretation:

- this is a union-style combination using survival terms
- it treats the three disengage types as if they were independent at each horizon
- the result is the cumulative probability that **at least one** of those disengage events happens by each horizon

This is broader than the current confidence ball, which ignores gas override entirely.

### 3. Convert cumulative horizons into per-2-second slice probabilities

Code:

```python
ind_disengage_probs = np.r_[
  any_disengage_probs[0],
  np.diff(any_disengage_probs) / (1 - any_disengage_probs[:-1]),
]
```

This is the most important detail in the whole system.

Although the code comment calls these "independent disengage prob for each 2s slice," the math is more precisely:

- first slice:
  - `p0 = P(event in next 0-2s)`
- later slices:
  - `pk = (Ck - Ck-1) / (1 - Ck-1)`

Where:

- `Ck` is the cumulative probability by time horizon `k`

That means each later slice is the probability of an event in that slice **conditioned on the event not having happened earlier**.

So this is best understood as a hazard-like per-slice conversion, not five fully independent unconditional probabilities.

### 4. Push the slice probabilities into a rolling 5x5 history

The publish state stores:

- `disengage_buffer = zeros(CONFIDENCE_BUFFER_LEN * DISENGAGE_WIDTH)`
- size = `25`

On each 2-second update:

- shift old data left by 5
- append the newest 5-slice vector at the end

Conceptually the buffer is:

```text
row 0: oldest snapshot   -> [2s, 4s, 6s, 8s, 10s] slice probabilities
row 1
row 2
row 3
row 4: newest snapshot   -> [2s, 4s, 6s, 8s, 10s] slice probabilities
```

### 5. Score the anti-diagonal

Code:

```python
for i in range(5):
  score += disengage_buffer[i*5 + 5 - 1 - i] / 5
```

Expanded index selection:

- row 0, col 4
- row 1, col 3
- row 2, col 2
- row 3, col 1
- row 4, col 0

That is the anti-diagonal of the 5x5 buffer:

```text
oldest snapshot:  10s slice
next snapshot:     8s slice
next snapshot:     6s slice
next snapshot:     4s slice
newest snapshot:   2s slice
```

This is clever and easy to miss.

Those five terms are all forecasts about roughly the **same real-world moment**, just made at different times:

- 8 seconds ago, the model predicted risk 10 seconds out
- 6 seconds ago, the model predicted risk 8 seconds out
- 4 seconds ago, the model predicted risk 6 seconds out
- 2 seconds ago, the model predicted risk 4 seconds out
- now, the model predicts risk 2 seconds out

So the score is not simply "current risk." It is closer to:

- "How much disengage risk has the model been consistently assigning to approximately the current moment?"

That gives the enum temporal memory and reduces dependence on a single frame's raw output.

### 6. Classify the score into red/yellow/green

Thresholds:

- `score < 0.01165` -> `green`
- `0.01165 <= score < 0.06157` -> `yellow`
- `score >= 0.06157` -> `red`

Because the score is the mean of five anti-diagonal values, these thresholds are operating on an averaged, history-aware risk measure, not directly on the original cumulative disengage probabilities.

## What the Score Means

The enum is best read as a coarse statement about the model's recent multi-frame confidence in its own near-term comfort/stability with respect to **driver intervention behavior**.

It does **not** directly mean:

- lane line confidence
- path certainty
- physical safety margin
- collision probability
- controller saturation
- ride comfort in a general human-factors sense

It is anchored specifically to model outputs trained around intervention-like behaviors:

- brake disengage
- gas disengage
- steer override

That is closer to:

- "How likely is the driver to intervene?"

than:

- "How dangerous is the scene physically?"

## Important Caveats

### It is history-dependent, not instantaneous

The current ball can react frame-to-frame after filtering. `modelV2.confidence` cannot, because its core state only advances every 2 seconds and averages across 5 such steps.

Practical consequence:

- it is slower and more stable
- it is less twitchy
- it can lag fast scene changes

### Warm-up behavior matters

The disengage buffer starts at zeros. Until several 2-second updates have occurred, the anti-diagonal is partially real data and partially zero padding.

Practical consequence:

- early after startup or after state reset, the score is biased toward `green`
- the signal becomes fully representative only after roughly 5 buffer updates
- at 2 seconds per update, that is about **10 seconds** to fully populate the diagonal

### It is only 3-state output

The enum throws away the continuous structure of the underlying score.

Practical consequence:

- very easy to read
- harder to animate expressively
- harder to use for nuanced position/height/size changes without additional derivation

### The combination assumes independence

`1 - ((1-brake) * (1-gas) * (1-steer))` assumes independence between the three intervention types at a given horizon.

In reality, these behaviors are correlated. So the union-style "any disengage" number should be treated as a pragmatic approximation, not a literal calibrated joint probability.

### The underlying task is behavioral, not physical

A high `modelV2.confidence` risk classification can occur because the model expects the driver to dislike or override the plan, even when the car is not in an objectively dangerous state.

Likewise, physically urgent situations may not map cleanly to this signal if they do not resemble the intervention patterns the model learned.

## How It Differs From the Current Confidence Ball

### Current ball in mici and `disengage_bars.py`

Source:

- `selfdrive/ui/mici/onroad/confidence_ball.py`
- `selfdrive/ui/onroad/disengage_bars.py`

Formula:

```python
(1 - max(brakeDisengageProbs)) * (1 - max(steerOverrideProbs))
```

Key characteristics:

- continuous scalar
- uses only brake + steer
- ignores gas disengage
- uses `max()` across the whole horizon, which effectively picks the 10s cumulative value
- filtered visually with a first-order filter
- drives:
  - color bands
  - ball height

### `modelV2.confidence`

Key characteristics:

- discrete enum
- uses brake + gas + steer
- converts cumulative probabilities into conditional 2-second slice risks
- uses a 5-snapshot anti-diagonal average
- no direct in-tree onroad UI consumer found

### Bottom line

The current ball answers:

- "What is the worst-case long-horizon brake/steer discomfort risk right now?"

`modelV2.confidence` answers something closer to:

- "How consistently risky has the model judged the approximate present moment, across multiple past forecasts and across all three intervention modes?"

Those are related, but they are not interchangeable.

## Related Signals Worth Understanding

These are the nearby fields most relevant to any redesign or deeper analysis.

### 1. `meta.disengagePredictions.*`

Fields:

- `brakeDisengageProbs`
- `gasDisengageProbs`
- `steerOverrideProbs`

These are the direct parents of both:

- the current confidence ball
- the `modelV2.confidence` enum

If you want the most interpretable detailed view, these arrays are the primary raw material.

Best for:

- separate bars
- horizon-specific diagnostics
- custom composite confidence metrics

### 2. `meta.hardBrakePredicted`

This is a separate boolean derived from hard-brake prediction heads, not from `modelV2.confidence`.

It is used for FCW-related logic and is much more event-like:

- high specificity when active
- binary only
- not a general confidence replacement

Best for:

- warning escalation
- highlighting severe braking predictions

### 3. `meta.engagedProb`

This is another meta-head output and should not be confused with the confidence enum.

It reflects the model's prediction about engagement behavior, not the same rolling anti-diagonal classification used by `modelV2.confidence`.

Best for:

- research
- possible future composite UI experiments

Not enough evidence in the current UI code suggests it is the right drop-in replacement for the confidence ball by itself.

### 4. `gasPressProbs` and `brakePressProbs`

These predict pedal press behavior at `[0, 2, 4, 6, 8, 10]` seconds.

Notably:

- `longitudinal_planner.py` reads `gasPressProbs[1]`
- these are consumed elsewhere more directly than `modelV2.confidence`

Best for:

- specific driver-intent interventions
- longitudinal interaction logic

### 5. `modelV2.acceleration`

This is unrelated to `modelV2.confidence` mathematically, but highly relevant if the UI goal is "how worried should I be?" rather than "how likely is a disengage?"

Best for:

- forward-looking braking or slowing intent
- continuous camera-based signal
- a BI-like predictive bar

### 6. Physical/controller signals

Examples:

- `carState.aEgo`
- `controlsState.lateralControlState.*.saturated`
- `carOutput.actuatorsOutput.torque`

These are not model-confidence signals, but they are often more actionable in a UI because they reflect what the car/controller is actually doing.

Best for:

- reactive bars
- control-limit visibility
- confirming that risk has turned into action

## Current Usage In This Tree

After searching the current repo:

- `modelV2.confidence` is defined in `cereal/log.capnp`
- computed in `selfdrive/modeld/fill_model_msg.py`
- mentioned in `DISENGAGE_BARS_RESEARCH.md`

I did **not** find an active onroad UI consumer that reads `modelV2.confidence` directly.

By contrast, the older confidence-ball logic is actively used in:

- `selfdrive/ui/mici/onroad/confidence_ball.py`
- `selfdrive/ui/onroad/disengage_bars.py`

So today, the enum is best viewed as:

- available message metadata
- useful for research or future UI work
- not the signal that currently drives the visible confidence ball

## Practical UI Options

If we want to use `modelV2.confidence`, there are several reasonable choices.

### Option A: Direct enum replacement

Use `modelV2.confidence` alone for the ball color:

- `green` -> green ball
- `yellow` -> yellow ball
- `red` -> red ball

Pros:

- simplest
- faithful to the producer's intended classification
- stable and easy to interpret

Cons:

- loses continuous motion/height richness unless we fake it
- slower to react than the old ball
- gives up some immediate interpretability about which underlying signal drove the change

Best if the main goal is:

- simple, stable state indication

### Option B: Hybrid enum color + old continuous position

Use:

- `modelV2.confidence` for color bands
- old brake/steer scalar for vertical position

Pros:

- keeps the readable motion of the old ball
- surfaces the newer classification without discarding the familiar feel

Cons:

- color and motion come from different semantics
- possible mismatch: a "high" ball could be yellow or red

Best if the main goal is:

- preserve current UI feel while adding the newer classifier

### Option C: Reconstruct a continuous score from the same pipeline

Instead of only reading the enum, reproduce the anti-diagonal score itself in the UI and render:

- color from thresholds
- position from raw score
- maybe extra markers from the component slices

Pros:

- semantically aligned with `modelV2.confidence`
- preserves continuous expressiveness
- most honest representation of the actual classifier internals

Cons:

- more implementation work
- requires duplicating or exposing producer-side state logic
- still inherits warm-up and 2-second cadence constraints

Best if the main goal is:

- a high-fidelity visualization of the real confidence algorithm

### Option D: Keep current ball and add enum as secondary badge/state

Leave the old ball unchanged, but show the enum as:

- a label
- a colored ring
- a small side indicator

Pros:

- no loss of current behavior
- adds new information without semantic overload
- easier to compare old and new systems during evaluation

Cons:

- more UI complexity
- potentially redundant if not designed carefully

Best if the main goal is:

- research and side-by-side validation

## Recommendation

If the purpose is research and signal understanding, the best next step is **not** an immediate full replacement.

The most defensible order is:

1. Keep the existing continuous ball behavior visible.
2. Add either a secondary `modelV2.confidence` indicator or an instrumentation mode.
3. Compare how often the enum disagrees with:
   - the old ball
   - individual `B/G/S` bars
   - `hardBrakePredicted`
   - actual disengagement/override moments

That avoids collapsing two genuinely different semantics into one UI element before we understand the tradeoff.

If a replacement is desired anyway, the cleanest long-term design is probably:

- expose the **raw anti-diagonal score** or recreate it in UI space
- use the enum thresholds for coarse classification
- preserve continuous rendering from the underlying score

That gives both interpretability and faithful alignment with the current producer logic.

## Most Important Takeaways

- `modelV2.confidence` is a discrete, history-aware classifier, not the scalar used by the current confidence ball.
- It combines brake, gas, and steer intervention predictions; the old ball uses only brake and steer.
- Its score is based on a rolling anti-diagonal across five 2-second snapshots, which makes it slower but more temporally grounded.
- The code comment says "independent" slice probabilities, but the actual math is conditional per-slice hazard-style conversion from cumulative probabilities.
- In the current tree, `modelV2.confidence` is produced but not visibly consumed by the onroad confidence widgets.
- For UI work, the real design question is not "old or new?" but whether we want:
  - an instantaneous continuous discomfort signal,
  - a slower 3-state temporal classifier,
  - or a hybrid of both.
