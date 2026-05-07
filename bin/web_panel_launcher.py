#!/usr/bin/env python3
"""Desktop-лаунчер для локальной панели Redroller.

Лаунчер управляет тем, что сам запустил: если он поднял web_panel.py, то
остановит сервер после закрытия окна приложения. Если панель уже запущена на
выбранном адресе, лаунчер переиспользует её и не завершает чужой процесс.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import List, Optional, Tuple


RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
ROOT = RESOURCE_ROOT
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
APP_NAME = "Redroller"
LEGACY_APP_NAME = "IP_ROTATOR.V1"
PANEL_CHILD_ARG = "--redroller-panel-child"
HUNTER_CHILD_ARG = "--redroller-hunter-child"
HUNTER_COMMAND_ENV = "REDROLLER_HUNTER_COMMAND_JSON"


def local_app_data_dir() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data)
    return Path.home() / "AppData" / "Local"


def migrate_legacy_runtime(runtime_dir: Path) -> None:
    legacy_dir = local_app_data_dir() / LEGACY_APP_NAME / ".web-runtime"
    if runtime_dir.exists() or not legacy_dir.exists():
        return
    runtime_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(legacy_dir, runtime_dir)
    except OSError:
        return


def default_runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        runtime_dir = local_app_data_dir() / APP_NAME / ".web-runtime"
        migrate_legacy_runtime(runtime_dir)
        return runtime_dir
    return APP_DIR / ".web-runtime"


def panel_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def status_url(host: str, port: int) -> str:
    return f"{panel_url(host, port)}/api/status"


def is_panel_running(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with urllib.request.urlopen(status_url(host, port), timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except (OSError, urllib.error.URLError):
        return False


def wait_for_panel(host: str, port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_panel_running(host, port, timeout=0.5):
            return True
        time.sleep(0.25)
    return False


def python_command() -> List[str]:
    if not getattr(sys, "frozen", False):
        return [sys.executable]
    base_executable = str(getattr(sys, "_base_executable", "") or "")
    if base_executable and Path(base_executable).exists() and "python" in Path(base_executable).name.lower():
        return [base_executable]
    python = shutil.which("python")
    if python:
        return [python]
    py_launcher = shutil.which("py")
    if py_launcher:
        return [py_launcher, "-3"]
    raise RuntimeError("Для запуска серверной части Redroller нужен установленный Python.")


def start_panel(host: str, port: int, runtime_dir: Path) -> subprocess.Popen:
    if getattr(sys, "frozen", False):
        command = [
            str(sys.executable),
            PANEL_CHILD_ARG,
            "--host",
            host,
            "--port",
            str(port),
            "--runtime-dir",
            str(runtime_dir),
        ]
        env = os.environ.copy()
        env[HUNTER_COMMAND_ENV] = json.dumps([str(sys.executable), HUNTER_CHILD_ARG])
    else:
        command = [
            *python_command(),
            str(ROOT / "web_panel.py"),
            "--host",
            host,
            "--port",
            str(port),
            "--runtime-dir",
            str(runtime_dir),
        ]
        env = None
    creationflags = 0
    if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    runtime_dir.mkdir(parents=True, exist_ok=True)
    out_log = runtime_dir / "launcher-web-panel.out.log"
    err_log = runtime_dir / "launcher-web-panel.err.log"
    out_handle = out_log.open("ab")
    err_handle = err_log.open("ab")
    try:
        return subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=out_handle,
            stderr=err_handle,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            env=env,
        )
    finally:
        out_handle.close()
        err_handle.close()


def candidate_browsers() -> List[Path]:
    paths = []
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.getenv(env_name)
        if not base:
            continue
        paths.extend(
            [
                Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ]
        )
    return paths


def find_app_browser() -> Optional[Path]:
    for path in candidate_browsers():
        if path.exists():
            return path
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def open_app_window(url: str, profile_dir: Path) -> Tuple[Optional[subprocess.Popen], bool]:
    browser = find_app_browser()
    if browser:
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            str(browser),
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-extensions",
            "--disable-background-mode",
        ]
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), True
    webbrowser.open(url, new=2, autoraise=True)
    return None, False


def terminate_process(process: subprocess.Popen, timeout: float = 8.0) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        process.wait(timeout=timeout)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def run_launcher(host: str, port: int, runtime_dir: Path) -> int:
    already_running = is_panel_running(host, port)
    panel_process: Optional[subprocess.Popen] = None
    if not already_running:
        panel_process = start_panel(host, port, runtime_dir)
        if not wait_for_panel(host, port):
            terminate_process(panel_process)
            print(
                f"{APP_NAME}: веб-панель не запустилась. Проверьте {runtime_dir / 'launcher-web-panel.err.log'}",
                file=sys.stderr,
            )
            return 1

    url = panel_url(host, port)
    app_process, owns_window = open_app_window(url, runtime_dir / "browser-profile")
    try:
        if app_process is not None:
            app_process.wait()
        else:
            print(f"{APP_NAME}: открыто {url}. Нажмите Ctrl+C здесь, чтобы остановить запущенный сервер.")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        if app_process is not None and app_process.poll() is None:
            terminate_process(app_process)
        if panel_process is not None and not already_running:
            terminate_process(panel_process)
    return 0 if owns_window or already_running else 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Запустить Redroller как desktop-приложение.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-dir", default=str(default_runtime_dir()))
    return parser.parse_args(argv)


def run_panel_child(argv: List[str]) -> int:
    import web_panel

    return web_panel.main(argv)


def run_hunter_child(argv: List[str]) -> int:
    import yc_ip_hunter

    return yc_ip_hunter.main(argv)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == [PANEL_CHILD_ARG]:
        return run_panel_child(argv[1:])
    if argv[:1] == [HUNTER_CHILD_ARG]:
        return run_hunter_child(argv[1:])
    args = parse_args(argv)
    return run_launcher(args.host, int(args.port), Path(args.runtime_dir).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
