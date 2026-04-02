# Light Show Refactor Plan

## Architecture overview

The light show lives in two files:

| File | Lines | Role |
|---|---|---|
| `selfdrive/ui/mici/layouts/music_visualizer.py` | 985 | All visuals + audio analysis |
| `selfdrive/ui/mici/layouts/settings/settings.py` | 318 | Orchestration, state machine, playback |

`settings.py` is clean — it pumps beat/energy/hype state and calls `EyebrowBilly.draw()`.
The complexity lives almost entirely in `music_visualizer.py`, specifically `EyebrowBilly.draw()` (440 lines, lines 545–984).

### Call flow

```
run_ui.sh → ui.py → MiciMainLayout → SettingsLayout
  ├─ _start_eyebrow_dance()  → loads music, spawns AudioAnalysis thread, creates EyebrowBilly
  ├─ _update_state()          → pumps beat_flash, energy, hype, is_in_drop each frame
  └─ _render()                → calls EyebrowBilly.draw() with 11 parameters
```

### Classes in music_visualizer.py

| Class | Lines | Status |
|---|---|---|
| `AudioAnalysis` | 61–209 | Clean. FFT + beat detection via numpy. Runs in background thread. |
| `DancingFigure` | 221–421 | **Dead code.** Not imported by settings.py. 200 lines unused. |
| `EyebrowBilly` | 452–985 | **The problem.** 530 lines, single 440-line `draw()` method. |

---

## What EyebrowBilly.draw() does

It's a monolithic render method that handles 10+ visual layers with interleaved physics and rendering:

| Layer | Lines | Elements | Active during |
|---|---|---|---|
| Background light show | 576–587 | 1–2 rectangles | Post-intro only |
| Warp-speed streaks | 666–732 | 500 beams, 3 draw calls each | Intro only |
| Firework core flash | 739–745 | 1 circle | Intro (first 15%) |
| Firework sparks | 748–777 | 220 sparks, 4 draw calls each | Intro (first 45%) |
| Shrapnel | 780–806 | 300 shards, 3 draw calls each | Intro (first 45%) |
| Chaos circles | 816–836 | 40 orbs, 4–5 draw calls each | Intro (28%–68%) |
| Dot-matrix eyes | 842–855 | 32 dots (16 per eye), 1–4 draws each | Always |
| Mouth dots | 858–875 | 4 dots, 1–4 draws each | Always |
| Waveform eyebrows | 879–926 | 48-pt polyline × 2 sides, 3-pass glow + bars | Post-intro |
| Outro arch eyebrows | 931–954 | 12-segment arc × 2 sides | Outro only |
| Beat-burst particles | 956–984 | ~14 per kick, `list[dict]` with append/rebuild | Post-intro |

The `__init__` (lines 465–543) pre-computes ~1,060 random params across 4 seeded RNGs for intro effects.

---

## Problems found

### 1. Temporary Python object allocation (~6,600 objects/frame during intro)

Every `rl.draw_circle(...)` and `rl.draw_line_ex(...)` constructs `rl.Color(...)` and `rl.Vector2(...)` ctypes structs on the heap. In C these are zero-cost stack values. In Python each one hits the allocator.

Rough count during intro:
- Warp: ~3 Color + ~6 Vector2 per beam × 500 ≈ 4,500
- Sparks: ~4 Color × 220 ≈ 880
- Shrapnel: ~3 Color × 300 ≈ 900
- Chaos/dots/etc ≈ 350

This is the single biggest perf difference between C-raylib and Python-raylib.

### 2. ~3,650 trig calls per frame on fixed angles

Spark and shrapnel angles are constants set at init, but `math.cos(sa)` / `math.sin(sa)` are recomputed every frame. The warp loop at least caches into `sa_cos`/`sa_sin`, but the spark loop calls `math.cos(sa)` twice and `math.sin(sa)` twice per spark (lines 761–767) without caching.

All fixed-angle cos/sin values could be precomputed once at init as numpy arrays.

### 3. No update/draw separation

