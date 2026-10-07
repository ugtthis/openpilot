#!/usr/bin/env python3
import datetime
import os
import signal
import sys
import time
import traceback

from openpilot.cereal import log
import openpilot.cereal.messaging as messaging
from openpilot.common.utils import atomic_write
from openpilot.common.params import Params, ParamKeyFlag
from openpilot.common.text_window import TextWindow
from openpilot.common.hardware import HARDWARE
from openpilot.system.manager.helpers import unblock_stdout, save_bootlog
from openpilot.system.manager.process import ensure_running
from openpilot.system.manager.process_config import managed_processes
from openpilot.system.camcorder.settings import ENCODER_MODE_PARAM, SENSOR_MODE_PARAM, CamcorderSettings, Quality
from openpilot.system.camcorder_lease import revoke_camcorder
from openpilot.system.loggerd.encoder_lease import revoke_encoder
from openpilot.system.athena.registration import register, UNREGISTERED_DONGLE_ID
from openpilot.common.swaglog import cloudlog, add_file_handler
from openpilot.common.version import get_build_metadata
from openpilot.common.hardware.hw import Paths


def manager_init() -> None:
  save_bootlog()

  build_metadata = get_build_metadata()

  params = Params()
  params.clear_all(ParamKeyFlag.CLEAR_ON_MANAGER_START)
  params.clear_all(ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION)
  params.clear_all(ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION)
  params.clear_all(ParamKeyFlag.CLEAR_ON_IGNITION_ON)
  if build_metadata.release_channel:
    params.clear_all(ParamKeyFlag.DEVELOPMENT_ONLY)

  if params.get_bool("RecordFrontLock"):
    params.put_bool("RecordFront", True, block=True)

  # set unset params to their default value
  for k in params.all_keys():
    default_value = params.get_default_value(k)
    if default_value is not None and params.get(k) is None:
      params.put(k, default_value, block=True)

  # Create folders needed for msgq
  try:
    os.mkdir(Paths.shm_path())
  except FileExistsError:
    pass
  except PermissionError:
    print(f"WARNING: failed to make {Paths.shm_path()}")

  # set params
  serial = HARDWARE.get_serial()
  params.put("Version", build_metadata.openpilot.version, block=True)
  params.put("GitCommit", build_metadata.openpilot.git_commit, block=True)
  params.put("GitCommitDate", build_metadata.openpilot.git_commit_date, block=True)
  params.put("GitBranch", build_metadata.channel, block=True)
  params.put("GitRemote", build_metadata.openpilot.git_origin, block=True)
  params.put_bool("IsTestedBranch", build_metadata.tested_channel, block=True)
  params.put_bool("IsReleaseBranch", build_metadata.release_channel, block=True)
  params.put("HardwareSerial", serial, block=True)

  # set dongle id
  reg_res = register(show_spinner=True)
  if reg_res:
    dongle_id = reg_res
  else:
    raise Exception(f"Registration failed for device {serial}")
  os.environ['DONGLE_ID'] = dongle_id  # Needed for swaglog
  os.environ['GIT_ORIGIN'] = build_metadata.openpilot.git_normalized_origin # Needed for swaglog
  os.environ['GIT_BRANCH'] = build_metadata.channel # Needed for swaglog
  os.environ['GIT_COMMIT'] = build_metadata.openpilot.git_commit # Needed for swaglog

  if not build_metadata.openpilot.is_dirty:
    os.environ['CLEAN'] = '1'

  # init logging
  cloudlog.bind_global(dongle_id=dongle_id,
                       version=build_metadata.openpilot.version,
                       origin=build_metadata.openpilot.git_normalized_origin,
                       branch=build_metadata.channel,
                       commit=build_metadata.openpilot.git_commit,
                       dirty=build_metadata.openpilot.is_dirty,
                       device=HARDWARE.get_device_type())

def manager_cleanup() -> None:
  # send signals to kill all procs
  for p in managed_processes.values():
    p.stop(block=False)

  # ensure all are killed
  for p in managed_processes.values():
    p.stop(block=True)

  cloudlog.info("everything is dead")


def ignition_blocked_processes(started: bool, ignition: bool) -> list[str]:
  """Manager backstop preventing offroad leases from crossing ignition-on."""
  # Leases are an offroad convenience, never permission to keep camcorder-only
  # resources alive while an ignition-on device is waiting to start.
  return ["encoderd", "camcorderd"] if ignition and not started else []


def drive_start_restarted_processes(started: bool, started_prev: bool) -> list[str]:
  """Processes skipped for one loop when a drive starts, so the drive gets a fresh one."""
  # The camcorder also runs these offroad; stock only runs them onroad, so every
  # drive starts with processes that have no offroad history.
  return ["camerad", "encoderd"] if started and not started_prev else []


# Only MICI is known to carry the OS04C10 wide sensor that the camcorder modes program.
CAMCORDER_MODES_SUPPORTED = HARDWARE.get_device_type() == "mici"
_TAKE_PHASES = ("recording", "finalizing")


def camcorder_stock_required(started: bool, ignition: bool, panda_state_seen: bool) -> bool:
  """Stock until panda state proves ignition is off; driving never sees a camcorder mode."""
  return started or ignition or not panda_state_seen or not CAMCORDER_MODES_SUPPORTED


def desired_camcorder_sensor_mode(stock_required: bool, params: Params) -> str:
  """Ignition and the driving stack always get the exact stock sensor mode."""
  return "stock" if stock_required else CamcorderSettings.load(params).sensor_mode


