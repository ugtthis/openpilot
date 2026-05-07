"""Connect web app origin for Photobooth QR deep links.

Resolution order:
  1. Param ``PhotoboothConnectHost`` (e.g. ``https://op-photo-booth-site.pages.dev``)
  2. Env ``CONNECT_HOST``
  3. Built-in default (override with param / env if needed)
"""

import os

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

_DEFAULT_ORIGIN = "https://op-photo-booth-site.pages.dev"


def photobooth_connect_origin() -> str:
  raw = Params().get("PhotoboothConnectHost")
  if raw:
    o = str(raw).strip().rstrip("/")
    if o:
      return o
  env = (os.environ.get("CONNECT_HOST") or "").strip().rstrip("/")
  if env:
    return env
  cloudlog.debug(
    "Photobooth QR using default Connect origin (set PhotoboothConnectHost param or CONNECT_HOST): %s",
    _DEFAULT_ORIGIN,
  )
  return _DEFAULT_ORIGIN
