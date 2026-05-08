import importlib.util
import base64
import json
import re
import tempfile
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
    def test_build_script_prepares_clean_release_folder(self):
        script = Path(__file__).with_name("build_web_panel_exe.ps1").read_text(encoding="utf-8")

        self.assertIn('$ReleasePath = Join-Path $FinalDistPath "release"', script)
        self.assertIn("Remove-Item -LiteralPath $ReleasePath -Recurse -Force", script)
        self.assertIn('Join-Path $ReleasePath "$Name.exe"', script)
        self.assertIn('Join-Path $ReleasePath "README.txt"', script)
        match = re.search(r'\$ReadmeBase64 = "([^"]+)"', script)
        self.assertIsNotNone(match)
        readme = base64.b64decode(match.group(1)).decode("utf-8")
        self.assertIn("%LOCALAPPDATA%\\Redroller\\.web-runtime", readme)

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

    def test_frozen_start_panel_uses_bundled_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp) / "runtime"
            exe_path = r"C:\apps\Redroller.exe"
            process = FakeProcess()

            with mock.patch.object(launcher.sys, "frozen", True, create=True), \
                mock.patch.object(launcher.sys, "executable", exe_path), \
                mock.patch.object(launcher.subprocess, "Popen", return_value=process) as popen:
                result = launcher.start_panel("127.0.0.1", 8797, runtime_dir)

            self.assertIs(result, process)
            command = popen.call_args.args[0]
            self.assertEqual(
                command,
                [
                    exe_path,
                    launcher.PANEL_CHILD_ARG,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8797",
                    "--runtime-dir",
                    str(runtime_dir),
                ],
            )
            env = popen.call_args.kwargs["env"]
            self.assertEqual(
                json.loads(env[launcher.HUNTER_COMMAND_ENV]),
                [exe_path, launcher.HUNTER_CHILD_ARG],
            )

    def test_main_dispatches_hidden_children(self):
        with mock.patch.object(launcher, "run_panel_child", return_value=11) as panel:
            code = launcher.main([launcher.PANEL_CHILD_ARG, "--port", "8797"])
        self.assertEqual(code, 11)
        panel.assert_called_once_with(["--port", "8797"])

        with mock.patch.object(launcher, "run_hunter_child", return_value=12) as hunter:
            code = launcher.main([launcher.HUNTER_CHILD_ARG, "--config", "config.json"])
        self.assertEqual(code, 12)
        hunter.assert_called_once_with(["--config", "config.json"])

    def test_start_panel_passes_user_config_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            runtime_dir = Path(tmp) / "runtime"
            app_dir.mkdir()
            config_path = app_dir / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            process = FakeProcess()

            with mock.patch.object(launcher, "APP_DIR", app_dir), \
                mock.patch.object(launcher.subprocess, "Popen", return_value=process) as popen:
                result = launcher.start_panel("127.0.0.1", 8797, runtime_dir)

            self.assertIs(result, process)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env[launcher.USER_CONFIG_ENV], str(config_path.resolve()))

    def test_frozen_default_runtime_dir_uses_local_app_data(self):
        with mock.patch.object(launcher.sys, "frozen", True, create=True), \
            mock.patch.dict(launcher.os.environ, {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}):
            runtime_dir = launcher.default_runtime_dir()

        self.assertEqual(
            runtime_dir,
            Path(r"C:\Users\tester\AppData\Local") / "Redroller" / ".web-runtime",
        )

    def test_frozen_default_runtime_dir_migrates_legacy_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            local_app_data = Path(tmp)
            legacy = local_app_data / "IP_ROTATOR.V1" / ".web-runtime"
            legacy.mkdir(parents=True)
            (legacy / "ip_rotator.sqlite3").write_text("db", encoding="utf-8")

            with mock.patch.object(launcher.sys, "frozen", True, create=True), \
                mock.patch.dict(launcher.os.environ, {"LOCALAPPDATA": str(local_app_data)}):
                runtime_dir = launcher.default_runtime_dir()

            self.assertEqual(runtime_dir, local_app_data / "Redroller" / ".web-runtime")
            self.assertEqual((runtime_dir / "ip_rotator.sqlite3").read_text(encoding="utf-8"), "db")

    def test_macos_default_runtime_dir_uses_application_support(self):
        with mock.patch.object(launcher.sys, "platform", "darwin"), \
            mock.patch.dict(launcher.os.environ, {}, clear=True), \
            mock.patch.object(launcher.Path, "home", return_value=Path("/Users/tester")):
            self.assertEqual(
                launcher.local_app_data_dir(),
                Path("/Users/tester") / "Library" / "Application Support",
            )

    def test_macos_candidate_browsers_include_app_bundles(self):
        with mock.patch.object(launcher.sys, "platform", "darwin"), \
            mock.patch.object(launcher.Path, "home", return_value=Path("/Users/tester")):
            candidates = launcher.candidate_browsers()

        self.assertIn(
            Path("/Applications") / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
            candidates,
        )
        self.assertIn(
            Path("/Users/tester") / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge",
            candidates,
        )


if __name__ == "__main__":
    unittest.main()
