import pyray as rl

from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.params import Params
from openpilot.selfdrive.ui.mici.onroad.driver_state import DriverStateRenderer
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

# ── Visual constants ──────────────────────────────────────────────────────────

_BG_COLOR         = rl.Color(14, 14, 14, 255)
_CORNER_ROUNDNESS = 0.15
_CORNER_SEGMENTS  = 10
_CELL_GAP         = 8    # gap between bento cells
_CELL_PAD         = 14   # padding within each cell

# ── Typography ────────────────────────────────────────────────────────────────

_SPEED_FONT_SIZE      = 80    # scaled down dynamically if number is too wide
_SPEED_UNIT_FONT_SIZE = 32
_LABEL_FONT_SIZE      = 26
_DIST_FONT_SIZE       = 34
_MODE_FONT_SIZE       = 40

# ── Bento grid proportions ────────────────────────────────────────────────────
#
#   Row 1 (60% h):  Speed (50% w) | DMoji (25% w) | Confidence (25% w)
#   Row 2 (40% h):  Mode (25% w)  | Arc (50% w)   | Lead car (25% w)

_ROW1_H_FRAC     = 0.60
_R1_SPEED_W_FRAC = 0.50
_R1_DMOJI_W_FRAC = 0.25   # confidence takes remaining width
_R2_MODE_W_FRAC  = 0.25
_R2_ARC_W_FRAC   = 0.50   # lead car takes remaining width

# ── Shared helper ─────────────────────────────────────────────────────────────

def _bottom_label(font, text: str, rect: rl.Rectangle,
                  size: int = _LABEL_FONT_SIZE,
                  color: rl.Color = rl.Color(150, 150, 150, 200)) -> float:
  """Draw a centered label at the bottom of a cell. Returns its top-y so
  callers can avoid drawing content behind it."""
  sz = measure_text_cached(font, text, size)
  x  = rect.x + (rect.width - sz.x) / 2
  y  = rect.y + rect.height - _CELL_PAD - sz.y
  rl.draw_text_ex(font, text, rl.Vector2(x, y), size, 0, color)
  return y


# ── Row 1 cells ───────────────────────────────────────────────────────────────

class _SpeedCell(Widget):
  def __init__(self):
    super().__init__()
    self._font_bold   = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._speed_filter = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)
    self._speed = 0.0
    self._unit  = "km/h"

  def _update_state(self) -> None:
    car_state  = ui_state.sm["carState"]
    v_ego      = car_state.vEgoCluster or car_state.vEgo
    factor     = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self._unit  = "km/h" if ui_state.is_metric else "mph"
    self._speed = self._speed_filter.update(max(0.0, v_ego * factor))

  def _render(self, rect: rl.Rectangle) -> None:
    speed_text    = str(round(self._speed))
    available_w   = rect.width - 2 * _CELL_PAD
    speed_size    = measure_text_cached(self._font_bold, speed_text, _SPEED_FONT_SIZE)
    # Scale font down proportionally so the number never overflows the cell
    font_size     = int(_SPEED_FONT_SIZE * min(1.0, available_w / max(speed_size.x, 1)))
    speed_size    = measure_text_cached(self._font_bold,   speed_text, font_size)
    unit_size     = measure_text_cached(self._font_medium, self._unit,  _SPEED_UNIT_FONT_SIZE)
    group_h       = speed_size.y - 8 + unit_size.y
    group_top     = rect.y + (rect.height - group_h) / 2

    rl.draw_text_ex(self._font_bold, speed_text,
                    rl.Vector2(rect.x + (rect.width - speed_size.x) / 2, group_top),
                    font_size, 0, rl.WHITE)
    rl.draw_text_ex(self._font_medium, self._unit,
                    rl.Vector2(rect.x + (rect.width - unit_size.x) / 2,
                               group_top + speed_size.y - 8),
                    _SPEED_UNIT_FONT_SIZE, 0, rl.Color(200, 200, 200, 180))


class _DMojiCell(Widget):
  def __init__(self):
    super().__init__()
    self._dmoji = DriverStateRenderer(lines=True)
    self._dmoji.set_should_draw(True)
    self._dmoji.set_force_active(True)

  def _update_state(self) -> None:
    self._dmoji.set_should_draw(ui_state.is_onroad())

  def _render(self, rect: rl.Rectangle) -> None:
    size = min(rect.width, rect.height) * 0.75
    dmoji_rect = rl.Rectangle(
      rect.x + (rect.width  - size) / 2,
      rect.y + (rect.height - size) / 2,
      size, size,
    )
    self._dmoji.set_rect(dmoji_rect)
    self._dmoji.render(dmoji_rect)


