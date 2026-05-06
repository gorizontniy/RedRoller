import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("web_panel_launcher.py")
SPEC = importlib.util.spec_from_file_location("web_panel_launcher_module", MODULE_PATH)
launcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


class FakeProcess:
    def __init__(self):
        self.wait_called = False
        self.terminated = False
        self.killed = False
        self._poll = None

    def wait(self, timeout=None):
        self.wait_called = True
        self._poll = 0
        return 0

    def poll(self):
        return self._poll

    def terminate(self):
        self.terminated = True
        self._poll = 0

    def kill(self):
        self.killed = True
        self._poll = -9


class WebPanelLauncherTests(unittest.TestCase):
    def test_panel_url_helpers(self):
        self.assertEqual(launcher.panel_url("127.0.0.1", 8787), "http://127.0.0.1:8787")
        self.assertEqual(
            launcher.status_url("127.0.0.1", 8787),
            "http://127.0.0.1:8787/api/status",
        )

    def test_run_launcher_reuses_existing_panel_without_terminating_server(self):
        app_process = FakeProcess()

        with mock.patch.object(launcher, "is_panel_running", return_value=True), \
            mock.patch.object(launcher, "start_panel") as start_panel, \
            mock.patch.object(launcher, "open_app_window", return_value=(app_process, True)), \
            mock.patch.object(launcher, "terminate_process") as terminate:
            code = launcher.run_launcher("127.0.0.1", 8787, Path("runtime"))

        self.assertEqual(code, 0)
        self.assertTrue(app_process.wait_called)
        start_panel.assert_not_called()
        terminate.assert_not_called()

    def test_run_launcher_stops_owned_panel_after_window_exit(self):
        panel_process = FakeProcess()
        app_process = FakeProcess()

        with mock.patch.object(launcher, "is_panel_running", return_value=False), \
            mock.patch.object(launcher, "start_panel", return_value=panel_process), \
            mock.patch.object(launcher, "wait_for_panel", return_value=True), \
            mock.patch.object(launcher, "open_app_window", return_value=(app_process, True)), \
            mock.patch.object(launcher, "terminate_process") as terminate:
            code = launcher.run_launcher("127.0.0.1", 8787, Path("runtime"))

        self.assertEqual(code, 0)
        self.assertTrue(app_process.wait_called)
        terminate.assert_called_once_with(panel_process)

    def test_run_launcher_cleans_up_when_panel_fails_to_start(self):
        panel_process = FakeProcess()

        with mock.patch.object(launcher, "is_panel_running", return_value=False), \
            mock.patch.object(launcher, "start_panel", return_value=panel_process), \
            mock.patch.object(launcher, "wait_for_panel", return_value=False), \
            mock.patch.object(launcher, "terminate_process") as terminate:
            code = launcher.run_launcher("127.0.0.1", 8787, Path("runtime"))

        self.assertEqual(code, 1)
        terminate.assert_called_once_with(panel_process)

    def test_frozen_default_runtime_dir_uses_local_app_data(self):
        with mock.patch.object(launcher.sys, "frozen", True, create=True), \
            mock.patch.dict(launcher.os.environ, {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}):
            runtime_dir = launcher.default_runtime_dir()

        self.assertEqual(
            runtime_dir,
            Path(r"C:\Users\tester\AppData\Local") / "IP_ROTATOR.V1" / ".web-runtime",
        )


if __name__ == "__main__":
    unittest.main()
