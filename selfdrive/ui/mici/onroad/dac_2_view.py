import pyray as rl

from collections.abc import Callable

from openpilot.selfdrive.ui.mici.onroad import dac_view as base_dac_view
from openpilot.selfdrive.ui.mici.onroad.confidence_ball import ConfidenceBall
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached


_THEME_DARK = "dark"
_THEME_LIGHT = "light"
_THEME_WINGS = "wings"
_THEME_CYCLE = (_THEME_DARK, _THEME_LIGHT, _THEME_WINGS)
_WINGS_INNER_BG = rl.Color(8, 24, 46, 255)
_WINGS_BAR_BG = rl.Color(10, 32, 60, 255)
_WINGS_BAR_FRAME = rl.Color(183, 213, 0, 230)
_WINGS_SEG_OFF = rl.Color(22, 56, 92, 255)
_WINGS_SPEEDO_BG = rl.Color(7, 21, 40, 255)
_WINGS_TEXT_PRIMARY = rl.Color(246, 223, 74, 255)
_WINGS_TEXT_SECONDARY = rl.Color(194, 220, 246, 245)
_WINGS_BORDER_DISENGAGED = rl.Color(32, 152, 240, 255)
_WINGS_BORDER_OVERRIDE = rl.Color(246, 223, 74, 255)
_WINGS_SWEEP_LIT = rl.Color(246, 223, 74, 255)
_WINGS_SWEEP_UNLIT = rl.Color(26, 78, 120, 255)
_WINGS_SWEEP_REDZONE_UNLIT = rl.Color(82, 36, 48, 255)
_WINGS_LABEL_DEFAULT = rl.Color(184, 215, 245, 255)


