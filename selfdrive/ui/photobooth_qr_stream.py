"""Arm/disarm device stream pipeline while the Photobooth QR screen is visible.

Direct Connect posts SDP to ``http://<device>:5001/stream`` without Athena, so
``webrtcd`` / ``stream_encoderd`` / ``camerad`` must be allowed to run via
``PhotoboothStreamActive`` (see ``system/manager/process_config.py``).

Closing the QR screen does not clear ``PhotoboothStreamActive`` while
``PhotoboothSessionActive`` is true (handoff to on-device preview / active call).
"""

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog


def arm_photobooth_stream_for_direct_poc() -> None:
  params = Params()
  if not params.get_bool("IsOffroad"):
    cloudlog.warning("Photobooth POC: not arming stream (device not offroad)")
    return
  params.put_bool("PhotoboothStreamActive", True)
  cloudlog.info("Photobooth POC: PhotoboothStreamActive=true while Photobooth QR is shown")


def disarm_photobooth_stream_for_direct_poc() -> None:
  params = Params()
  if params.get_bool("PhotoboothSessionActive"):
    cloudlog.info("Photobooth POC: skip disarm stream while Photobooth session active")
    return
  params.put_bool("PhotoboothStreamActive", False)
  cloudlog.info("Photobooth POC: PhotoboothStreamActive=false (Photobooth QR closed)")
