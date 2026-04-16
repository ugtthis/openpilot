"""S-bar column: segment bar uses label="" then GPS is drawn on top (replaces a letter label).
Steer → rotation is −deg (same sign as HudRenderer wheel) but |steer| is clamped here; draw_texture_pro uses center-anchored dest/origin like that HUD path."""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad import dac_view as base_dac_view
from openpilot.selfdrive.ui.mici.onroad.dac_2_view import DAC2View
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached


_DAC3_LEFT_GROUP_WIDTH_RATIO = 0.57

_S_BAR_GPS_ICON_MAX_FRAC = 0.67
_S_BAR_GPS_STEER_CLAMP_DEG = 90.0
_S_BAR_GPS_TEX_W = 88
_S_BAR_GPS_TEX_H = 100


class DAC3LeadCarTile(base_dac_view.LeadCarTile):
  def _draw_icon_and_indicator(self, rect: rl.Rectangle, lead_visual: float) -> None:
    theme = self._theme_name()
    is_light = theme == "light"
    is_wings = theme == "wings"
    lead_none_texture = self._lead_texture_none_light if is_light else self._lead_texture_none
    lead_on_texture = self._lead_texture_lead_light if is_light else self._lead_texture_lead

    icon_w = max(1.0, rect.width - base_dac_view._LEAD_TILE_ICON_LEFT_PAD - base_dac_view._LEAD_TILE_ICON_RIGHT_PAD)
    icon_h = max(1.0, rect.height - 2 * base_dac_view._LEAD_TILE_ICON_PAD_Y)
    base_scale = min(icon_w / lead_none_texture.width, icon_h / lead_none_texture.height)
    draw_w = lead_none_texture.width * base_scale
    draw_h = lead_none_texture.height * base_scale
    icon_x = rect.x + (rect.width - draw_w) / 2
    icon_y = rect.y + (rect.height - draw_h) / 2 - base_dac_view._LEAD_TILE_ICON_TOP_BIAS

    dim_tint = rl.Color(255, 255, 255, 220) if is_light else (rl.Color(180, 214, 240, 230) if is_wings else rl.Color(118, 118, 118, 220))
    rl.draw_texture_ex(lead_none_texture, rl.Vector2(icon_x, icon_y), 0.0, base_scale, dim_tint)
    if lead_visual > 0:
      on_tint = rl.Color(255, 255, 255, int(255 * lead_visual))
      rl.draw_texture_ex(lead_on_texture, rl.Vector2(icon_x, icon_y), 0.0, base_scale, on_tint)