Physics (position, alpha, visibility) is computed inline inside the draw pass. Example: the warp loop computes 7 intermediate values (`pos`, `beam_delay`, `beam_collapse`, `beam_cap`, `blen`, `p_head`, `p_tail`) per beam before drawing. This prevents numpy vectorization — you can't batch-compute positions when they're interleaved with draw calls.

### 4. Multi-pass glow via repeated draw calls

Each element is drawn 3–4 times to simulate glow halos:
- Warp beams: glow aura (thick, 12% alpha) + main streak + bright tip = 3 passes × 500 = 1,500 draw calls
- Chaos circles: fill + 3 concentric `draw_circle_lines` + conditional hi-hat ring = 4–5 passes × 40 = 200
- Eyebrow polyline: 3-pass glow per segment × 47 segments × 2 sides = 282 draw calls

**Quirk:** `BLEND_ADDITIVE` works for overlapping elements (warp beams) but doesn't replicate the concentric ring halo on isolated dots. Those need either a pre-rendered glow sprite or a RenderTexture + bloom shader.

### 5. 440-line draw() with no decomposition

All timing logic (`if doing_intro`, `if intro_frac < 0.45`, `if outro_frac > 0.05`) is interleaved with rendering code. You can't reason about "when does this appear" separately from "how is it drawn." The `_intro_pos` closure is re-created every frame as a nested function inside `draw()`.

### 6. Parallel lists instead of structured arrays

The `__init__` stores particle properties as 7 separate Python lists per layer (e.g., `_warp_angle[500]`, `_warp_speed[500]`, `_warp_phase[500]`, ...). Each list index is a separate Python bounds-checked lookup. These should be structured numpy arrays.

### 7. Beat-burst particles use list[dict] with append/rebuild

Lines 963–984 use `self._particles.append({...})` and rebuild the alive list every frame (`alive = []; ... self._particles = alive`). This creates dict objects and triggers GC. The raylib particle pattern uses a fixed-size pool with an `active` flag.

### 8. DancingFigure is 200 lines of dead code

Defined at lines 221–421, never imported by settings.py. Appears to be an earlier iteration (stick figure) that was replaced by EyebrowBilly.

### 9. hsv_to_color called per-element

The warp loop calls `hsv_to_color` twice per beam (~1,000 calls during intro), each running a 6-branch if/elif chain. Many elements use the same hue — the conversion could be cached per-frame.

### 10. No adaptive quality / frame skipping

During the intro, all 500 warp beams + 220 sparks + 300 shrapnel render at full fidelity regardless of FPS. No `get_fps()` check to reduce counts when the frame budget is blown.

---

## PR plan

Ordered by simplification impact first (makes the code easier to maintain and understand), then performance.

### PR 1: Extract layers from EyebrowBilly.draw()

Split the 440-line `draw()` into self-contained layer classes. No behavior change.

- Create a `DrawContext` dataclass bundling the 11 draw parameters
- Extract each visual block into its own class with `draw(ctx)`:
  - `WarpField` (lines 666–732)
  - `Firework` (lines 734–807, covers sparks + shrapnel + core flash)
  - `ChaosOrbs` (lines 816–836)
  - `DotFace` (lines 838–875, covers eyes + mouth)
  - `WaveformBrows` (lines 877–926)
  - `OutroBrows` (lines 931–954)
  - `BeatParticles` (lines 956–984)
  - `BackgroundLightShow` (lines 576–587)
- Each layer gets a time envelope (`enter`/`exit` on `intro_frac`) so the sequencing logic is declarative, not scattered through `if` branches
- Move `_intro_pos` from a per-frame closure to a method on `DotFace`

**Impact:** 440-line method → ~8 classes of 40–60 lines each. Sequencing is readable. Each layer is independently testable.

### PR 2: Delete DancingFigure dead code

Remove lines 212–421 (the `DancingFigure` class and its constants `_DANCE_SPEED`, `_SWAY_SPEED`, `_PARTICLE_LIFE`). Verify no imports reference it.

**Impact:** −210 lines, zero risk.

### PR 3: Precompute fixed trig + convert parallel lists to numpy arrays

- In `__init__`, store cos/sin of all fixed angles as numpy arrays alongside the angles themselves:
  ```python
  self._warp_cos = np.cos(self._warp_angle)
  self._warp_sin = np.sin(self._warp_angle)
  ```
