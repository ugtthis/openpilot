import pyray as rl

from collections.abc import Callable

from openpilot.selfdrive.ui.mici.onroad import dac_view as base_dac_view
from openpilot.selfdrive.ui.mici.onroad.confidence_ball import ConfidenceBall


class DAC2View(base_dac_view.DACView):
  def __init__(self, bookmark_callback: Callable[[], None] | None = None, confidence_ball: ConfidenceBall | None = None):
    super().__init__(bookmark_callback)
    self._confidence_ball_tile = confidence_ball if confidence_ball is not None else ConfidenceBall()

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

    top_right_rect = rl.Rectangle(rect.x + left_group_w + gap, rect.y, right_group_w, top_right_h)
    bottom_row_y = rect.y + top_right_h + base_dac_view._RIGHT_ROW_GAP
    bottom_row_x = rect.x + left_group_w + gap
    bottom_rects = (
      rl.Rectangle(bottom_row_x, bottom_row_y, bottom_tile_w, bottom_right_h),
      rl.Rectangle(bottom_row_x + bottom_tile_w + base_dac_view._BOTTOM_ROW_GAP, bottom_row_y, bottom_tile_w, bottom_right_h),
    )

    dm_label, dm_label_color, dm_icon = self._dm_bar_label_info()

    # DAC-2 swaps the old B slot for confidence while keeping S and DM behavior.
    self._confidence_ball_tile.render_in_rect(bar_rects[0], align_right=True)
    self._draw_segment_bar(bar_rects[1], self._combined_steering_warning(), "S", None, None, None)
    self._draw_segment_bar(bar_rects[2], self._dm_filter.x, dm_label, dm_label_color, dm_icon, None)

    bookmark_rect, speedo_rect = self._split_top_right_rect(top_right_rect)
    self._bookmark_hit_rect = top_right_rect
    self._bookmark_button.render(bookmark_rect)
    self._draw_speedometer_tile(speedo_rect)
    self._experimental_button.render(bottom_rects[0])
    self._lead_tile.render(bottom_rects[1])
