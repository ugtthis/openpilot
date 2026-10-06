"""User-chosen camcorder capture settings, persisted as params.

Unknown or missing values read as the stock defaults, so a bad param can never
change what the camcorder records into something nobody chose.
"""

from dataclasses import dataclass
from enum import StrEnum

from openpilot.common.params import Params

QUALITY_PARAM = "CamcorderQuality"
FRAME_RATE_PARAM = "CamcorderFrameRate"


class Quality(StrEnum):
  STOCK = "stock"  # the encoder settings openpilot uses for driving logs
  MAX = "max"


FRAME_RATES = (20, 30)


@dataclass(frozen=True)
class CamcorderSettings:
  quality: Quality = Quality.STOCK
  frame_rate: int = FRAME_RATES[0]

  @classmethod
  def load(cls, params: Params | None = None) -> "CamcorderSettings":
    params = params or Params()
    quality = params.get(QUALITY_PARAM, return_default=True)
    frame_rate = params.get(FRAME_RATE_PARAM, return_default=True)
    return cls(
      quality=Quality(quality) if quality in tuple(Quality) else Quality.STOCK,
      frame_rate=frame_rate if frame_rate in FRAME_RATES else FRAME_RATES[0],
    )

  def save(self, params: Params | None = None) -> None:
    params = params or Params()
    params.put(QUALITY_PARAM, str(self.quality), block=True)
    params.put(FRAME_RATE_PARAM, self.frame_rate, block=True)
