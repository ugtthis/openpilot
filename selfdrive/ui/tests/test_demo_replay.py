from openpilot.selfdrive.ui.mici.layouts.settings import demo_replay


class FakeProc:
  def __init__(self, running=True):
    self.running = running

  def poll(self):
    return None if self.running else 0

  def terminate(self):
    self.running = False

  def wait(self, timeout=None):
    self.running = False
    return 0


class FakeParams:
  def __init__(self):
    self.values = {}

  def put_bool(self, key, value):
    self.values[key] = value


def test_demo_replay_start_prefetch_failure_does_not_raise(monkeypatch):
  params = FakeParams()
  calls = []

  def fake_popen(args, **kwargs):
    calls.append(args)
    if args[2] == demo_replay._FILE_DOWNLOADER_MODULE:
      raise OSError("prefetch unavailable")
    return FakeProc(running=True)

  monkeypatch.setattr(demo_replay, "Params", lambda: params)
  monkeypatch.setattr(demo_replay.subprocess, "Popen", fake_popen)

  controller = demo_replay.DemoReplayController()
  controller.start()

  assert controller.is_running
  assert params.values["DemoReplayActive"] is True
  assert any(args[0] == demo_replay._REPLAY_BINARY for args in calls)


def test_demo_replay_does_not_prefetch_on_init(monkeypatch):
  params = FakeParams()
  calls = []

  def fake_popen(args, **kwargs):
    calls.append(args)
    return FakeProc(running=True)

  monkeypatch.setattr(demo_replay, "Params", lambda: params)
  monkeypatch.setattr(demo_replay.subprocess, "Popen", fake_popen)

  demo_replay.DemoReplayController()

  assert calls == []


def test_demo_replay_reuses_running_prefetch_process(monkeypatch):
  params = FakeParams()
  prefetch_proc = FakeProc(running=True)
  calls = []

  def fake_popen(args, **kwargs):
    calls.append(args)
    if args[2] == demo_replay._FILE_DOWNLOADER_MODULE:
      return prefetch_proc
    return FakeProc(running=True)

  monkeypatch.setattr(demo_replay, "Params", lambda: params)
  monkeypatch.setattr(demo_replay.subprocess, "Popen", fake_popen)

  controller = demo_replay.DemoReplayController()
  controller.start()

  prefetch_calls = [args for args in calls if args[2] == demo_replay._FILE_DOWNLOADER_MODULE]
  replay_calls = [args for args in calls if args[0] == demo_replay._REPLAY_BINARY]

  assert len(prefetch_calls) == 1
  assert len(replay_calls) == 1
