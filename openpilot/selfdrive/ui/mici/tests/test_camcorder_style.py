from openpilot.selfdrive.ui.mici.layouts.camcorder_style import (
  SECOND_ROW_VISIBLE_FRACTION, row_height_for_peek, thumb_size_for_row,
)


def test_second_row_only_peeks_into_the_viewport():
  list_h, gap = 168, 2
  row = row_height_for_peek(list_h, gap, SECOND_ROW_VISIBLE_FRACTION)
  visible_next = list_h - row - gap
  assert 0 < visible_next < row
  assert abs(visible_next / row - SECOND_ROW_VISIBLE_FRACTION) < 0.02


def test_thumbnail_matches_row_height_at_four_by_three():
  thumb_w, thumb_h = thumb_size_for_row(114)
  assert thumb_h == 114
  assert abs(thumb_w / thumb_h - 4 / 3) < 0.02