def update_camcorder_sensor_mode(stock_required: bool, params: Params, take_active: bool = False) -> list[str]:
  """Publish the startup-only mode and name processes that must be rebuilt around it."""
  # A settings change during a take waits for it to finish; stock is never deferred.
  if take_active and not stock_required:
    return []
  settings = CamcorderSettings.load(params)
  desired_sensor = desired_camcorder_sensor_mode(stock_required, params)
  desired_encoder = f"{desired_sensor}:{Quality.STOCK if stock_required else settings.quality}"
  restart: list[str] = []
  if params.get(SENSOR_MODE_PARAM, return_default=True) != desired_sensor:
    params.put(SENSOR_MODE_PARAM, desired_sensor, block=True)
    restart.extend(("camerad", "encoderd"))
  if params.get(ENCODER_MODE_PARAM, return_default=True) != desired_encoder:
    params.put(ENCODER_MODE_PARAM, desired_encoder, block=True)
    restart.append("encoderd")
  return list(dict.fromkeys(restart))


def manager_thread() -> None:
  cloudlog.bind(daemon="manager")
  cloudlog.info("manager start")
  cloudlog.info({"environ": os.environ})

  params = Params()

  ignore: list[str] = []
  if params.get("DongleId") in (None, UNREGISTERED_DONGLE_ID):
    ignore += ["manage_athenad", "uploader"]
  if os.getenv("NOBOARD") is not None:
    ignore.append("pandad")
  ignore += [x for x in os.getenv("BLOCK", "").split(",") if len(x) > 0]

  sm = messaging.SubMaster(['deviceState', 'carParams', 'pandaStates', 'camcorderState'], poll='deviceState')
  pm = messaging.PubMaster(['managerState'])

  params.put_bool("IsOffroad", True, block=True)
  # Ignition is unknown until pandaStates arrives (manager may restart mid-drive).
  update_camcorder_sensor_mode(True, params)
  ensure_running(managed_processes.values(), False, params=params, CP=sm['carParams'], not_run=ignore)

  started_prev = False
  ignition_prev = False

  while True:
    sm.update(1000)

    started = sm['deviceState'].started

    if started and not started_prev:
      params.clear_all(ParamKeyFlag.CLEAR_ON_ONROAD_TRANSITION)
    elif not started and started_prev:
      params.clear_all(ParamKeyFlag.CLEAR_ON_OFFROAD_TRANSITION)

    ignition = any(ps.ignitionLine or ps.ignitionCan for ps in sm['pandaStates'] if ps.pandaType != log.PandaState.PandaType.unknown)
    if ignition and not ignition_prev:
      params.clear_all(ParamKeyFlag.CLEAR_ON_IGNITION_ON)
    if ignition:
      # Leases are offroad-only. Revoke every loop so a racing or wedged UI
      # cannot keep them alive into this ignition cycle or the next offroad.
      revoke_encoder()
      revoke_camcorder()

    # update offroad state for services that don't subscribe to deviceState
    if started != started_prev:
      params.put_bool("IsOffroad", not started, block=True)

    stock_required = camcorder_stock_required(started, ignition, sm.seen['pandaStates'])
    take_active = sm.alive['camcorderState'] and str(sm['camcorderState'].phase) in _TAKE_PHASES
    mode_restarts = update_camcorder_sensor_mode(stock_required, params, take_active)
    not_run = ignore + ignition_blocked_processes(started, ignition) + drive_start_restarted_processes(started, started_prev) + mode_restarts

    started_prev = started
    ignition_prev = ignition

    ensure_running(managed_processes.values(), started, params=params, CP=sm['carParams'], not_run=not_run)

    running = ' '.join("{}{}\u001b[0m".format("\u001b[32m" if p.proc.is_alive() else "\u001b[31m", p.name)
                       for p in managed_processes.values() if p.proc)
    print(running)
    cloudlog.debug(running)

    # send managerState
    msg = messaging.new_message('managerState', valid=True)
    msg.managerState.processes = [p.get_process_state_msg() for p in managed_processes.values()]
    pm.send('managerState', msg)

    # kick AGNOS power monitoring watchdog
    try:
      if sm.all_checks(['deviceState']):
        with atomic_write("/var/tmp/power_watchdog", "w", overwrite=True) as f:
          f.write(str(time.monotonic()))
    except Exception:
      pass

    # Exit main loop when uninstall/shutdown/reboot is needed
    shutdown = False
    for param in ("DoUninstall", "DoShutdown", "DoReboot"):
      if params.get_bool(param):
        shutdown = True
        params.put("LastManagerExitReason", f"{param} {datetime.datetime.now()}", block=True)
        cloudlog.warning(f"Shutting down manager - {param} set")

    if shutdown:
      break


def main() -> None:
  manager_init()
  if os.getenv("PREPAREONLY") is not None:
    return

  # SystemExit on sigterm
  signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(1))

  try:
    manager_thread()
  except Exception:
    traceback.print_exc()
    cloudlog.exception("crash")
  finally:
    manager_cleanup()

  params = Params()
  if params.get_bool("DoUninstall"):
    cloudlog.warning("uninstalling")
    HARDWARE.uninstall()
  elif params.get_bool("DoReboot"):
    cloudlog.warning("reboot")
    HARDWARE.reboot()
  elif params.get_bool("DoShutdown"):
    cloudlog.warning("shutdown")
    HARDWARE.shutdown()


if __name__ == "__main__":
  unblock_stdout()

  try:
    main()
  except KeyboardInterrupt:
    print("got CTRL-C, exiting")
  except Exception:
    add_file_handler(cloudlog)
    cloudlog.exception("Manager failed to start")

    try:
      managed_processes['ui'].stop()
    except Exception:
      pass

    # Show last 3 lines of traceback
    error = traceback.format_exc(-3)
    error = "Manager failed to start\n\n" + error
    with TextWindow(error) as t:
      t.wait_for_exit()

    raise

  # manual exit because we are forked
  sys.exit(0)