class _ConfidenceCell(Widget):
  """Vertical track with a color-coded dot showing model confidence."""

  def __init__(self):
    super().__init__()
    self._font   = gui_app.font(FontWeight.MEDIUM)
    self._filter = FirstOrderFilter(-0.5, 0.5, 1 / gui_app.target_fps)

  def _update_state(self) -> None:
    if ui_state.status == UIStatus.DISENGAGED:
      self._filter.update(-0.5)
    else:
      preds = ui_state.sm['modelV2'].meta.disengagePredictions
      brake = max(preds.brakeDisengageProbs or [1])
      steer = max(preds.steerOverrideProbs  or [1])
      self._filter.update((1 - brake) * (1 - steer))

  def _dot_color(self) -> rl.Color:
    status = ui_state.status
    if status == UIStatus.ENGAGED:
      if self._filter.x > 0.5:
        return rl.Color(0, 230, 100, 255)
      elif self._filter.x > 0.2:
        return rl.Color(255, 170, 0, 255)
      else:
        return rl.Color(220, 40, 40, 255)
    elif status == UIStatus.OVERRIDE:
      return rl.Color(200, 200, 200, 255)
    else:
      return rl.Color(50, 50, 50, 255)

  def _render(self, rect: rl.Rectangle) -> None:
    label_top = _bottom_label(self._font, "conf", rect)

    dot_r        = 10
    track_x      = rect.x + rect.width / 2
    track_top    = rect.y + _CELL_PAD + dot_r
    track_bottom = label_top - _CELL_PAD - dot_r
    track_h      = max(track_bottom - track_top, 1.0)

    rl.draw_line(int(track_x), int(track_top), int(track_x), int(track_bottom),
                 rl.Color(60, 60, 60, 255))

    t     = max(0.0, min(1.0, self._filter.x))
    dot_y = track_bottom - t * track_h
    rl.draw_circle(int(track_x), int(dot_y), dot_r, self._dot_color())


# ── Row 2 cells ───────────────────────────────────────────────────────────────

class _ModeToggleCell(Widget):
  """Tap to toggle ExperimentalMode param (gated on longitudinal control)."""

  def __init__(self):
    super().__init__()
    self._font_bold   = gui_app.font(FontWeight.BOLD)
    self._font_medium = gui_app.font(FontWeight.MEDIUM)
    self._click_delay = 0.075
    self._params      = Params()
    self._experimental = self._params.get_bool("ExperimentalMode")  # read once
    self.set_click_callback(self._toggle)

  def _toggle(self) -> None:
    self._experimental = not self._experimental          # flip in memory immediately
    self._params.put_bool("ExperimentalMode", self._experimental)  # persist

  def _update_state(self) -> None:
    pass  # state lives in memory; _toggle is the only writer

  def _render(self, rect: rl.Rectangle) -> None:
    available  = ui_state.has_longitudinal_control
    alpha      = 255 if available else 80

    mode_text   = "exp"   if self._experimental else "chill"
    mode_color  = rl.Color(255, 200, 0, alpha) if self._experimental else rl.Color(160, 210, 255, alpha)
    available_w = rect.width - 2 * _CELL_PAD
    mode_size   = measure_text_cached(self._font_bold, mode_text, _MODE_FONT_SIZE)
    mode_fs     = int(_MODE_FONT_SIZE * min(1.0, available_w / max(mode_size.x, 1)))
    mode_size   = measure_text_cached(self._font_bold, mode_text, mode_fs)
    mode_x      = rect.x + (rect.width  - mode_size.x) / 2
    mode_y      = rect.y + (rect.height - mode_size.y) / 2
    rl.draw_text_ex(self._font_bold, mode_text, rl.Vector2(mode_x, mode_y),
                    mode_fs, 0, mode_color)

    if not available:
      lock_size = measure_text_cached(self._font_medium, "no long ctrl", 22)
      lock_x    = rect.x + (rect.width - lock_size.x) / 2
      rl.draw_text_ex(self._font_medium, "no long ctrl",
                      rl.Vector2(lock_x, rect.y + _CELL_PAD), 22, 0,
                      rl.Color(100, 100, 100, 180))


