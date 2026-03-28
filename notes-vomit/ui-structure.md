# Raylib UI Architecture — openpilot (MICI / TICI / Offroad / Onroad)

## Table of Contents

- [1. High-Level Architecture](#1-high-level-architecture)
- [2. Entry Point & Window Initialization](#2-entry-point--window-initialization)
- [3. GuiApplication — The Render Core](#3-guiapplication--the-render-core)
- [4. Widget System Foundation](#4-widget-system-foundation)
- [5. UIState & Device — The Brains](#5-uistate--device--the-brains)
- [6. TICI Layout (2160x1080)](#6-tici-layout-2160x1080)
- [7. MICI Layout (536x240)](#7-mici-layout-536x240)
- [8. Onroad Rendering Pipeline](#8-onroad-rendering-pipeline)
- [9. MICI-Only Widgets](#9-mici-only-widgets)
- [10. Offroad / Onroad State Machine](#10-offroad--onroad-state-machine)
- [11. Raylib API Usage Catalog](#11-raylib-api-usage-catalog)
- [12. MICI vs TICI Comparison Matrix](#12-mici-vs-tici-comparison-matrix)
- [13. Asset & Font System](#13-asset--font-system)
- [14. File Map](#14-file-map)
- [15. DAC (Driver Alert Cluster)](#15-dac-driver-alert-cluster)

---

## 1. High-Level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        selfdrive/ui/ui.py                        │
│                      (entry point, main loop)                    │
├──────────────────────┬───────────────────────────────────────────┤
│  BIG_UI=1 (tici)     │  BIG_UI=0 (mici)                         │
│  MainLayout          │  MiciMainLayout                           │
│  (state machine)     │  (horizontal Scroller)                    │
├──────────────────────┴───────────────────────────────────────────┤
│                    system/ui/lib/application.py                   │
│                  GuiApplication (raylib wrapper)                  │
│           init_window / render loop / nav stack / fonts           │
├──────────────────────────────────────────────────────────────────┤
│                    system/ui/widgets/__init__.py                  │
│                  Widget (abstract base class)                     │
│         render() → _update_state() → _layout() → _render()      │
├──────────────────────────────────────────────────────────────────┤
│                         pyray (raylib)                            │
│              init_window / begin_drawing / draw_* / ...          │
└──────────────────────────────────────────────────────────────────┘
```

The UI runs as a single-threaded raylib application. Python bindings (`pyray`) wrap the C raylib library. The only C++ raylib usage is in `selfdrive/ui/installer/installer.cc`.

---

## 2. Entry Point & Window Initialization

**File:** `selfdrive/ui/ui.py`

```python
def main():
    config_realtime_process(0, 51)       # RT priority
    gui_app.init_window("UI")            # creates raylib window

    if BIG_UI:
        MainLayout()                     # tici: 2160x1080
    else:
        MiciMainLayout()                 # mici: 536x240

    for should_render in gui_app.render():
        ui_state.update()                # poll cereal, update state
```

**Screen Sizes:**
| Device | Resolution | `BIG` env | Layout Class |
|--------|-----------|-----------|-------------|
| TICI (comma three) | 2160 x 1080 | `1` | `MainLayout` |
| MICI (comma four) | 536 x 240 | `0` (default) | `MiciMainLayout` |

**Launch:** `run_ui.sh` handles build, replay, and launching. Defaults to MICI; pass `--tici` for TICI layout.

---

## 3. GuiApplication — The Render Core

**File:** `system/ui/lib/application.py`

Global singleton: `gui_app`

### Window Setup

```python
gui_app.init_window(title, fps=60)
# Sets MSAA 4x, optional vsync
# Loads all fonts (7 weights)
# Starts mouse polling thread (140Hz on device)
# Creates render texture if scaling needed
```

### Render Loop (generator)

Every frame in `gui_app.render()`:

```
1. Poll mouse events (PC: manual / device: threaded at 140Hz)
2. Skip if _should_render is False (screen off)
3. begin_texture_mode() or begin_drawing()
4. clear_background(BLACK)
5. rl_push_matrix() + rl_scalef() if scaled
6. Run all _nav_stack_ticks (callbacks registered by layouts)
7. Render top N widgets from nav stack:
   - MICI: top 2 widgets (allows overlay + base)
   - TICI: top 1 widget
8. rl_pop_matrix()
9. end_texture_mode() → begin_drawing() → draw_texture_pro() (with optional burn-in shader)
10. draw_fps() if SHOW_FPS
11. end_drawing()
12. yield should_render
```

### Nav Stack

The nav stack is the primary navigation mechanism:

- `push_widget(w)` — push widget, disable previous, enable new
- `pop_widget(idx)` — pop from stack
- `pop_widgets_to(w, callback)` — pop everything above target widget
- `widget_in_stack(w)` — check if widget is in stack
- `_nav_stack_widgets_to_render` — how many top widgets render simultaneously:
  - **MICI: 2** (base layout + overlay like settings)
  - **TICI: 1** (only topmost)
- `_nav_stack_ticks` — callbacks that fire every frame regardless of which widget is on top (used by `MiciMainLayout._handle_transitions`)

### Env Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `FPS` | 60 (20 for tizi) | Target framerate |
| `BIG` | `0` | `1` = TICI layout (2160x1080) |
| `SHOW_FPS` | `0` | Draw FPS counter |
| `SHOW_TOUCHES` | `0` | Draw touch points |
| `SCALE` | `1.0` | PC scale factor |
| `GRID` | `0` | Overlay grid |
| `RECORD` | `0` | Record frames to mp4 |
| `BURN_IN` | — | OLED burn-in prevention shader |
| `OFFSCREEN` | `0` | Skip FPS limiting |

---

## 4. Widget System Foundation

**File:** `system/ui/widgets/__init__.py`

### Base `Widget` Class (Abstract)

Every UI element inherits from `Widget`. The lifecycle:

```
render(rect)
  ├── _update_state()       # poll data, update internal state
  ├── _layout()             # compute child positions
  ├── _render(rect)         # ABSTRACT — draw with raylib
  ├── _process_mouse_events()  # handle touches/clicks
  └── [debug: draw_rectangle_lines]
```

**Key properties:**
- `_rect: rl.Rectangle` — position + size
- `_parent_rect` — clipping boundary (used by Scroller)
- `_children: list[Widget]` — registered children, lifecycle propagated
- `_enabled` / `_is_visible` — can be `bool` or `Callable[[], bool]`
- `_click_callback` — fired on release
- `_click_delay` — visual hold-after-release

**Mouse/Touch:**
- `MAX_TOUCH_SLOTS = 2` (multi-touch)
- `_handle_mouse_press(pos)`, `_handle_mouse_release(pos)`, `_handle_mouse_event(event)`
- Hit testing via `rl.check_collision_point_rec`

### Key Widget Subclasses

```
Widget (abc.ABC)
├── Label / UnifiedLabel          — text rendering with emoji, shimmer, scroll
├── Button / IconButton           — standard clickable (TICI)
├── Toggle                        — animated on/off switch (160x80)
├── BigButton / BigCircleButton   — card-style (MICI, 402x180 / 180x180)
├── BigToggle / BigParamControl   — toggle with Params persistence (MICI)
├── SliderBase / BigSlider        — slide-to-confirm (MICI)
├── Scroller                      — MICI horizontal snap-scroll
├── Scroller (tici)               — TICI vertical scroll
├── NavWidget / NavBar            — swipe-to-dismiss overlay
├── NavScroller                   — NavWidget + Scroller combo
├── Keyboard                      — TICI QWERTY grid
├── MiciKeyboard                  — MICI radial proximity keyboard
├── ListItem                      — TICI settings row
├── IconWidget                    — static icon
├── HtmlRenderer                  — basic HTML→raylib text
├── ConfirmDialog                 — TICI modal
├── CameraView                    — VisionIPC + GLSL shader
└── AugmentedRoadView             — camera + overlays
```

---

## 5. UIState & Device — The Brains

**File:** `selfdrive/ui/ui_state.py`

### UIState (singleton: `ui_state`)

Subscribes to 20 cereal services and maintains core state:

| Field | Type | Source |
|-------|------|--------|
| `started` | `bool` | `deviceState.started AND ignition` |
| `ignition` | `bool` | `pandaStates[*].ignitionLine/Can` |
| `status` | `UIStatus` | DISENGAGED / ENGAGED / OVERRIDE |
| `panda_type` | `PandaType` | First panda's type |
| `is_metric` | `bool` | `Params("IsMetric")` |
| `always_on_dm` | `bool` | `Params("AlwaysOnDM")` |
| `has_longitudinal_control` | `bool` | `CarParams` |
| `light_sensor` | `float` | `wideRoadCameraState.exposureValPercent` |
| `prime_state` | `PrimeState` | Subscription state |

**Key methods:**
- `is_onroad()` → `self.started`
- `is_offroad()` → `not self.started`
- `engaged` (property) → `started and selfdriveState.enabled`
- `update()` — called every frame: `sm.update(0)`, `_update_state()`, `_update_status()`, params refresh every 5s, `device.update()`

**Callbacks:**
- `_offroad_transition_callbacks` — fired when `started` changes
- `_engaged_transition_callbacks` — fired when `engaged` changes

### Device (singleton: `device`)

Manages screen brightness and wakefulness:

| Aspect | MICI | TICI |
|--------|------|------|
| Offroad brightness | 65 | 50 |
| Interactive timeout (ignition) | 5s | 10s |
| Interactive timeout (offroad) | 30s | 30s |

**Wakefulness flow:**
1. Touch or ignition-off → reset timeout
2. Timeout → fire `_interactive_timeout_callbacks`
3. No ignition + timed out → `set_display_power(False)`, `set_should_render(False)`

---

## 6. TICI Layout (2160x1080)

**File:** `selfdrive/ui/layouts/main.py`

```
MainLayout (Widget)
├── Sidebar (SIDEBAR_WIDTH px wide, left edge)
│   ├── Settings icon
│   ├── Flag/bookmark button
│   └── Status indicators
└── Active Layout (fills remaining width):
    ├── HOME:     HomeLayout
    │   ├── SetupWidget / PrimeWidget
    │   └── OffroadAlerts
    ├── SETTINGS: SettingsLayout
    │   ├── Device / Toggles / Software / Developer / Firehose
    │   └── [uses ListItem + Scroller(tici)]
    └── ONROAD:   AugmentedRoadView (tici)
        ├── CameraView (shader-based YUV render)
        ├── ModelRenderer (lane lines, path)
        ├── HudRenderer
        │   ├── ExpButton (192px, top-right)
        │   ├── Set speed box (always visible)
        │   └── Current speed + unit (center)
        ├── AlertRenderer (bottom rounded-rect boxes)
        └── DriverStateRenderer (3D face mesh, 192px)
```

### State Machine

Uses `MainState` enum (HOME=0, SETTINGS=1, ONROAD=2) — no scroller:

- **Onroad transition:** `ui_state.started` changes → `_set_mode_for_state()`
  - Started → hide sidebar, switch to ONROAD (immediate, no delay)
  - Stopped → show sidebar, switch to HOME
- **Settings:** pushed via sidebar callback, hides sidebar
- **Onroad click:** toggles sidebar visibility

### Screen Layout (TICI)

```
┌─────────────┬──────────────────────────────────────────┐
│             │                                          │
│   Sidebar   │          Content Area                    │
│  (SW wide)  │   (2160 - SW) x 1080                    │
│             │                                          │
│  [settings] │   HomeLayout / SettingsLayout /          │
│  [bookmark] │   AugmentedRoadView                     │
│  [status]   │                                          │
│             │                                          │
└─────────────┴──────────────────────────────────────────┘
```

When sidebar hidden (onroad): content fills full 2160x1080.

### TICI Onroad Border

Color-coded border around the camera view (`UI_BORDER_SIZE = 30px`):

| Status | Color | Hex |
|--------|-------|-----|
| DISENGAGED | Blue | `#122839` |
| OVERRIDE | Gray | `#89928D` |
| ENGAGED | Green | `#167F40` |

```python
rl.draw_rectangle_lines_ex(rect, UI_BORDER_SIZE, BLACK)     # outer black
rl.draw_rectangle_rounded_lines_ex(border_rect, 0.12, 10, UI_BORDER_SIZE, border_color)
```

---

## 7. MICI Layout (536x240)

**File:** `selfdrive/ui/mici/layouts/main.py`

```
MiciMainLayout (Scroller, horizontal snap)
├── [page 0] MiciOffroadAlerts (vertical Scroller)
│   └── AlertItem[] (520x212/240/324 cards)
├── [page 1] MiciHomeLayout
│   ├── "openpilot" title (96pt DISPLAY font)
│   ├── Version / branch / commit / date labels
│   └── Status bar: [settings gear] [network] [exp mode] [mic]
├── [page 2] AugmentedRoadView (mici)
│   ├── CameraView (engaged/enhance_driver shader uniforms)
│   ├── ModelRenderer (status-colored lanes, wider lines)
│   ├── HudRenderer
│   │   ├── Steering wheel (50x50, rotates with angle)
│   │   ├── TurnIntent (animated arrow)
│   │   ├── TorqueBar (curved arc at bottom)
│   │   └── Set speed circle (top-left, fades after 2.5s)
│   ├── AlertRenderer (top-gradient overlay, animated)
│   ├── DriverStateRenderer (60px texture DMoji)
│   ├── ConfidenceBall (right 60px panel)
│   └── SidePanelActionTray
│       ├── Bookmark action (tap after reveal)
│       └── DAC action (tap after reveal)
│
└── [pushed to nav stack on demand]:
    ├── OnboardingWindow
    │   ├── TermsPage (QR code for comma.ai/terms)
    │   ├── TrainingGuideAttentionNotice (scrollable cards)
    │   ├── TrainingGuidePreDMTutorial
    │   ├── TrainingGuideDMTutorial (live camera + progress ring)
    │   └── TrainingGuideRecordFront (data consent)
    └── SettingsLayout (NavScroller)
        ├── TogglesLayoutMici
        ├── NetworkLayoutMici → WifiUIMici
        ├── DeviceLayoutMici
        ├── DeveloperLayoutMici
        ├── PairingDialog (QR code)
        └── FirehoseLayout
```

### Horizontal Scroller Navigation

Unlike TICI's state machine, MICI uses a **horizontal snap-scrolling Scroller** with 3 pages. Users swipe left/right between alerts, home, and onroad views.

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Horizontal Scroller (snap)                        │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┬─────────┐ │
│  │  Offroad  │  │   Home   │  │     Camera + Overlays    │ Side    │ │
│  │  Alerts   │  │  Screen  │  │       476 x 240          │ Panel   │ │
│  │ 536x240   │  │ 536x240  │  │                          │  60px   │ │
│  └──────────┘  └──────────┘  └──────────────────────────┴─────────┘ │
│                                                                      │
│       ← swipe →                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Transition Logic

```python
ONROAD_DELAY = 2.5  # seconds

# Registered as nav_stack_tick — runs EVERY frame
def _handle_transitions():
    if ui_state.started != _prev_onroad:
        if ui_state.started:
            _onroad_time_delay = rl.get_time()      # start 2.5s timer
        else:
            scroll_to(home_layout)                   # immediate scroll home

    # After 2.5s delay, pop nav stack and scroll to onroad
    if _onroad_time_delay and rl.get_time() - _onroad_time_delay >= ONROAD_DELAY:
        gui_app.pop_widgets_to(self, callback=lambda: scroll_to(onroad_layout))

    # Standstill → moving: immediately go to onroad
    if not carState.standstill and prev_standstill:
        gui_app.pop_widgets_to(self, callback=lambda: scroll_to(onroad_layout))
```

**Interactive timeout:**
- Onroad + moving → pop to self, scroll to onroad
- Onroad + standstill → do nothing (stay in settings/home)
- Offroad → pop instantly, scroll to home, screen turns off

### First-Frame Setup

On first render, scrolls to alerts page if there are active alerts, otherwise scrolls to home page (offset = `_rect.width` = 536px).

---

## 8. Onroad Rendering Pipeline

### MICI AugmentedRoadView Render Order

**File:** `selfdrive/ui/mici/onroad/augmented_road_view.py`

```
_render(rect):
  1. _switch_stream_if_needed()
     - Wide cam when vEgo < 5 m/s (experimental mode)
     - Road cam when vEgo > 10 m/s
     - Hysteresis zone between

  2. _update_calibration()
     - rot_from_euler(rpyCalib)
     - view_from_calib = view_frame_from_device @ device_from_calib

  3. _content_rect = (rect.x, rect.y, rect.width - 60, rect.height)
     → 476 x 240 camera area, 60px side panel reserved

  4. rl.begin_scissor_mode(content_rect)   ← clip to camera area

  5. super()._render(content_rect)         ← CameraView: YUV shader render
     - MICI shader adds: engaged desaturation, contrast, gamma
     - Disengaged: 85% brightness
     - Driver enhance: brightness boost + S-curve tonemap

  6. _model_renderer.render(content_rect)  ← lane lines + path
     - Lane colors: green (confirmed) / white (default) / gray (unclear)
     - Orange blend on high torque
     - Path hidden when DISENGAGED
     - Wider lines: factor 0.12-0.16 (vs TICI 0.025)

  7. draw_texture_ex(fade_texture)         ← gradient fade overlay

  8. _driver_state_renderer.render()       ← DMoji at (x+16, y+10)
     - Texture-based: dm_background, dm_person, dm_cone, dm_center
     - 60px base size
     - Cone rotates with head angle
     - Hidden when disengaged (unless AlwaysOnDM)
     - Hidden when HUD top icons showing

  9. _alert_renderer.render(content_rect)  ← driving alerts (only if started)
     - Top-gradient overlay style
     - Animated slide in/out (BounceFilter + FirstOrderFilter)
     - Turn signal icons (blinking at Mazda heartbeat: 80/60 BPM)
     - Adaptive font: 92px → 64px based on text length
     - Lowercase text

  10. _hud_renderer.render(content_rect)    ← HUD elements
      - TorqueBar: curved arc at bottom (radius 1200px, span 12.7°)
      - Steering wheel: bottom-left (50x50), rotates with angle
      - TurnIntent: animated arrow during lane changes
      - Set speed: top-left circle with gradient shadow, fades after 2.5s

  11. draw_rectangle_rounded_lines_ex(content_rect, 0.2, 10, 50, BLACK)
      → fake rounded border on camera area

  12. rl.end_scissor_mode()                  ← end camera clip

  13. _confidence_ball.render(full_rect)     ← right panel (outside scissor)
      - Colored dot: green (>0.5) / yellow (>0.2) / red (<0.2)
      - White during override, dark gray when disengaged
      - Vertical position = (1 - confidence) * panel_height

  14. _bookmark_icon.render(full_rect)       ← right panel (outside scissor)
      - Swipe-from-right gesture
      - State machine: HIDDEN → DRAGGING → TRIGGERED
      - Threshold: 50px swipe left triggers bookmark
      - Stays visible 1.5s after trigger

  15. if not started:                        ← offroad overlay
      draw_rectangle(full_rect, Color(0,0,0,175))  ← darken
      _offroad_label.render(rect)            ← "start the car to use openpilot"
```

### TICI AugmentedRoadView Render Order

**File:** `selfdrive/ui/onroad/augmented_road_view.py`

```
_render(rect):
  1. if not ui_state.started: return         ← TICI skips render entirely when offroad

  2. _switch_stream_if_needed()
     - Wide cam when vEgo < 10 m/s
     - Road cam when vEgo > 15 m/s

  3. _content_rect = (x+30, y+30, w-60, h-60)   ← UI_BORDER_SIZE=30 inset

  4. rl.begin_scissor_mode(content_rect)

  5. super()._render(rect)                    ← CameraView
     - Simple gamma shader (pow(1/1.28) on TICI)

  6. model_renderer.render(content_rect)      ← white-only lane lines
  7. _hud_renderer.render(content_rect)       ← ExpButton + speed
  8. alert_renderer.render(content_rect)      ← bottom rounded-rect boxes
  9. driver_state_renderer.render(content_rect) ← 3D spline face mesh

  10. rl.end_scissor_mode()

  11. _draw_border(rect)                       ← colored border
```

### Camera View Shader Differences

| Feature | MICI | TICI |
|---------|------|------|
| Base conversion | YUV → RGB | YUV → RGB |
| Engaged effect | Desaturate to 20%, +20% contrast, gamma | Simple gamma (1/1.28) |
| Disengaged effect | 85% brightness | None |
| Driver enhance | Brightness boost, contrast, S-curve tonemap | None |
| Shader uniforms | `engaged`, `enhance_driver` | — |
| GLSL version | `300 es` (device) / `330 core` (macOS) | Same |

---

## 9. MICI-Only Widgets

These widgets exist only in the MICI UI and have no TICI equivalent:

### ConfidenceBall

**File:** `selfdrive/ui/mici/onroad/confidence_ball.py`

A colored dot in the 60px right panel. Vertical position maps to model confidence:
- **Green** (>0.5 confidence)
- **Yellow** (>0.2)
- **Red** (<0.2)
- **White** (override)
- **Dark gray** (disengaged)

Uses `rl.draw_circle_gradient()`.

### TorqueBar

**File:** `selfdrive/ui/mici/onroad/torque_bar.py`

Curved arc at the bottom of the onroad view showing steering torque utilization:
- Arc radius: 1200px
- Angular span: 12.7°
- Gradient: white → yellow → orange as torque increases
- Accounts for lateral acceleration, roll compensation, curvature
- Uses `arc_bar_pts()` with quantized LRU cache for polygon generation

### SidePanelActionTray

**File:** `selfdrive/ui/mici/onroad/action_tray.py`

Single reveal controller for side-panel actions:
- State machine: `COLLAPSED → PEEKING → EXPANDED`
- Swipe left from anywhere on the onroad view first peeks the bookmark action
- Crossing the reveal threshold latches the tray open and reveals both actions
- Actions are **tap-only** once revealed
- Tap outside the actions collapses the tray
- Uses `BounceFilter` to animate reveal offset
- Reports left-swipe state the same way the original bookmark gesture did

### Bookmark Action

**File:** `selfdrive/ui/mici/onroad/action_tray.py`

- Tap target inside `SidePanelActionTray`
- Uses the existing bookmark bubble asset
- On tap: publishes the `bookmarkButton` cereal message and collapses the tray

### DAC Action

**File:** `selfdrive/ui/mici/onroad/action_tray.py`

- Tap target inside `SidePanelActionTray`
- Uses the DAC circular button background plus a mode-dependent icon:
  `dac-btn` when the road view is active, `onroad-ui-icon` when DAC is active
- On tap: toggles `AugmentedRoadView._dac_active` and collapses the tray

### TurnIntent

**File:** embedded in `selfdrive/ui/mici/onroad/hud_renderer.py`

Animated arrow icon during lane changes:
- Rotates based on `preLaneChangeLeft/Right` events
- Alpha animation via filter
- Drawn around the steering wheel icon

### MiciKeyboard

**File:** `system/ui/widgets/mici_keyboard.py`

Radial/proximity-based key selection — keys animate toward the touch point:
- Keys float and move toward finger
- Selected key zooms up with scale animation
- Texture-based background
- Much more fluid/animated than TICI's grid QWERTY

### BigButton / BigCircleButton Family

**File:** `selfdrive/ui/mici/widgets/button.py`

Card-style widgets for the MICI settings and dialogs:
- `BigCircleButton` — 180x180 round, texture backgrounds, bounce scale animation
- `BigButton` — 402x180 rectangular, label + sub-label + icon
- `BigToggle` — adds toggle pill icon
- `BigParamControl` — auto-persists to `Params`
- `GreyBigButton` — 476px wide, grey translucent, non-interactive info card
- All use textures from `icons_mici/buttons/`

### BigSlider / RedBigSlider

**File:** `system/ui/widgets/slider.py`

Slide-to-confirm widgets for MICI:
- Texture-based track background
- Drag circle from right to left to confirm
- Shimmer label text animation
- `RedBigSlider` variant for destructive actions

---

## 10. Offroad / Onroad State Machine

### State Definition

```python
# selfdrive/ui/ui_state.py
started = sm["deviceState"].started and ignition
is_onroad()  = started       # True when car ignition on + system started
is_offroad() = not started   # True otherwise
```

### Transition Flow

```
                  ignition ON + deviceState.started
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
         OFFROAD                  ONROAD
    ┌─────────────────┐    ┌─────────────────────┐
    │ Home screen     │    │ Camera + overlays    │
    │ Offroad alerts  │    │ Model + HUD          │
    │ Settings        │    │ Alerts               │
    │ Onboarding      │    │ Driver monitoring    │
    └────────┬────────┘    └──────────┬──────────┘
             │                        │
             │    selfdriveState      │
             │    .enabled            │
             │         │              │
             │    ┌────┴────┐         │
             │    ▼         ▼         │
             │ DISENGAGED ENGAGED     │
             │            /OVERRIDE   │
             └────────────────────────┘
```

### MICI Transition Behavior

| Event | Action |
|-------|--------|
| Car starts (ignition on) | After 2.5s delay, pop nav stack + scroll to onroad |
| Car stops (ignition off) | Immediately scroll to home (don't pop settings) |
| Standstill → moving | Pop nav stack + scroll to onroad |
| Interactive timeout (onroad, moving) | Pop to self + scroll to onroad |
| Interactive timeout (onroad, standstill) | No action (stay in settings/home) |
| Interactive timeout (offroad) | Pop instantly + scroll to home + screen off |

### TICI Transition Behavior

| Event | Action |
|-------|--------|
| Car starts | Hide sidebar, switch to ONROAD mode (immediate) |
| Car stops | Show sidebar, switch to HOME mode |
| Onroad click | Toggle sidebar visibility |
| Interactive timeout | Call `_set_mode_for_state()` |

### Process-Level Offroad/Onroad

From `system/manager/process_config.py`, processes are tagged:
- `only_onroad` — started only when `started=True` (e.g. controlsd, modeld)
- `only_offroad` — started only when `started=False` (e.g. updater)
- Neither — always running (e.g. ui, manager)

---

## 11. Raylib API Usage Catalog

### Window & Loop

| API | Where Used |
|-----|-----------|
| `rl.init_window(w, h, title)` | `application.py`, `installer.cc` |
| `rl.close_window()` | `application.py` |
| `rl.window_should_close()` | `application.py` render loop |
| `rl.set_config_flags(MSAA_4X)` | `application.py` |
| `rl.set_target_fps(fps)` | `application.py` |
| `rl.begin_drawing()` / `end_drawing()` | `application.py` render loop |
| `rl.clear_background(BLACK)` | Every frame |

### Drawing Primitives

| API | Primary Users |
|-----|--------------|
| `rl.draw_rectangle_rounded()` | `Button`, `Toggle`, `AlertRenderer` |
| `rl.draw_rectangle_rounded_lines_ex()` | `AugmentedRoadView` border, TICI border |
| `rl.draw_rectangle_rec()` | `CameraView` placeholder |
| `rl.draw_rectangle()` | Offroad overlay darkening |
| `rl.draw_rectangle_gradient_h()` | HUD header gradient |
| `rl.draw_circle()` | Touch debug, `Toggle` knob |
| `rl.draw_circle_gradient()` | `ConfidenceBall`, set speed shadow |
| `rl.draw_line()` / `draw_line_ex()` | Grid overlay, separator lines |
| `rl.draw_triangle_fan()` | `ModelRenderer` lane/path polygons |
| `rl.draw_spline_linear()` | TICI `DriverStateRenderer` face mesh |

### Text

| API | Where Used |
|-----|-----------|
| `rl.draw_text_ex()` | Monkey-patched in `application.py` for font scaling. Used everywhere via `Label`/`UnifiedLabel`. |
| `rl.measure_text_ex()` | Cached in `text_measure.py` |
| `rl.gui_label()` | raygui labels |
| `rl.gui_set_font()` / `gui_set_style()` | `application.py` init |

### Textures & Images

| API | Where Used |
|-----|-----------|
| `rl.load_font_ex()` | `application.py` font loading |
| `rl.load_image()` / `image_resize()` | `application.py` texture loading |
| `rl.load_texture_from_image()` | `application.py`, `replay/ui.py` |
| `rl.gen_texture_mipmaps()` | Font texture filtering |
| `rl.set_texture_filter(BILINEAR)` | Font + icon textures |
| `rl.draw_texture_ex()` | action tray buttons, fade overlay, icons |
| `rl.draw_texture_pro()` | Scaled blit, steering wheel rotation, camera render |
| `rl.draw_texture_v()` | Simple positioned textures |
| `rl.update_texture()` | `replay/ui.py` live frame update |
| `rl.unload_texture()` | `application.py` cleanup |

### Shaders

| API | Where Used |
|-----|-----------|
| `rl.load_shader_from_memory()` | `CameraView` (YUV→RGB), `application.py` (burn-in) |
| `rl.begin_shader_mode()` / `end_shader_mode()` | Camera render, burn-in |
| `rl.set_shader_value()` | Shader uniforms (engaged, enhance_driver) |

### Render Textures

| API | Where Used |
|-----|-----------|
| `rl.load_render_texture()` | `application.py` (scaling, recording) |
| `rl.begin_texture_mode()` / `end_texture_mode()` | `application.py` render loop |
| `rl.load_image_from_texture()` | Recording frames |

### Scissor (Clipping)

| API | Where Used |
|-----|-----------|
| `rl.begin_scissor_mode()` | `AugmentedRoadView`, `Scroller`, `HtmlRenderer`, `NavWidget` |
| `rl.end_scissor_mode()` | Paired with above |

### Transform Matrix

| API | Where Used |
|-----|-----------|
| `rl.rl_push_matrix()` / `rl_pop_matrix()` | `application.py` scaling, `Scroller` zoom |
| `rl.rl_scalef()` | PC scale factor |
| `rl.rl_translatef()` | Scroller scroll offset |

### Input

| API | Where Used |
|-----|-----------|
| `rl.poll_input_events()` | Mouse thread (140Hz on device) |
| `rl.get_touch_position()` | `application.py` input handling |
| `rl.is_mouse_button_pressed/down/released()` | `application.py` MouseState |
| `rl.set_mouse_scale()` | PC scaling |

### Timing

| API | Where Used |
|-----|-----------|
| `rl.get_time()` | Transition delays, animation timing, click delays |
| `rl.get_frame_time()` | Animation delta time |
| `rl.get_fps()` | FPS monitoring, panda timeout, filter dt |

---

## 12. MICI vs TICI Comparison Matrix

| Aspect | MICI (comma four) | TICI (comma three) |
|--------|-------------------|-------------------|
| **Resolution** | 536 x 240 | 2160 x 1080 |
| **Root layout** | `MiciMainLayout(Scroller)` | `MainLayout(Widget)` |
| **Navigation** | Horizontal snap scroller (3 pages) | State machine (HOME/SETTINGS/ONROAD) |
| **Sidebar** | None (status bar on home screen) | Left sidebar (always present offroad) |
| **Onroad transition** | 2.5s delay + scroll animation | Immediate state switch |
| **Offroad rendering** | Camera shows with darkened overlay | Camera does not render at all |
| **Camera content area** | 476x240 (minus 60px side panel) | 2100x1020 (minus 2x30px border) |
| **Border style** | Black rounded lines (50px thick, no color) | Color-coded lines (blue/gray/green, 30px) |
| **Speed display** | No current speed on screen | Center-screen speed + unit |
| **Set speed** | Top-left circle, fades after 2.5s | Always-visible box |
| **ExpButton** | None (long-press home to toggle) | 192px button, top-right |
| **Steering wheel** | 50x50 rotating icon, bottom-left | None |
| **Torque bar** | Curved arc at bottom | None |
| **Confidence ball** | Right panel dot, color-coded | None |
| **Bookmark** | Swipe-from-right gesture | Sidebar flag button |
| **Lane lines** | Status-colored (green/white/gray), 0.12-0.16 width | White only, 0.025 width |
| **Path** | Hidden when disengaged | Always drawn |
| **Lead indicator** | Commented out | Active |
| **Alerts (onroad)** | Top-gradient overlay, animated slide, turn signals | Bottom rounded-rect boxes, fixed positions |
| **Alert font** | 16-82px, lowercase | 66-177px |
| **DMoji** | 60px texture-based (cone/dot) | 192px 3D spline face mesh |
| **Camera switch speeds** | Wide < 5 m/s, Road > 10 m/s | Wide < 10 m/s, Road > 15 m/s |
| **Camera shader** | Engaged grading, driver enhance | Simple gamma only |
| **Keyboard** | Radial proximity-based | Grid QWERTY |
| **Settings items** | BigButton/BigToggle cards in horizontal scroller | ListItem rows in vertical scroller |
| **Confirmation** | Slide-to-confirm (BigSlider) | Modal dialog (ConfirmDialog) |
| **Pairing** | Dark background + QR, NavWidget dismiss | Light background, two-column, IconButton close |
| **Nav stack render count** | 2 (base + overlay) | 1 (topmost only) |
| **Interactive timeout** | 5s (ignition) / 30s (offroad) | 10s (ignition) / 30s (offroad) |
| **Offroad brightness** | 65 | 50 |
| **FPS default** | 60 | 60 (tizi: 20) |
| **Assets directory** | `icons_mici/` | `icons/` |
| **Fonts** | + DISPLAY, DISPLAY_REGULAR, ROMAN, SEMI_BOLD | NORMAL, MEDIUM, BOLD |
| **Font scale** | 1.16 | 1.242 |
| **Camera zoom** | Wide: 0.7*1.5, Road: interp(vEgo, [10,30], [0.8,1.0]) | Wide: 2.0, Road: 1.1 |

---

## 13. Asset & Font System

### Font Weights

```python
class FontWeight(StrEnum):
    NORMAL = "normal"
    MEDIUM = "medium"
    BOLD = "bold"
    SEMI_BOLD = "semi_bold"
    UNIFONT = "unifont"
    DISPLAY_REGULAR = "display_regular"
    ROMAN = "roman"
    DISPLAY = "display"
```

Font files loaded from `selfdrive/assets/fonts/`. The `process.py` script generates `.fnt` bitmap fonts from TTF files.

**Font scaling:**
- `FONT_SCALE = 1.242` (TICI) or `1.16` (MICI)
- Applied via monkey-patched `rl.draw_text_ex()` in `application.py`

### Texture Loading

```python
gui_app.texture(path, w=None, h=None)
```

- Loads from `selfdrive/assets/` (relative paths)
- Caches by `(path, w, h)` tuple
- Auto-scales dimensions for MICI (`w * width / 2160`)
- Applies bilinear texture filtering

### MICI-Specific Assets

```
selfdrive/assets/icons_mici/
├── buttons/          # BigButton/BigCircleButton textures
├── onroad/           # bookmark.png, onroad_fade.png
├── setup/            # StartPage textures
├── settings/
│   └── keyboard/     # MiciKeyboard textures
└── ...
```

---

## 14. File Map

### Core Framework

| File | Role |
|------|------|
| `selfdrive/ui/ui.py` | Entry point, main loop |
| `selfdrive/ui/ui_state.py` | UIState singleton, Device singleton |
| `system/ui/lib/application.py` | GuiApplication (raylib wrapper) |
| `system/ui/widgets/__init__.py` | Base Widget class |

### TICI UI

| File | Role |
|------|------|
| `selfdrive/ui/layouts/main.py` | MainLayout (state machine root) |
| `selfdrive/ui/layouts/home.py` | HomeLayout |
| `selfdrive/ui/layouts/sidebar.py` | Sidebar |
| `selfdrive/ui/layouts/onboarding.py` | Onboarding flow |
| `selfdrive/ui/layouts/settings/settings.py` | Settings container |
| `selfdrive/ui/layouts/settings/device.py` | Device settings |
| `selfdrive/ui/layouts/settings/toggles.py` | Toggle settings |
| `selfdrive/ui/layouts/settings/software.py` | Software settings |
| `selfdrive/ui/layouts/settings/developer.py` | Developer settings |
| `selfdrive/ui/layouts/settings/firehose.py` | Firehose settings |
| `selfdrive/ui/onroad/augmented_road_view.py` | TICI onroad camera + overlays |
| `selfdrive/ui/onroad/cameraview.py` | TICI camera renderer |
| `selfdrive/ui/onroad/model_renderer.py` | TICI lane lines + path |
| `selfdrive/ui/onroad/hud_renderer.py` | TICI HUD (speed, ExpButton) |
| `selfdrive/ui/onroad/alert_renderer.py` | TICI driving alerts |
| `selfdrive/ui/onroad/driver_state.py` | TICI driver face mesh |
| `selfdrive/ui/onroad/exp_button.py` | Experimental mode button |
| `selfdrive/ui/onroad/driver_camera_dialog.py` | TICI driver camera preview |
| `selfdrive/ui/widgets/offroad_alerts.py` | TICI offroad alerts |
| `selfdrive/ui/widgets/prime.py` | Prime subscription widget |
| `selfdrive/ui/widgets/setup.py` | Setup/firehose widget |
| `selfdrive/ui/widgets/pairing_dialog.py` | TICI pairing QR |
| `selfdrive/ui/widgets/ssh_key.py` | SSH key management |
| `selfdrive/ui/widgets/exp_mode_button.py` | Experimental mode gradient button |

### MICI UI

| File | Role |
|------|------|
| `selfdrive/ui/mici/layouts/main.py` | MiciMainLayout (scroller root) |
| `selfdrive/ui/mici/layouts/home.py` | MiciHomeLayout + NetworkIcon |
| `selfdrive/ui/mici/layouts/offroad_alerts.py` | MiciOffroadAlerts + AlertItem |
| `selfdrive/ui/mici/layouts/onboarding.py` | MICI onboarding flow |
| `selfdrive/ui/mici/layouts/settings/settings.py` | MICI settings menu |
| `selfdrive/ui/mici/layouts/settings/device.py` | MICI device settings |
| `selfdrive/ui/mici/layouts/settings/toggles.py` | MICI toggle settings |
| `selfdrive/ui/mici/layouts/settings/developer.py` | MICI developer settings |
| `selfdrive/ui/mici/layouts/settings/firehose.py` | MICI firehose settings |
| `selfdrive/ui/mici/layouts/settings/network/` | MICI WiFi UI |
| `selfdrive/ui/mici/onroad/action_tray.py` | MICI side-panel action tray |
| `selfdrive/ui/mici/onroad/augmented_road_view.py` | MICI onroad camera + overlays + DAC view switching |
| `selfdrive/ui/mici/onroad/cameraview.py` | MICI camera (engaged shader) |
| `selfdrive/ui/mici/onroad/model_renderer.py` | MICI lane lines (colored, wider) |
| `selfdrive/ui/mici/onroad/hud_renderer.py` | MICI HUD (wheel, torque, turn intent) |
| `selfdrive/ui/mici/onroad/alert_renderer.py` | MICI alerts (top gradient) |
| `selfdrive/ui/mici/onroad/driver_state.py` | MICI DMoji (texture-based) |
| `selfdrive/ui/mici/onroad/confidence_ball.py` | Model confidence indicator |
| `selfdrive/ui/mici/onroad/torque_bar.py` | Steering torque arc |
| `selfdrive/ui/mici/onroad/__init__.py` | Constants + blend_colors helper |
| `selfdrive/ui/mici/widgets/button.py` | BigButton/BigCircleButton family |
| `selfdrive/ui/mici/widgets/dialog.py` | BigDialog/BigConfirmationDialog |
| `selfdrive/ui/mici/widgets/pairing_dialog.py` | MICI pairing QR |

### Shared Widgets (`system/ui/widgets/`)

| File | Role |
|------|------|
| `button.py` | Button, ButtonRadio, IconButton |
| `label.py` | Label, UnifiedLabel, gui_label |
| `toggle.py` | Toggle (animated switch) |
| `slider.py` | BigSlider, RedBigSlider |
| `scroller.py` | MICI horizontal Scroller, NavScroller |
| `scroller_tici.py` | TICI vertical Scroller |
| `nav_widget.py` | NavWidget, NavBar (swipe dismiss) |
| `keyboard.py` | TICI Keyboard |
| `mici_keyboard.py` | MICI Keyboard (radial) |
| `list_view.py` | ListItem + action widgets |
| `layouts.py` | HBoxLayout |
| `icon_widget.py` | IconWidget |
| `html_render.py` | HtmlRenderer |
| `confirm_dialog.py` | ConfirmDialog (TICI modal) |
| `option_dialog.py` | MultiOptionDialog |
| `network.py` | TICI WiFi UI |
| `inputbox.py` | InputBox (text field) |

### System UI Apps

| File | Role |
|------|------|
| `system/ui/mici_setup.py` | MICI first-boot setup flow |
| `system/ui/tici_setup.py` | TICI first-boot setup flow |
| `system/ui/mici_updater.py` | MICI OTA updater |
| `system/ui/mici_reset.py` | MICI factory reset |
| `system/ui/tici_updater.py` | TICI OTA updater |
| `system/ui/tici_reset.py` | TICI factory reset |

### C++ (Installer Only)

| File | Role |
|------|------|
| `selfdrive/ui/installer/installer.cc` | C++ raylib installer (MICI: 536x240, TICI: 2160x1080) |

### Libraries (`system/ui/lib/`)

| File | Role |
|------|------|
| `application.py` | GuiApplication core |
| `scroll_panel.py` | TICI scroll panel (raygui) |
| `scroll_panel2.py` | MICI scroll panel (custom) |
| `shader_polygon.py` | Shader-based polygon fill |
| `text_measure.py` | Cached text measurement |
| `wrap_text.py` | Text wrapping |
| `emoji.py` | Emoji rendering support |
| `utils.py` | UI utilities |
| `egl.py` | EGL context management |
| `wifi_manager.py` | WiFi management |
| `networkmanager.py` | NetworkManager D-Bus |
| `multilang.py` | Multilanguage support |

---

## 15. DAC (Driver Alert Cluster)

### Overview

DAC is a MICI-only alternate onroad view exposed through a side-panel action tray. The user swipes left from anywhere on the onroad view to reveal the tray, then taps the DAC action to switch from the road camera to the DAC bento view.

### Side Panel Layout (60px strip, right edge)

```
┌────────────────────────────────────────────────┬──────────┐
│                                                │          │
│          Road camera / DAC view                │  Conf.   │
│               476 x 240                        │   Ball   │
│                                                │          │
│                              Bookmark   DAC    │  swipe   │
│                              (tap)      (tap)  │  reveal  │
│                                                │   edge   │
│                                                │          │
│                                                │          │
└────────────────────────────────────────────────┴──────────┘
```

- **ConfidenceBall** — always visible, top of panel (existing)
- **SidePanelActionTray** — invisible right-edge grab zone + reveal animation controller
- **Bookmark action** — tap target inside the tray
- **DAC action** — tap target inside the tray

The tray owns the gesture logic and the buttons own the click logic. This keeps activation decoupled from reveal behavior and avoids accidental triggers while swiping.

### DAC View Architecture

```
AugmentedRoadView._dac_active
        │
  ┌─────┴──────┐
  │ False      │ True
  ▼            ▼
Road camera  DACView (bento layout)
pipeline     ┌─────────────────────────────┐
             │ _SpeedCell  │  _DMojiCell   │
             │  (left 60%) │  (right 40%)  │
             └─────────────────────────────┘
```

**State transitions:**

| Event | Transition |
|-------|-----------|
| Swipe from right edge | Tray becomes `EXPANDED` |
| Tap DAC action (road view active) | `_dac_active = True`, tray collapses |
| Tap DAC action (DAC view active) | `_dac_active = False`, tray collapses |
| Tap outside tray actions | Tray collapses |
| Car stops / goes offroad | `_dac_active = False`, tray collapses |

### Files

| File | Role |
|------|------|
| `selfdrive/ui/mici/onroad/action_tray.py` | `SidePanelActionTray`, `BookmarkActionButton`, `DACActionButton` |
| `selfdrive/ui/mici/onroad/dac_view.py` | `DACView`, `_SpeedCell`, `_DMojiCell` — bento layout |
| `selfdrive/ui/mici/onroad/augmented_road_view.py` | `AugmentedRoadView`, road rendering, DAC mode switching |
| `selfdrive/assets/icons_dac/dac-btn.png` | Source asset for the DAC tray action icon |
| `selfdrive/assets/icons_dac/dac_btn.svg` | Source SVG for the icon |

### DACView Cell Structure

`DACView._layout()` computes all cell rects from the parent rect — adding a new bento cell means:
1. Create a `Widget` subclass in `dac_view.py`
2. Instantiate it in `DACView.__init__()` and register with `self._child()`
3. Compute its `rl.Rectangle` in `DACView._layout()`

Current cells (POC):

| Cell | Class | Content | Position |
|------|-------|---------|---------|
| Speed | `_SpeedCell` | Large speed number + unit label | Left 60% |
| DMoji | `_DMojiCell` | `DriverStateRenderer(lines=True)`, 100x100 | Right 40% |

### Dev Testing

Run `selfdrive/ui/mici/onroad/augmented_road_view.py` directly:
- **SPACE** — toggle wide/road camera
- **D** — toggle DAC mode (without needing to swipe)
