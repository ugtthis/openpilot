from types import SimpleNamespace
from unittest.mock import Mock, patch

from openpilot.system.manager.process import RESTART_INTERVAL_S, PythonProcess


def test_opt_in_python_process_restarts_after_an_unexpected_exit():
  process = PythonProcess("test", "test.module", lambda *args: True, restart=True)
  process.proc = SimpleNamespace(exitcode=-9)
  replacement = Mock()

  with patch("openpilot.system.manager.process.Process", return_value=replacement):
    process.start()

  assert process.proc is replacement
  replacement.start.assert_called_once()


def test_crash_looping_process_restarts_at_most_once_per_interval():
  process = PythonProcess("test", "test.module", lambda *args: True, restart=True)

  with (
    patch("openpilot.system.manager.process.Process") as create_process,
    patch("openpilot.system.manager.process.time.monotonic", return_value=100.0) as now,
  ):
    process.start()
    process.proc.exitcode = 1
    now.return_value = 100.0 + RESTART_INTERVAL_S - 0.1
    process.start()
    assert create_process.call_count == 1

    now.return_value = 100.0 + RESTART_INTERVAL_S
    process.start()
    assert create_process.call_count == 2


def test_python_process_keeps_the_default_no_restart_policy():
  process = PythonProcess("test", "test.module", lambda *args: True)
  exited = SimpleNamespace(exitcode=-9)
  process.proc = exited

  with patch("openpilot.system.manager.process.Process") as create_process:
    process.start()

  assert process.proc is exited
  create_process.assert_not_called()