class _SteeringArcCell(Widget):
  """Top-down bird's eye view of the predicted path from modelV2.position."""

  _VIEW_DISTANCE = 50.0   # metres shown in the cell height
  _MAX_POINTS    = 40

  def __init__(self):
    super().__init__()
    self._path_pts: list[tuple[float, float]] = []

  def _update_state(self) -> None:
    sm = ui_state.sm
    if not sm.valid['modelV2']:
      self._path_pts = []
      return
    xs = sm['modelV2'].position.x
    ys = sm['modelV2'].position.y
    self._path_pts = list(zip(xs, ys))

  def _render(self, rect: rl.Rectangle) -> None:
    cx     = rect.x + rect.width  / 2
    bottom = rect.y + rect.height - _CELL_PAD
    top    = rect.y + _CELL_PAD
    scale  = (bottom - top) / self._VIEW_DISTANCE

    status = ui_state.status
    if status == UIStatus.ENGAGED:
      path_color = rl.Color(0, 220, 80, 200)
      car_color  = rl.Color(0, 220, 80, 255)
    elif status == UIStatus.OVERRIDE:
      path_color = rl.Color(255, 255, 255, 150)
      car_color  = rl.WHITE
    else:
      path_color = rl.Color(120, 120, 120, 120)
      car_color  = rl.Color(120, 120, 120, 200)

    # Car dot at bottom center
    rl.draw_circle(int(cx), int(bottom), 6, car_color)

    # Build screen-space points along the path
    screen_pts: list[rl.Vector2] = [rl.Vector2(cx, bottom)]
    for px, py in self._path_pts[:self._MAX_POINTS]:
      if px <= 0:
        continue
      sx = cx     - py * scale   # lateral: neg-y = left → right on screen
      sy = bottom - px * scale   # forward:  x   → upward
      if sy < top:
        break
      screen_pts.append(rl.Vector2(sx, sy))

    if len(screen_pts) >= 2:
      rl.draw_line_strip(screen_pts, len(screen_pts), path_color)


class _LeadCarCell(Widget):
  """Vertical bar gauge: fills as the lead car approaches, color-coded by distance."""

  _MAX_DIST   = 100.0   # metres at which bar is empty
  _BAR_W_FRAC = 0.35    # bar width as fraction of cell width

  def __init__(self):
    super().__init__()
    self._font        = gui_app.font(FontWeight.BOLD)
    self._font_label  = gui_app.font(FontWeight.MEDIUM)
    self._has_lead    = False
    self._d_rel       = 0.0
    self._fill_filter = FirstOrderFilter(0.0, 0.15, 1 / gui_app.target_fps)

  def _update_state(self) -> None:
    sm    = ui_state.sm
    radar = sm['radarState'] if sm.valid['radarState'] else None
    lead  = radar.leadOne if radar else None
    self._has_lead = bool(lead and lead.status)
    if self._has_lead:
      self._d_rel = lead.dRel
      self._fill_filter.update(1.0 - min(self._d_rel / self._MAX_DIST, 1.0))
    else:
      self._fill_filter.update(0.0)

  def _bar_color(self) -> rl.Color:
    if not self._has_lead:
      return rl.Color(80, 80, 80, 180)
    if self._d_rel > 40:
      return rl.Color(0, 200, 80, 255)
    if self._d_rel > 15:
      return rl.Color(255, 150, 0, 255)
    return rl.Color(220, 40, 40, 255)

  def _render(self, rect: rl.Rectangle) -> None:
    label_top = _bottom_label(self._font_label, "lead", rect)

    bar_w      = rect.width * self._BAR_W_FRAC
    bar_x      = rect.x + (rect.width - bar_w) / 2
    bar_top    = rect.y + _CELL_PAD + _DIST_FONT_SIZE + 6
    bar_bottom = label_top - _CELL_PAD
    bar_h      = max(bar_bottom - bar_top, 1.0)

    # Track background
    rl.draw_rectangle_rounded(rl.Rectangle(bar_x, bar_top, bar_w, bar_h),
                              0.3, 8, rl.Color(40, 40, 40, 255))

    # Fill (grows from bottom as car gets closer)
    fill_h = bar_h * self._fill_filter.x
    if fill_h > 1:
      rl.draw_rectangle_rounded(
        rl.Rectangle(bar_x, bar_bottom - fill_h, bar_w, fill_h),
        0.3, 8, self._bar_color(),
      )

    # Distance readout
    if self._has_lead:
      dist      = self._d_rel if ui_state.is_metric else self._d_rel * 3.28084
      unit      = "m" if ui_state.is_metric else "ft"
      dist_text = f"{round(dist)}{unit}"
      alpha     = 255
    else:
      dist_text = "--"
      alpha     = 100

    dist_size = measure_text_cached(self._font, dist_text, _DIST_FONT_SIZE)
    rl.draw_text_ex(self._font, dist_text,
                    rl.Vector2(rect.x + (rect.width - dist_size.x) / 2, rect.y + _CELL_PAD),
                    _DIST_FONT_SIZE, 0, rl.Color(255, 255, 255, alpha))