class DAC3View(DAC2View):
  def __init__(self, bookmark_callback=None, confidence_ball=None):
    super().__init__(bookmark_callback, confidence_ball)
    self._lead_tile = self._child(DAC3LeadCarTile(light_mode_fn=self._is_light_mode, theme_name_fn=self._theme_name))
    self._s_bar_gps_texture = gui_app.texture(
      "icons_dac/gps-nav-s-bar.png", _S_BAR_GPS_TEX_W, _S_BAR_GPS_TEX_H,
    )

  def _draw_s_bar_gps_icon(self, bar_rect: rl.Rectangle) -> None:
    sm = ui_state.sm
    steer_deg = float(sm["carState"].steeringAngleDeg) if sm.valid["carState"] else 0.0
    steer_deg = max(-_S_BAR_GPS_STEER_CLAMP_DEG, min(_S_BAR_GPS_STEER_CLAMP_DEG, steer_deg))
    rotation_deg = -steer_deg

    tex = self._s_bar_gps_texture
    label_cx = int(bar_rect.x + bar_rect.width / 2)
    label_cy = int(bar_rect.y + bar_rect.height - base_dac_view._LABEL_AREA_H / 2)
    max_w = bar_rect.width * _S_BAR_GPS_ICON_MAX_FRAC
    max_h = base_dac_view._LABEL_AREA_H * _S_BAR_GPS_ICON_MAX_FRAC
    scale = min(max_w / tex.width, max_h / tex.height)
    draw_w = tex.width * scale
    draw_h = tex.height * scale

    src = rl.Rectangle(0, 0, tex.width, tex.height)
    dest = rl.Rectangle(float(label_cx), float(label_cy), draw_w, draw_h)
    origin = (draw_w / 2, draw_h / 2)
    tint = self._theme_colors()["text_primary"]
    rl.draw_texture_pro(tex, src, dest, origin, rotation_deg, tint)

  def _dac3_steering_warning_for_weight(self, dominant_weight: float) -> float:
    steer_prediction = self._normalized_steer_prediction()
    steer_actuator = self._steer_actuator_filter.x
    dominant = max(steer_prediction, steer_actuator)
    agreement = steer_prediction * steer_actuator
    dominant_weight = min(max(dominant_weight, 0.0), 1.0)
    return dominant_weight * dominant + (1.0 - dominant_weight) * agreement

  def _draw_speedometer_sweep(self, panel_rect: rl.Rectangle, speed_frac: float) -> None:
    max_segments = base_dac_view._SPEEDO_SEGMENTS
    gap = float(base_dac_view._SPEEDO_SEG_GAP)
    min_seg_w = 1.0
    seg_count = max_segments
    seg_w = (panel_rect.width - (seg_count - 1) * gap) / seg_count

    while seg_count > 12 and seg_w < min_seg_w:
      seg_count -= 1
      seg_w = (panel_rect.width - (seg_count - 1) * gap) / seg_count

    if seg_w < min_seg_w and seg_count > 1:
      gap = max(0.0, (panel_rect.width - seg_count * min_seg_w) / (seg_count - 1))
      seg_w = min_seg_w

    lit_segments = speed_frac * seg_count
    red_zone_start = int(seg_count * base_dac_view._SPEEDO_RED_ZONE_START_RATIO)
    sweep_y = panel_rect.y + base_dac_view._SPEEDO_SWEEP_TOP_INSET
    sweep_h = max(10.0, panel_rect.height * base_dac_view._SPEEDO_SWEEP_HEIGHT_RATIO)
    theme = self._theme_name()
    is_light = theme == "light"
    is_wings = theme == "wings"

    for idx in range(seg_count):
      x = panel_rect.x + idx * (seg_w + gap)
      seg_rect = rl.Rectangle(x, sweep_y, seg_w, sweep_h)

      if idx < lit_segments:
        if idx < red_zone_start:
          color = base_dac_view._RETRO_PANEL_GLOW
          if is_wings:
            color = rl.Color(246, 223, 74, 255)
        else:
          color = rl.Color(210, 32, 24, 255)
      else:
        if is_light:
          color = rl.Color(188, 188, 178, 255) if idx < red_zone_start else rl.Color(198, 182, 178, 255)
        elif is_wings:
          color = rl.Color(26, 78, 120, 255) if idx < red_zone_start else rl.Color(82, 36, 48, 255)
        else:
          color = rl.Color(52, 52, 52, 255) if idx < red_zone_start else rl.Color(62, 24, 24, 255)
      rl.draw_rectangle_rounded(seg_rect, 0.15, 4, color)

  def _draw_speedometer_readout(self, rect: rl.Rectangle, panel_rect: rl.Rectangle) -> None:
    unit_text = "km/h" if ui_state.is_metric else "mph"
    speed_text = str(round(self._speed_display_filter.x))
    speed_size = 62
    unit_size = 20

    unit_text_size = measure_text_cached(self._font_medium, unit_text, unit_size)
    speed_text_size = measure_text_cached(self._font_display, speed_text, speed_size)
    speed_center_y = base_dac_view._top_row_number_center_y(rect)
    readout_group_w = speed_text_size.x + 1 + unit_text_size.x
    speed_slot_x = panel_rect.x + (panel_rect.width - readout_group_w) / 2
    speed_pos = rl.Vector2(
      speed_slot_x,
      speed_center_y - speed_text_size.y / 2 - base_dac_view._SPEED_TEXT_BASELINE_OFFSET - base_dac_view._SPEEDO_VALUE_Y_OFFSET,
    )
    theme = self._theme_colors()
    rl.draw_text_ex(self._font_display, speed_text, speed_pos, speed_size, 0, theme["text_primary"])

    unit_pos = rl.Vector2(
      speed_pos.x + speed_text_size.x + 1,
      speed_pos.y + (speed_text_size.y - unit_text_size.y) / 2,
    )
    rl.draw_text_ex(self._font_medium, unit_text, unit_pos, unit_size, 0, theme["text_secondary"])

  def _draw_tiles(self, rect: rl.Rectangle) -> None:
    gap = base_dac_view._TILE_GAP

    left_group_w = rect.width * _DAC3_LEFT_GROUP_WIDTH_RATIO
    right_group_w = rect.width - left_group_w - gap

    tall_tile_w = (left_group_w - 3 * gap) / 4
    tall_tile_h = rect.height

    top_right_h = rect.height * base_dac_view._RIGHT_TOP_HEIGHT_RATIO + base_dac_view._RIGHT_TOP_HEIGHT_BOOST
    bottom_right_h = rect.height - top_right_h - base_dac_view._RIGHT_ROW_GAP
    bottom_tile_w = (right_group_w - base_dac_view._BOTTOM_ROW_GAP) / 2

    bar_rects = tuple(
      rl.Rectangle(rect.x + idx * (tall_tile_w + gap), rect.y, tall_tile_w, tall_tile_h)
      for idx in range(4)
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

    self._confidence_ball_tile.render_in_rect(bar_rects[0], align_right=True, mask_color=self._theme_colors()["confidence_mask"])
    self._draw_segment_bar(bar_rects[1], self._combined_steering_warning(), "", None, None, None)
    self._draw_s_bar_gps_icon(bar_rects[1])
    self._draw_segment_bar(bar_rects[2], self._dac3_steering_warning_for_weight(0.75), "75", None, None, None)
    self._draw_segment_bar(bar_rects[3], self._dac3_steering_warning_for_weight(0.80), "80", None, None, None)

    bookmark_rect, speedo_rect = self._split_top_right_rect(top_right_rect)
    self._bookmark_hit_rect = top_right_rect
    self._bookmark_button.render(bookmark_rect)
    if self._set_speed_alpha > 1e-2:
      self._draw_set_speed_tile(speedo_rect)
    else:
      self._draw_speedometer_tile(speedo_rect)
    self._experimental_button.render(bottom_rects[0])
    self._lead_tile.render(bottom_rects[1])