class DAC2View(base_dac_view.DACView):
  def __init__(self, bookmark_callback: Callable[[], None] | None = None, confidence_ball: ConfidenceBall | None = None):
    self._theme_idx = 0
    super().__init__(
      bookmark_callback,
      light_mode_fn=lambda: self._theme_name() == _THEME_LIGHT,
      theme_name_fn=self._theme_name,
    )
    self._confidence_ball_tile = confidence_ball if confidence_ball is not None else ConfidenceBall()
    self._left_group_hit_rect = rl.Rectangle()
    self._set_speed_alpha = 0.0
    self._set_speed_text = "0"
    self._set_speed_temp_texture = gui_app.texture("icons_dac/wings-icon.png", 196, 196)

  def set_set_speed_overlay(self, alpha: float, set_speed_text: str) -> None:
    self._set_speed_alpha = max(0.0, min(1.0, alpha))
    self._set_speed_text = set_speed_text

  def _is_light_mode(self) -> bool:
    return self._theme_name() == _THEME_LIGHT

  def _theme_name(self) -> str:
    return _THEME_CYCLE[self._theme_idx]

  def _theme_colors(self) -> dict[str, rl.Color]:
    theme = self._theme_name()
    if theme == _THEME_DARK:
      return {
        "inner_bg": base_dac_view._DAC_BG_COLOR,
        "bar_bg": base_dac_view._BAR_BG_COLOR,
        "bar_frame": base_dac_view._BAR_FRAME_COLOR,
        "seg_off": base_dac_view._SEG_OFF_COLOR,
        "speedo_bg": base_dac_view._SPEEDO_PANEL_BG,
        "text_primary": rl.Color(255, 255, 255, 255),
        "text_secondary": rl.Color(235, 235, 235, 230),
        "confidence_mask": base_dac_view._BAR_BG_COLOR,
      }
    if theme == _THEME_LIGHT:
      return {
        "inner_bg": rl.Color(236, 236, 232, 255),
        "bar_bg": rl.Color(246, 246, 241, 255),
        "bar_frame": rl.Color(180, 180, 170, 220),
        "seg_off": rl.Color(214, 214, 204, 255),
        "speedo_bg": rl.Color(244, 244, 238, 255),
        "text_primary": rl.Color(34, 34, 34, 255),
        "text_secondary": rl.Color(72, 72, 72, 240),
        "confidence_mask": rl.Color(246, 246, 241, 255),
      }

    return {
      "inner_bg": _WINGS_INNER_BG,
      "bar_bg": _WINGS_BAR_BG,
      "bar_frame": _WINGS_BAR_FRAME,
      "seg_off": _WINGS_SEG_OFF,
      "speedo_bg": _WINGS_SPEEDO_BG,
      "text_primary": _WINGS_TEXT_PRIMARY,
      "text_secondary": _WINGS_TEXT_SECONDARY,
      "confidence_mask": _WINGS_BAR_BG,
    }

  def _border_colors_for_theme(self) -> dict[UIStatus, rl.Color]:
    theme = self._theme_name()
    if theme == _THEME_DARK:
      return base_dac_view._BORDER_COLORS
    if theme == _THEME_LIGHT:
      return {
        UIStatus.DISENGAGED: rl.Color(168, 177, 189, 255),
        UIStatus.OVERRIDE: rl.Color(176, 176, 168, 255),
        UIStatus.ENGAGED: base_dac_view._BORDER_COLORS[UIStatus.ENGAGED],
      }
    return {
      UIStatus.DISENGAGED: _WINGS_BORDER_DISENGAGED,
      UIStatus.OVERRIDE: _WINGS_BORDER_OVERRIDE,
      UIStatus.ENGAGED: base_dac_view._BORDER_COLORS[UIStatus.ENGAGED],
    }

  def _draw_speedometer_sweep(self, panel_rect: rl.Rectangle, speed_frac: float) -> None:
    seg_w = max(2.0, (panel_rect.width - (base_dac_view._SPEEDO_SEGMENTS - 1) * base_dac_view._SPEEDO_SEG_GAP) / base_dac_view._SPEEDO_SEGMENTS)
    lit_segments = speed_frac * base_dac_view._SPEEDO_SEGMENTS
    red_zone_start = int(base_dac_view._SPEEDO_SEGMENTS * base_dac_view._SPEEDO_RED_ZONE_START_RATIO)
    sweep_y = panel_rect.y + base_dac_view._SPEEDO_SWEEP_TOP_INSET
    sweep_h = max(10.0, panel_rect.height * base_dac_view._SPEEDO_SWEEP_HEIGHT_RATIO)
    theme = self._theme_name()
    is_light = theme == _THEME_LIGHT
    is_wings = theme == _THEME_WINGS

    for idx in range(base_dac_view._SPEEDO_SEGMENTS):
      x = panel_rect.x + idx * (seg_w + base_dac_view._SPEEDO_SEG_GAP)
      seg_rect = rl.Rectangle(x, sweep_y, seg_w, sweep_h)

      if idx < lit_segments:
        if idx < red_zone_start:
          color = _WINGS_SWEEP_LIT if is_wings else base_dac_view._RETRO_PANEL_GLOW
        else:
          color = rl.Color(210, 32, 24, 255)
      else:
        if is_light:
          color = rl.Color(188, 188, 178, 255) if idx < red_zone_start else rl.Color(198, 182, 178, 255)
        elif is_wings:
          color = _WINGS_SWEEP_UNLIT if idx < red_zone_start else _WINGS_SWEEP_REDZONE_UNLIT
        else:
          color = rl.Color(52, 52, 52, 255) if idx < red_zone_start else rl.Color(62, 24, 24, 255)
      rl.draw_rectangle_rounded(seg_rect, 0.15, 4, color)

  def _draw_speedometer_readout(self, rect: rl.Rectangle, panel_rect: rl.Rectangle) -> None:
    unit_text = "km/h" if ui_state.is_metric else "mph"
    unit_size = 27
    unit_text_size = measure_text_cached(self._font_medium, unit_text, unit_size)

    speed_text = str(round(self._speed_display_filter.x))
    speed_size = max(base_dac_view._SPEEDO_VALUE_MIN_SIZE, int(rect.height * 0.50))
    speed_text_size = measure_text_cached(self._font_display, speed_text, speed_size)
    speed_slot_size = measure_text_cached(self._font_display, "888", speed_size)
    speed_center_y = base_dac_view._top_row_number_center_y(rect)
    readout_group_w = speed_slot_size.x + base_dac_view._SPEEDO_UNIT_X_GAP + unit_text_size.x
    speed_slot_x = panel_rect.x + (panel_rect.width - readout_group_w) / 2 + base_dac_view._SPEEDO_READOUT_X_OFFSET
    speed_pos = rl.Vector2(
      speed_slot_x + (speed_slot_size.x - speed_text_size.x) / 2,
      speed_center_y - speed_text_size.y / 2 - base_dac_view._SPEED_TEXT_BASELINE_OFFSET - base_dac_view._SPEEDO_VALUE_Y_OFFSET,
    )
    theme = self._theme_colors()
    rl.draw_text_ex(self._font_display, speed_text, speed_pos, speed_size, 0, theme["text_primary"])

    unit_pos = rl.Vector2(
      speed_slot_x + speed_slot_size.x + base_dac_view._SPEEDO_UNIT_X_GAP,
      speed_pos.y + (speed_text_size.y - unit_text_size.y) / 2,
    )
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, unit_size, 0, theme["text_secondary"])

  def _draw_panel(self, rect: rl.Rectangle, _bg_color: rl.Color) -> None:
    theme = self._theme_colors()
    rl.draw_rectangle_rounded(rect, base_dac_view._TILE_ROUNDNESS, base_dac_view._TILE_SEGMENTS, theme["speedo_bg"])
    rl.draw_rectangle_rounded_lines_ex(rect, base_dac_view._TILE_ROUNDNESS, base_dac_view._TILE_SEGMENTS, 1.5, theme["bar_frame"])

  def _draw_speedometer_tile(self, rect: rl.Rectangle) -> None:
    self._draw_panel(rect, base_dac_view._SPEEDO_PANEL_BG)
    max_speed = 160.0 if ui_state.is_metric else 100.0
    speed_frac = min(max(self._speed / max_speed, 0.0), 1.0)
    panel_rect = rl.Rectangle(
      rect.x + base_dac_view._SPEEDO_PANEL_PAD_X,
      rect.y + base_dac_view._SPEEDO_PANEL_PAD_Y,
      rect.width - 2 * base_dac_view._SPEEDO_PANEL_PAD_X,
      rect.height - 2 * base_dac_view._SPEEDO_PANEL_PAD_Y,
    )
    self._draw_speedometer_sweep(panel_rect, speed_frac)
    self._draw_speedometer_readout(rect, panel_rect)

  def draw_status_border(self, rect: rl.Rectangle) -> None:
    border_rect = rl.Rectangle(rect.x, rect.y, rect.width, rect.height)
    border_colors = self._border_colors_for_theme()
    border_color = border_colors.get(ui_state.status, border_colors[UIStatus.DISENGAGED])
    rl.draw_rectangle_rounded(border_rect, base_dac_view._BORDER_ROUNDNESS, base_dac_view._BORDER_SEGMENTS, border_color)

    inner_rect = rl.Rectangle(
      rect.x + base_dac_view._BORDER_SIZE,
      rect.y + base_dac_view._BORDER_SIZE,
      rect.width - 2 * base_dac_view._BORDER_SIZE,
      rect.height - 2 * base_dac_view._BORDER_SIZE,
    )
    outer_radius_px = base_dac_view._rounded_corner_radius_px(border_rect, base_dac_view._BORDER_ROUNDNESS)
    inner_radius_px = max(0.0, outer_radius_px - base_dac_view._BORDER_SIZE)
    inner_roundness = base_dac_view._roundness_for_radius(inner_rect, inner_radius_px)
    rl.draw_rectangle_rounded(inner_rect, inner_roundness, base_dac_view._BORDER_SEGMENTS, self._theme_colors()["inner_bg"])

  def _draw_segment_bar(self, rect: rl.Rectangle, level: float, label: str,
                        label_color=None, label_icon=None, segment_color=None) -> None:
    theme = self._theme_colors()
    rl.draw_rectangle_rounded(rect, base_dac_view._TILE_ROUNDNESS, base_dac_view._TILE_SEGMENTS, theme["bar_bg"])
    rl.draw_rectangle_rounded_lines_ex(rect, base_dac_view._TILE_ROUNDNESS, base_dac_view._TILE_SEGMENTS, 1.5, theme["bar_frame"])

    seg_x = rect.x + base_dac_view._BAR_H_PAD
    seg_w = rect.width - 2 * base_dac_view._BAR_H_PAD
    seg_area_h = rect.height - base_dac_view._BAR_V_PAD_TOP - base_dac_view._LABEL_AREA_H
    seg_area_bottom = rect.y + base_dac_view._BAR_V_PAD_TOP + seg_area_h
    total_gap_h = (base_dac_view._N_SEGS - 1) * base_dac_view._SEG_GAP + (base_dac_view._N_PAIRS - 1) * base_dac_view._PAIR_EXTRA_GAP
    seg_h = max(4.0, (seg_area_h - total_gap_h) / base_dac_view._N_SEGS)
    n_lit = level * base_dac_view._N_SEGS

    def blend_seg(on: rl.Color, fill: float) -> rl.Color:
      off = theme["seg_off"]
      return rl.Color(
        int(off.r + fill * (on.r - off.r)),
        int(off.g + fill * (on.g - off.g)),
        int(off.b + fill * (on.b - off.b)),
        255,
      )

    collapse_until = -1
    for p in range(base_dac_view._COLLAPSE_STARTS_AT_PAIR, base_dac_view._N_PAIRS):
      if n_lit >= 2 * (p + 1):
        collapse_until = p

    if collapse_until >= 0:
      top_block_of_collapse = 2 * (collapse_until + 1) - 1
      c_y = base_dac_view._block_top_y(top_block_of_collapse, seg_h, seg_area_bottom)
      c_h = seg_area_bottom - c_y
      rl.draw_rectangle_rounded(
        rl.Rectangle(seg_x, c_y, seg_w, c_h),
        base_dac_view._SEG_ROUNDNESS,
        base_dac_view._SEG_ROUND_SEGS,
        segment_color if segment_color is not None else base_dac_view._SEG_ON[top_block_of_collapse],
      )
      first_normal_pair = collapse_until + 1
    else:
      first_normal_pair = 0

    for pair in range(first_normal_pair, base_dac_view._N_PAIRS):
      i_bot = pair * 2
      i_top = pair * 2 + 1
      color = segment_color if segment_color is not None else base_dac_view._SEG_ON[i_bot]
      bot_fill = min(max(n_lit - i_bot, 0.0), 1.0)
      top_fill = min(max(n_lit - i_top, 0.0), 1.0)
      bot_y = base_dac_view._block_top_y(i_bot, seg_h, seg_area_bottom)
      top_y = base_dac_view._block_top_y(i_top, seg_h, seg_area_bottom)

      if top_fill >= base_dac_view._MERGE_THRESHOLD:
        rl.draw_rectangle_rounded(
          rl.Rectangle(seg_x, top_y, seg_w, 2 * seg_h + base_dac_view._SEG_GAP),
          base_dac_view._SEG_ROUNDNESS, base_dac_view._SEG_ROUND_SEGS, color,
        )
      else:
        rl.draw_rectangle_rounded(
          rl.Rectangle(seg_x, bot_y, seg_w, seg_h),
          base_dac_view._SEG_ROUNDNESS, base_dac_view._SEG_ROUND_SEGS, blend_seg(color, bot_fill),
        )
        rl.draw_rectangle_rounded(
          rl.Rectangle(seg_x, top_y, seg_w, seg_h),
          base_dac_view._SEG_ROUNDNESS, base_dac_view._SEG_ROUND_SEGS, blend_seg(color, top_fill),
        )

    label_cx = rect.x + rect.width / 2
    label_cy = rect.y + rect.height - base_dac_view._LABEL_AREA_H / 2
    if self._theme_name() == _THEME_LIGHT:
      default_label_color = rl.Color(94, 94, 94, 255)
    elif self._theme_name() == _THEME_WINGS:
      default_label_color = _WINGS_LABEL_DEFAULT
    else:
      default_label_color = base_dac_view._DM_LABEL_DEFAULT_COLOR
    color = label_color if label_color is not None else default_label_color
    if label_icon is not None:
      scale = min(base_dac_view._DM_ICON_SIZE / label_icon.width, base_dac_view._DM_ICON_SIZE / label_icon.height)
      draw_w = label_icon.width * scale
      draw_h = label_icon.height * scale
      rl.draw_texture_ex(
        label_icon,
        rl.Vector2(int(label_cx - draw_w / 2), int(label_cy - draw_h / 2)),
        0.0, scale, color,
      )
    elif label:
      text_size = measure_text_cached(self._font, label, base_dac_view._LABEL_FONT_SIZE)
      rl.draw_text_ex(
        self._font, label,
        rl.Vector2(int(label_cx - text_size.x / 2), int(label_cy - text_size.y / 2)),
        base_dac_view._LABEL_FONT_SIZE, 0, color,
      )

  def _draw_set_speed_tile(self, rect: rl.Rectangle) -> None:
    self._draw_panel(rect, base_dac_view._SPEEDO_PANEL_BG)
    alpha = self._set_speed_alpha
    if alpha < 1e-2:
      return

    if self._theme_name() == _THEME_WINGS:
      icon_max_w = rect.width * 0.72
      icon_max_h = rect.height * 0.72
      scale = min(icon_max_w / self._set_speed_temp_texture.width, icon_max_h / self._set_speed_temp_texture.height)
      draw_w = self._set_speed_temp_texture.width * scale
      draw_h = self._set_speed_temp_texture.height * scale
      draw_x = rect.x + (rect.width - draw_w) / 2
      draw_y = rect.y + (rect.height - draw_h) / 2
      tint = rl.Color(255, 255, 255, int(255 * alpha))
      rl.draw_texture_ex(self._set_speed_temp_texture, rl.Vector2(draw_x, draw_y), 0.0, scale, tint)
      return

    speed_size = max(66, int(rect.height * 0.52))
    max_size = max(26, int(rect.height * 0.20))
    set_speed_text_size = measure_text_cached(self._font_display, self._set_speed_text, speed_size)
    max_text = tr("MAX")
    max_text_size = measure_text_cached(self._font_medium, max_text, max_size)

    group_height = set_speed_text_size.y + max_text_size.y - 6
    speed_y = rect.y + (rect.height - group_height) / 2 - 4
    speed_x = rect.x + (rect.width - set_speed_text_size.x) / 2
    max_x = rect.x + (rect.width - max_text_size.x) / 2
    max_y = speed_y + set_speed_text_size.y - 2

    text_color = self._theme_colors()["text_primary"]
    color = rl.Color(text_color.r, text_color.g, text_color.b, int(255 * 0.9 * alpha))
    rl.draw_text_ex(self._font_display, self._set_speed_text, rl.Vector2(speed_x, speed_y), speed_size, 0, color)
    rl.draw_text_ex(self._font_medium, max_text, rl.Vector2(max_x, max_y), max_size, 0, color)

  def _handle_mouse_release(self, mouse_pos) -> None:
    if rl.check_collision_point_rec(mouse_pos, self._left_group_hit_rect):
      self._theme_idx = (self._theme_idx + 1) % len(_THEME_CYCLE)
      return
    super()._handle_mouse_release(mouse_pos)

  def _draw_tiles(self, rect: rl.Rectangle) -> None:
    gap = base_dac_view._TILE_GAP

    left_group_w = rect.width * 0.42 - base_dac_view._LEFT_GROUP_WIDTH_SHRINK
    right_group_w = rect.width - left_group_w - gap

    tall_tile_w = (left_group_w - 2 * gap) / 3
    tall_tile_h = rect.height

    top_right_h = rect.height * base_dac_view._RIGHT_TOP_HEIGHT_RATIO + base_dac_view._RIGHT_TOP_HEIGHT_BOOST
    bottom_right_h = rect.height - top_right_h - base_dac_view._RIGHT_ROW_GAP
    bottom_tile_w = (right_group_w - base_dac_view._BOTTOM_ROW_GAP) / 2

    bar_rects = (
      rl.Rectangle(rect.x, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + tall_tile_w + gap, rect.y, tall_tile_w, tall_tile_h),
      rl.Rectangle(rect.x + 2 * (tall_tile_w + gap), rect.y, tall_tile_w, tall_tile_h),
    )
    self._left_group_hit_rect = rl.Rectangle(rect.x, rect.y, left_group_w, tall_tile_h)

    top_right_rect = rl.Rectangle(rect.x + left_group_w + gap, rect.y, right_group_w, top_right_h)
    bottom_row_y = rect.y + top_right_h + base_dac_view._RIGHT_ROW_GAP
    bottom_row_x = rect.x + left_group_w + gap
    self._experimental_hit_rect = rl.Rectangle(bottom_row_x, bottom_row_y, right_group_w, bottom_right_h)
    bottom_rects = (
      rl.Rectangle(bottom_row_x, bottom_row_y, bottom_tile_w, bottom_right_h),
      rl.Rectangle(bottom_row_x + bottom_tile_w + base_dac_view._BOTTOM_ROW_GAP, bottom_row_y, bottom_tile_w, bottom_right_h),
    )

    dm_label, dm_label_color, dm_icon = self._dm_bar_label_info()

    # DAC-2 swaps the old B slot for confidence while keeping S and DM behavior.
    self._confidence_ball_tile.render_in_rect(bar_rects[0], align_right=True, mask_color=self._theme_colors()["confidence_mask"])
    self._draw_segment_bar(bar_rects[1], self._combined_steering_warning(), "S", None, None, None)
    self._draw_segment_bar(bar_rects[2], self._dm_filter.x, dm_label, dm_label_color, dm_icon, None)

    bookmark_rect, speedo_rect = self._split_top_right_rect(top_right_rect)
    self._bookmark_hit_rect = top_right_rect
    self._bookmark_button.render(bookmark_rect)
    if self._set_speed_alpha > 1e-2:
      self._draw_set_speed_tile(speedo_rect)
    else:
      self._draw_speedometer_tile(speedo_rect)
    self._experimental_button.render(bottom_rects[0])
    self._lead_tile.render(bottom_rects[1])