# ── DACView: bento container ──────────────────────────────────────────────────

class DACView(Widget):
  def __init__(self):
    super().__init__()
    self._speed_cell      = self._child(_SpeedCell())
    self._dmoji_cell      = self._child(_DMojiCell())
    self._confidence_cell = self._child(_ConfidenceCell())
    self._mode_cell       = self._child(_ModeToggleCell())
    self._arc_cell        = self._child(_SteeringArcCell())
    self._lead_cell       = self._child(_LeadCarCell())

  def _layout(self) -> None:
    r = self.rect
    g = _CELL_GAP

    # Outer padding matches the inter-cell gap so all spacing is uniform
    inner_x = r.x + g
    inner_y = r.y + g
    inner_w = r.width  - 2 * g
    inner_h = r.height - 2 * g

    # Two rows separated by one gap
    available_h = inner_h - g
    row1_h = available_h * _ROW1_H_FRAC
    row2_h = available_h - row1_h
    row1_y = inner_y
    row2_y = inner_y + row1_h + g

    # Three columns separated by two gaps
    available_w = inner_w - 2 * g

    # Row 1: Speed (50%) | DMoji (25%) | Confidence (remaining)
    r1_speed_w = available_w * _R1_SPEED_W_FRAC
    r1_dmoji_w = available_w * _R1_DMOJI_W_FRAC
    r1_conf_w  = available_w - r1_speed_w - r1_dmoji_w
    r1_speed_x = inner_x
    r1_dmoji_x = r1_speed_x + r1_speed_w + g
    r1_conf_x  = r1_dmoji_x + r1_dmoji_w + g

    self._speed_cell.set_rect(      rl.Rectangle(r1_speed_x, row1_y, r1_speed_w, row1_h))
    self._dmoji_cell.set_rect(      rl.Rectangle(r1_dmoji_x, row1_y, r1_dmoji_w, row1_h))
    self._confidence_cell.set_rect( rl.Rectangle(r1_conf_x,  row1_y, r1_conf_w,  row1_h))

    # Row 2: Mode (25%) | Arc (50%) | Lead (remaining)
    r2_mode_w = available_w * _R2_MODE_W_FRAC
    r2_arc_w  = available_w * _R2_ARC_W_FRAC
    r2_lead_w = available_w - r2_mode_w - r2_arc_w
    r2_mode_x = inner_x
    r2_arc_x  = r2_mode_x + r2_mode_w + g
    r2_lead_x = r2_arc_x  + r2_arc_w  + g

    self._mode_cell.set_rect( rl.Rectangle(r2_mode_x, row2_y, r2_mode_w, row2_h))
    self._arc_cell.set_rect(  rl.Rectangle(r2_arc_x,  row2_y, r2_arc_w,  row2_h))
    self._lead_cell.set_rect( rl.Rectangle(r2_lead_x, row2_y, r2_lead_w, row2_h))

  def _render(self, rect: rl.Rectangle) -> None:
    # Draw each cell's background — gaps between them show as the outer dark area
    for cell in (self._speed_cell, self._dmoji_cell, self._confidence_cell,
                 self._mode_cell,  self._arc_cell,   self._lead_cell):
      rl.draw_rectangle_rounded(cell.rect, _CORNER_ROUNDNESS, _CORNER_SEGMENTS, _BG_COLOR)

    self._speed_cell.render(      self._speed_cell.rect)
    self._dmoji_cell.render(      self._dmoji_cell.rect)
    self._confidence_cell.render( self._confidence_cell.rect)
    self._mode_cell.render(       self._mode_cell.rect)
    self._arc_cell.render(        self._arc_cell.rect)
    self._lead_cell.render(       self._lead_cell.rect)