- Convert the 7 parallel Python lists per layer (angle, speed, delay, gravity, size, cos, sin) into a single structured numpy array or a 2D array with named columns
- In the spark/shrapnel loops, use the precomputed cos/sin instead of calling `math.cos(sa)` multiple times

**Impact:** Eliminates ~2,500 redundant trig calls per frame. Replaces 7 Python list lookups per element with one numpy row access.

### PR 4: Separate update() from draw()

For each extracted layer class (from PR 1):
- Add an `update(ctx)` method that computes positions, alphas, and visibility into arrays
- The `draw(ctx)` method reads those arrays and issues draw calls only

**Quirk:** This must happen after PR 1 (layers exist) and PR 3 (numpy arrays exist). The warp loop is the main target — its 7 intermediate values per beam become vectorized numpy ops.

**Impact:** Enables numpy vectorization. Makes profiling cleaner (you can measure update vs draw time separately).

### PR 5: Pool-based beat-burst particles

Replace `list[dict]` with a fixed-size numpy particle pool:
- Pre-allocate arrays for `x0, y0, vx, vy, born, hue` with `active` mask
- `emit()` finds dead slots, no append
- `update()` is vectorized numpy (position = x0 + vx*age, etc.)
- `draw()` iterates only active slots

Apply the same pattern to DancingFigure's particles if it's kept (currently dead code).

**Impact:** Eliminates per-frame dict allocation and list rebuild GC pressure.

### PR 6: Reduce temporary Color/Vector2 allocations

Strategies (pick based on profiling):
- Cache common colors (`WHITE_FULL`, `WHITE_HALF`, etc.) as module-level constants
- For the warp/spark/shrapnel loops where color varies only by alpha, cache the RGB part and create Color once per unique alpha
- For `hsv_to_color` calls with the same hue, cache the result per-frame
- Consider a small helper that reuses a single mutable Vector2 struct for draw calls (pyray may or may not support this — needs testing)

**Quirk:** pyray's `rl.Color` is a ctypes Structure. Whether Python reuses or copies them on pass-to-C depends on the binding. Profile before optimizing.

**Impact:** Potentially large reduction in allocator pressure, but needs measurement.

### PR 7: Glow via additive blending + glow sprite

- Wrap particle/beam drawing in `rl.begin_blend_mode(rl.BLEND_ADDITIVE)` / `rl.end_blend_mode()`
- For the warp beams: draw each beam once (not 3×). Additive overlap produces natural glow where beams cluster.
- For the dot halos: create a small pre-rendered glow texture (soft radial gradient PNG, ~32×32). Draw it as a sprite instead of 3–4 concentric `draw_circle_lines` calls per dot.
- For the eyebrow polyline: draw the line once with additive blending. Alternatively, draw to a RenderTexture and composite with a blur shader for real bloom.

**Quirk:** The current concentric ring glow at specific pixel radii (+0, +2, +4) has a precise look. A glow sprite will be softer/different. Test visually before committing.

**Impact:** Cuts draw calls by ~2–3× during intro. Simpler code per element.

### PR 8: Adaptive particle count

- Check `rl.get_fps()` each frame
- If below 30fps, halve the warp beam / spark / shrapnel counts (skip every other index)
- If below 20fps, skip chaos circles entirely

**Impact:** Prevents the intro from tanking FPS on weaker hardware. Low risk — the visual density of 250 beams vs 500 is barely noticeable.

---

## File reference

| Path | What it contains |
|---|---|
| `selfdrive/ui/mici/layouts/music_visualizer.py` | `AudioAnalysis`, `DancingFigure` (dead), `EyebrowBilly` |
| `selfdrive/ui/mici/layouts/settings/settings.py` | `SettingsLayout` — orchestration, music playback, state updates |
| `selfdrive/ui/ui.py` | Entry point, picks mici or tici layout |
| `selfdrive/ui/mici/layouts/main.py` | `MiciMainLayout`, hosts `SettingsLayout` |
| `music/better-now-audio.mp3` | The MP3 used by the visualizer |
| `selfdrive/assets/icons_mici/red_dome_button.png` | Button icon that triggers the light show |
