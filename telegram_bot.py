#!/usr/bin/env python3
"""Small Telegram control panel for yc_ip_hunter.py.

This file intentionally uses only the Python standard library. It is meant to
be a thin private wrapper around the existing hunter script: start/stop a
configured account, inspect state/logs, export target subnets, and request a
manual rotation via control files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_CONFIG = "telegram_bot_config.json"
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TELEGRAM_MAX_TEXT = 3900
STALE_CALLBACK_MARKERS = (
    "query is too old",
    "query id is invalid",
    "response timeout expired",
)


class BotConfigError(RuntimeError):
    pass


class TelegramError(RuntimeError):
    pass


def load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise BotConfigError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BotConfigError(f"Config file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise BotConfigError("Config root must be an object.")
    return data


def resolve_path(base: Path, value: Optional[str], default: str = "") -> Path:
    raw = value or default
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-").lower()
    return slug or "account"


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information,
                False,
                int(pid),
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            pass
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def tail_lines(path: Path, count: int = 80) -> List[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-count:]


def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def read_optional_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def format_count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def make_progress_bar(current: int, total: int, width: int = 8) -> str:
    if total <= 0:
        return "░" * width
    filled = min(width, round(width * current / total))
    return "█" * filled + "░" * (width - filled)


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clamp_telegram_text(text: str, limit: int = TELEGRAM_MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n...обрезано, открой лог для полной версии."
    return text[: max(0, limit - len(suffix))] + suffix


def format_log_line(line: str) -> str:
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d{3}\s+(\w+)\s+(.*)$",
        line,
    )
    if not match:
        return line[-320:]
    _, clock, level, message = match.groups()
    return f"[{clock}] {level} {message}"[-360:]


def parse_rfc3339(value: str) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def local_clock() -> str:
    return dt.datetime.now().astimezone().strftime("%H:%M:%S")


def datetime_epoch(value: Optional[dt.datetime]) -> Optional[float]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.astimezone()
    return value.timestamp()


def log_line_epoch(line: str) -> Optional[float]:
    match = re.match(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d{3}\b", line)
    if not match:
        return None
    try:
        naive = dt.datetime.strptime(" ".join(match.groups()), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    tz = dt.datetime.now().astimezone().tzinfo
    return naive.replace(tzinfo=tz).timestamp()


def is_stale_callback_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in STALE_CALLBACK_MARKERS)


class TelegramApi:
    def __init__(self, token: str, proxy_url: str = "") -> None:
        if not token:
            raise BotConfigError("Telegram token is empty.")
        self.base_url = f"https://api.telegram.org/bot{urllib.parse.quote(token, safe='')}/"
        if proxy_url:
            proxy = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            self.opener = urllib.request.build_opener(proxy)
        else:
            self.opener = urllib.request.build_opener()

    def request(
        self,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Path]] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        url = self.base_url + method
        body: bytes
        headers: Dict[str, str]
        if files:
            body, content_type = self._multipart_body(payload or {}, files)
            headers = {"Content-Type": content_type}
        else:
            body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json"}

        last_error: Optional[TelegramError] = None
        for attempt in range(1, 3):
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    raw = response.read().decode("utf-8")
                    break
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                raise TelegramError(f"Telegram HTTP {exc.code}: {raw}") from exc
            except urllib.error.URLError as exc:
                last_error = TelegramError(f"Telegram network error: {exc.reason}")
                if attempt < 2:
                    time.sleep(1.5)
                    continue
                raise last_error from exc
            except OSError as exc:
                last_error = TelegramError(f"Telegram connection error: {exc}")
                if attempt < 2:
                    time.sleep(1.5)
                    continue
                raise last_error from exc
        else:
            raise last_error or TelegramError("Telegram request failed.")

        data = json.loads(raw) if raw else {}
        if not data.get("ok"):
            raise TelegramError(str(data))
        result = data.get("result")
        return result if isinstance(result, dict) else {"result": result}

    def _multipart_body(
        self, payload: Dict[str, Any], files: Dict[str, Path]
    ) -> Tuple[bytes, str]:
        boundary = uuid.uuid4().hex
        parts: List[bytes] = []
        for name, value in payload.items():
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        for field_name, path in files.items():
            filename = path.name
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    (
                        f'Content-Disposition: form-data; name="{field_name}"; '
                        f'filename="{filename}"\r\n'
                    ).encode("utf-8"),
                    b"Content-Type: application/octet-stream\r\n\r\n",
                    path.read_bytes(),
                    b"\r\n",
                ]
            )
        parts.append(f"--{boundary}--\r\n".encode("ascii"))
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def get_updates(self, offset: int, timeout: int) -> List[Dict[str, Any]]:
        result = self.request(
            "getUpdates",
            {
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=timeout + 10,
        )
        updates = result.get("result") if isinstance(result, dict) else result
        return updates if isinstance(updates, list) else []

    def download_file(self, file_id: str) -> bytes:
        info = self.request("getFile", {"file_id": file_id})
        file_path = info.get("file_path") or ""
        if not file_path:
            raise TelegramError("getFile returned no file_path")
        token = self.base_url.split("/bot", 1)[1].rstrip("/")
        url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        req = urllib.request.Request(url)
        with self.opener.open(req, timeout=30) as resp:
            return resp.read()

    def send_message(
        self,
        chat_id: Any,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": clamp_telegram_text(text),
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.request("sendMessage", payload, timeout=30)

    def edit_message(
        self,
        chat_id: Any,
        message_id: Any,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": clamp_telegram_text(text),
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            return self.request("editMessageText", payload, timeout=30)
        except TelegramError as exc:
            if "message is not modified" in str(exc).lower():
                return {}
            raise

    def send_chat_action(self, chat_id: Any, action: str = "typing") -> bool:
        try:
            self.request(
                "sendChatAction",
                {"chat_id": chat_id, "action": action},
                timeout=10,
            )
            return True
        except TelegramError as exc:
            print(f"Telegram chat action failed: {exc}", file=sys.stderr)
            return False

    def send_message_draft(self, chat_id: Any, draft_id: int, text: str) -> bool:
        try:
            self.request(
                "sendMessageDraft",
                {
                    "chat_id": chat_id,
                    "draft_id": draft_id,
                    "text": clamp_telegram_text(text, limit=900),
                },
                timeout=4,
            )
            return True
        except TelegramError as exc:
            error_text = str(exc).lower()
            if "not found" not in error_text and "network error" not in error_text:
                print(f"Telegram draft update failed: {exc}", file=sys.stderr)
            return False

    def answer_callback(self, callback_id: str, text: str = "") -> bool:
        if not callback_id:
            return False
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            self.request("answerCallbackQuery", payload, timeout=10)
            return True
        except TelegramError as exc:
            if is_stale_callback_error(exc):
                return False
            print(f"Telegram callback answer failed: {exc}", file=sys.stderr)
            return False

    def send_document(
        self,
        chat_id: Any,
        path: Path,
        caption: str = "",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload: Dict[str, Any] = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
        self.request("sendDocument", payload, files={"document": path}, timeout=60)


class HunterAccount:
    def __init__(self, raw: Dict[str, Any], base_dir: Path, default_python: str) -> None:
        self.raw = raw
        self.name = str(raw.get("name") or "Main")
        self.slug = safe_slug(self.name)
        self.workdir = resolve_path(base_dir, raw.get("workdir"), ".")
        self.config_path = resolve_path(self.workdir, raw.get("config"), "config.json")
        self.script_path = resolve_path(self.workdir, raw.get("script"), "yc_ip_hunter.py")
        self.python = str(raw.get("python") or default_python or sys.executable)
        self.args = list(raw.get("args") or ["--run", "--yes-delete-cloud"])
        self.proxy_url = str(raw.get("proxy_url") or "")
        self.stop_file = resolve_path(
            self.workdir, raw.get("stop_file"), f".ip-hunter.{self.slug}.stop"
        )
        self.recreate_file = resolve_path(
            self.workdir, raw.get("recreate_file"), f".ip-hunter.{self.slug}.recreate"
        )
        self.pid_file = resolve_path(
            self.workdir, raw.get("pid_file"), f".ip-hunter.{self.slug}.pid"
        )
        self.runner_log = resolve_path(
            self.workdir, raw.get("runner_log"), f"runner.{self.slug}.log"
        )
        self.hunter_config = read_optional_json(self.config_path)
        self.state_path = resolve_path(
            self.config_path.parent,
            str(self.hunter_config.get("state_file") or "state.json"),
        )
        self.log_path = resolve_path(
            self.config_path.parent,
            str(self.hunter_config.get("log_file") or "run.log"),
        )

    def pid(self) -> Optional[int]:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    def is_running(self) -> bool:
        pid = self.pid()
        if pid is None:
            return False
        running = pid_exists(pid)
        if not running:
            self.pid_file.unlink(missing_ok=True)
        return running

    def start(self) -> Tuple[bool, str]:
        if self.is_running():
            return False, f"{self.name} уже запущен."
        if not self.config_path.exists():
            return False, f"Нет config: {self.config_path}"
        if not self.script_path.exists():
            return False, f"Нет скрипта: {self.script_path}"

        self.stop_file.unlink(missing_ok=True)
        self.recreate_file.unlink(missing_ok=True)
        self.runner_log.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self.python,
            str(self.script_path),
            "--config",
            str(self.config_path),
            *self.args,
            "--stop-file",
            str(self.stop_file),
            "--recreate-file",
            str(self.recreate_file),
        ]
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (self.raw.get("env") or {}).items()})
        if self.proxy_url:
            env["HTTP_PROXY"] = self.proxy_url
            env["HTTPS_PROXY"] = self.proxy_url

        log_handle = self.runner_log.open("ab")
        creationflags = 0
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            command,
            cwd=str(self.workdir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
        )
        self.pid_file.write_text(str(process.pid), encoding="utf-8")
        time.sleep(float(self.raw.get("startup_check_seconds") or 1.0))
        if process.poll() is not None:
            self.pid_file.unlink(missing_ok=True)
            tail = "\n".join(tail_lines(self.runner_log, 12))
            if tail:
                return False, f"{self.name} не стартовал.\n\n{tail[-1500:]}"
            return False, f"{self.name} не стартовал, код выхода {process.returncode}."
        return True, f"{self.name} запущен, PID {process.pid}."

    def stop_observed_since(self, requested_at: float) -> bool:
        state = read_optional_json(self.state_path)
        stopped_epoch = datetime_epoch(parse_rfc3339(str(state.get("stopped_at") or "")))
        if stopped_epoch is not None and stopped_epoch >= requested_at - 5:
            return True
        for path in [self.log_path, self.runner_log]:
            for line in tail_lines(path, 220):
                if "Stop requested" not in line:
                    continue
                event_epoch = log_line_epoch(line)
                if event_epoch is None or event_epoch >= requested_at - 5:
                    return True
        return False

    def stop(self, timeout: int = 45) -> Tuple[bool, str]:
        requested_at = time.time()
        self.stop_file.write_text("stop\n", encoding="utf-8")
        pid = self.pid()
        if pid is None:
            deadline = time.time() + min(timeout, 15)
            while time.time() < deadline:
                if self.stop_observed_since(requested_at):
                    self.stop_file.unlink(missing_ok=True)
                    return True, f"{self.name} остановлен по stop-file."
                time.sleep(1)
            return True, (
                f"{self.name}: stop-file создан, но PID-файл не найден. "
                "Если live-лог ещё движется, роллер должен увидеть stop-file в ближайшем ожидании."
            )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not pid_exists(pid):
                self.pid_file.unlink(missing_ok=True)
                self.stop_file.unlink(missing_ok=True)
                return True, f"{self.name} остановлен."
            if self.stop_observed_since(requested_at):
                self.pid_file.unlink(missing_ok=True)
                self.stop_file.unlink(missing_ok=True)
                return True, f"{self.name} остановлен по stop-file."
            time.sleep(1)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        time.sleep(1)
        if not pid_exists(pid):
            self.pid_file.unlink(missing_ok=True)
            self.stop_file.unlink(missing_ok=True)
            return True, f"{self.name} остановлен принудительно."
        return True, f"{self.name}: отправил принудительную остановку после ожидания."

    def recreate_now(self) -> Tuple[bool, str]:
        self.recreate_file.write_text("recreate\n", encoding="utf-8")
        if self.is_running():
            return True, f"{self.name}: запросил пересоздание текущего цикла."
        return True, f"{self.name}: флаг пересоздания создан; сработает при запуске."

    def export_targets(self) -> Path:
        target_ips = self.hunter_config.get("target_ips") or []
        target_cidrs = self.hunter_config.get("target_cidrs") or []
        exports_dir = self.workdir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        path = exports_dir / f"subnets_{self.slug}.txt"
        lines = [f"Отчет: {self.name}", ""]
        if target_cidrs:
            lines.append("CIDR:")
            lines.extend(str(item) for item in target_cidrs)
        if target_ips:
            lines.extend(["", "IP:"])
            lines.extend(str(item) for item in target_ips)
        if not target_cidrs and not target_ips:
            lines.append("В config нет target_ips/target_cidrs.")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def target_summary(self) -> str:
        target_ips = self.hunter_config.get("target_ips") or []
        target_cidrs = self.hunter_config.get("target_cidrs") or []
        return f"{len(target_cidrs)} CIDR + {len(target_ips)} IP"

    def target_ip_capacity(self) -> int:
        total = 0
        for cidr in self.hunter_config.get("target_cidrs") or []:
            try:
                total += ipaddress.ip_network(str(cidr), strict=False).num_addresses
            except ValueError:
                continue
        total += len(self.hunter_config.get("target_ips") or [])
        return total

    def settings_text(self) -> str:
        cfg = self.hunter_config
        zones = cfg.get("zones") or [cfg.get("zone") or "ru-central1-a"]
        return (
            f"⚙️ Настройки: {self.name}\n"
            f"Режим: {cfg.get('rotation_mode', 'legacy')}\n"
            f"Зоны: {', '.join(map(str, zones))}\n"
            f"Цели: {self.target_summary()}\n"
            f"Whitelist: {self.target_ip_capacity()} IP\n"
            f"Подсети: {', '.join(map(str, cfg.get('target_cidrs') or [])) or '-'}\n"
            f"Прямые IP: {', '.join(map(str, cfg.get('target_ips') or [])) or '-'}\n"
            f"Max cloud: {cfg.get('max_parallel_clouds', '-')}\n"
            f"Адресов/cloud: {cfg.get('max_addresses_per_cloud', '-')}\n"
            f"Удаление cloud: {'да' if cfg.get('allow_delete_cloud') else 'нет'}\n"
            f"Прокси: {'есть' if self.proxy_url else 'нет'}"
        )

    def log_tail_text(self, limit: int = 10) -> str:
        lines = tail_lines(self.log_path, limit)
        if not lines and self.runner_log.exists():
            lines = tail_lines(self.runner_log, limit)
        if not lines:
            return "Лог пока пуст."
        return "\n".join(format_log_line(line) for line in lines[-limit:])

    def allocation_stats_from_log(self) -> Tuple[int, int]:
        allocated_ips: List[str] = []
        for line in read_lines(self.log_path):
            if "Allocated IP " not in line and "TARGET MATCH" not in line:
                continue
            allocated_ips.extend(IP_RE.findall(line))
        return len(allocated_ips), len(set(allocated_ips))

    def recent_allocations(self) -> List[Dict[str, Any]]:
        state = read_optional_json(self.state_path)
        recent = state.get("recent_allocations")
        if isinstance(recent, list):
            return [item for item in recent if isinstance(item, dict)]
        return []

    def allocation_event_id(self, item: Dict[str, Any]) -> str:
        return "|".join(
            str(item.get(key) or "")
            for key in ["at", "address_id", "ip", "zone", "folder_id"]
        )

    def allocation_message(self, item: Dict[str, Any]) -> str:
        state = read_optional_json(self.state_path)
        log_checked, log_unique = self.allocation_stats_from_log()
        checked = max(format_count(state.get("checked_count")), log_checked)
        unique = max(format_count(state.get("unique_checked_count")), log_unique)
        ip = str(item.get("ip") or "-")
        matched = bool(item.get("matched"))

        if matched:
            cleaned = format_count(state.get("checked_count"))
            return (
                f"✅ НАЙДЕН! (1 шт.)\n\n"
                f"🌐 IP:\n - {ip}\n"
                f"📍 Зона: {item.get('zone') or '-'}\n"
                f"🧹 Очищено: {cleaned}"
            )

        # Speed: delta between last two allocations
        recent = [a for a in (state.get("recent_allocations") or []) if isinstance(a, dict)]
        speed_text = ""
        if len(recent) >= 2:
            t1 = parse_rfc3339(str(recent[-1].get("at") or ""))
            t2 = parse_rfc3339(str(recent[-2].get("at") or ""))
            if t1 and t2:
                delta = abs((t1 - t2).total_seconds())
                speed_text = f"⚡ {delta:.1f}с"

        # Duplicate marker: count how many times this IP appears in recent allocations
        seen_count = sum(1 for a in recent if a.get("ip") == ip)
        dup_marker = f" ♻ x{seen_count}" if seen_count > 1 else ""

        zone = str(item.get("zone") or "-")
        parts = [
            f"[{self.name}] 📍 {zone}:{dup_marker}",
            f"- {ip}",
            "",
            f"📦 {checked} (уник: {unique})",
        ]
        if speed_text:
            parts.append(speed_text)
        return "\n".join(parts)

    def log_event_id(self, index: int, line: str) -> str:
        return f"log|{index}|{line}"

    def log_event_message(self, line: str) -> Optional[str]:
        formatted = format_log_line(line)
        if "Created folder" in line:
            return f"[{self.name}] 📁 Новый folder\n{formatted}"
        if "Created cloud" in line:
            return f"[{self.name}] ☁️ Новый cloud\n{formatted}"
        if "Saved folder" in line and "creating a fresh working folder" in line:
            return f"[{self.name}] 🧹 Старый folder протух\n{formatted}"
        if "hit a limit; rotating cloud" in line:
            state = read_optional_json(self.state_path)
            n = format_count(state.get("cloud_recreations_done")) + format_count(state.get("manual_recreates_done")) + 1
            cloud = state.get("hybrid_cloud_id") or state.get("current_cloud_id") or "-"
            folder = state.get("hybrid_folder_id") or state.get("current_folder_id") or "-"
            return (
                f"[{self.name}] ♻️ Авто пересоздание #{n}\n"
                f"☁️ Cloud: {cloud}\n"
                f"📁 Folder: {folder}"
            )
        if "Manual recreate requested" in line:
            state = read_optional_json(self.state_path)
            n = format_count(state.get("cloud_recreations_done")) + format_count(state.get("manual_recreates_done")) + 1
            cloud = state.get("hybrid_cloud_id") or state.get("current_cloud_id") or "-"
            folder = state.get("hybrid_folder_id") or state.get("current_folder_id") or "-"
            return (
                f"[{self.name}] ♻️ Ручное пересоздание #{n}\n"
                f"☁️ Cloud: {cloud}\n"
                f"📁 Folder: {folder}"
            )
        if "Cloud quota gate is still full" in line:
            return f"[{self.name}] ⏳ Ожидание слота cloud\n{formatted}"
        if "Permission denied during create_address" in line:
            return f"[{self.name}] 🔴 Ошибка прав на создание IP\n{formatted}"
        if " ERROR " in line:
            return f"[{self.name}] 🔴 Ошибка роллера\n{formatted}"
        return None

    def notable_log_events(self) -> List[Tuple[str, str]]:
        events: List[Tuple[str, str]] = []
        lines = read_lines(self.log_path)
        start = max(0, len(lines) - 200)
        for index, line in enumerate(lines[start:], start=start):
            message = self.log_event_message(line)
            if message:
                events.append((self.log_event_id(index, line), message))
        return events

    def last_log_record(self) -> Dict[str, Any]:
        for line in reversed(tail_lines(self.log_path, 240)):
            match = re.match(
                r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}),\d{3}\s+(\w+)\s+(.*)$",
                line,
            )
            if not match:
                continue
            _, clock, level, message = match.groups()
            return {
                "clock": clock,
                "level": level,
                "message": message,
                "line": line,
                "epoch": log_line_epoch(line),
            }
        return {}

    def status(self) -> Dict[str, Any]:
        state = read_optional_json(self.state_path)
        log_tail = tail_lines(self.log_path, 160)
        log_text = "\n".join(log_tail)
        log_ips = IP_RE.findall(log_text)
        log_checked, log_unique = self.allocation_stats_from_log()

        checked = format_count(state.get("checked_count"))
        checked = max(checked, log_checked)
        unique = format_count(state.get("unique_checked_count"))
        unique = max(unique, log_unique, len(set(log_ips)) if not log_unique else log_unique)
        if unique > checked:
            checked = unique

        success = state.get("success") if isinstance(state.get("success"), dict) else {}
        found_ip = success.get("ip") or ""
        if not found_ip:
            for line in reversed(log_tail):
                if "TARGET MATCH" in line:
                    ips = IP_RE.findall(line)
                    found_ip = ips[0] if ips else ""
                    break

        last_ip = state.get("last_allocated_ip") or ""
        if not last_ip:
            for line in reversed(log_tail):
                if "Allocated IP " in line or "TARGET MATCH" in line:
                    ips = IP_RE.findall(line)
                    last_ip = ips[0] if ips else ""
                    break

        cloud_count = ""
        for line in reversed(log_tail):
            marker = "Organization cloud count for rotation:"
            if marker in line:
                cloud_count = line.split(marker, 1)[1].strip().split(".", 1)[0]
                break

        recent_error = ""
        for line in reversed(log_tail):
            if "ERROR" in line or "WARNING" in line:
                recent_error = line.split(" ", 3)[-1] if len(line.split(" ", 3)) >= 4 else line
                break
        last_log = self.last_log_record()
        running = self.is_running()
        failed = (not running) and last_log.get("level") == "ERROR"

        recent_allocations = self.recent_allocations()
        last_allocation = recent_allocations[-1] if recent_allocations else {}
        created_clouds = state.get("created_clouds")
        last_created_cloud = created_clouds[-1] if isinstance(created_clouds, list) and created_clouds else {}
        active_cloud = (
            last_allocation.get("cloud_id")
            or last_created_cloud.get("cloud_id")
            or state.get("current_cloud_id")
            or state.get("hybrid_cloud_id")
        )
        active_folder = (
            last_allocation.get("folder_id")
            or state.get("current_folder_id")
            or state.get("hybrid_folder_id")
        )

        # Count ALL allocations in current cloud (incl. deleted) for progress bar
        addresses_by_cloud = state.get("addresses_by_cloud") or {}
        cloud_addr_list = addresses_by_cloud.get(str(active_cloud or ""), [])
        if not isinstance(cloud_addr_list, list):
            cloud_addr_list = []
        cloud_addresses = len(cloud_addr_list)
        max_cloud_addresses = format_count(self.hunter_config.get("max_addresses_per_cloud") or 9)

        return {
            "running": running,
            "pid": self.pid(),
            "failed": failed,
            "last_log": last_log,
            "checked": checked,
            "unique": unique,
            "found_ip": found_ip,
            "last_ip": last_ip,
            "last_zone": state.get("last_zone") or self.hunter_config.get("zone") or "",
            "folder": active_folder or "",
            "cloud": active_cloud or "",
            "recreates": format_count(state.get("cloud_recreations_done"))
            + format_count(state.get("manual_recreates_done")),
            "cloud_count": cloud_count,
            "cloud_addresses": cloud_addresses,
            "max_cloud_addresses": max_cloud_addresses,
            "recent_error": recent_error,
            "log_tail": self.log_tail_text(6),
            "success": bool(success or found_ip),
            "updated_at": local_clock(),
        }

    def dashboard_text(self) -> str:
        status = self.status()
        active = "🟢" if status["running"] else ("🔴" if status["failed"] else "⚪")
        found = status["found_ip"] or "0"
        zones = self.hunter_config.get("zones") or [self.hunter_config.get("zone") or "-"]
        # Folder tag: mark roll-* folders as ephemeral
        folder_str = status["folder"] or "-"
        folder_name = folder_str.split("/")[-1] if "/" in folder_str else folder_str
        is_ephemeral = folder_name.startswith("roll-") or folder_name.startswith("ip-hunter")
        folder_label = f"{folder_str} (эфемерный)" if is_ephemeral and folder_str != "-" else folder_str
        # Auth type from config
        auth_cfg = self.hunter_config.get("auth") or {}
        auth_type = "service-account" if auth_cfg.get("service_account_key_file") else "oauth"
        # Progress bar for current cloud
        ca = status["cloud_addresses"]
        max_ca = status["max_cloud_addresses"]
        bar = make_progress_bar(ca, max_ca)
        lines = [
            f"🎛 ДАШБОРД: {self.name}",
            f"{active} Активен · 📍 {', '.join(map(str, zones))}",
            f"☁️ Cloud: {status['cloud'] or '-'}",
            f"📁 Folder: {folder_label}",
            f"🔐 Auth: {auth_type}",
            f"♻️ Пересозданий: {status['recreates']}",
            f"📊 В текущем облаке: {bar} {ca}/{max_ca}",
            "",
            f"📦 Проверено: {status['checked']} IP (уник: {status['unique']})",
            f"🎯 Найдено: {found} IP",
            f"📋 Whitelist: {self.target_ip_capacity()} IP в {len(self.hunter_config.get('target_cidrs') or [])} подсетях",
            f"🌐 Прокси: {'есть' if self.proxy_url else 'нет'}",
            f"🕒 Обновлено: {status['updated_at']}",
        ]
        if status["last_ip"]:
            lines.append(f"Последний IP: {status['last_ip']} ({status['last_zone'] or '-'})")
        if status["recent_error"]:
            lines.append(f"Последнее: {status['recent_error'][:180]}")
        return "\n".join(lines)

    def log_view_text(self) -> str:
        status = self.status()
        active = "🟢" if status["running"] else ("🔴" if status["failed"] else "⚪")
        log_lines = html_escape(self.log_tail_text(14))
        return (
            f"{active} <b>Лог: {html_escape(self.name)}</b>\n"
            f"🔄 Обновлено: {status['updated_at']}\n"
            f"📦 Проверено: <b>{status['checked']}</b> IP (уник: {status['unique']})\n"
            f"🕒 Последний IP: <code>{html_escape(status['last_ip'] or '-')}</code>\n"
            f"\n<pre>{log_lines}</pre>"
        )

    def monitor_text(self) -> str:
        status = self.status()
        if status["running"]:
            active = "🟢 работает"
        elif status["failed"]:
            active = "🔴 ошибка"
        else:
            active = "⚪ остановлен"
        last_zone_suffix = f" ({status['last_zone']})" if status["last_ip"] else ""
        zones = self.hunter_config.get("zones") or [self.hunter_config.get("zone") or "-"]
        ca = status["cloud_addresses"]
        max_ca = status["max_cloud_addresses"]
        bar = make_progress_bar(ca, max_ca)
        log_lines = html_escape(self.log_tail_text(12))
        return (
            f"🎛 <b>{html_escape(self.name)}</b>: {active}\n"
            f"📍 Зоны: {html_escape(', '.join(map(str, zones)))}\n"
            f"☁️ Cloud: <code>{html_escape(status['cloud'] or '-')}</code>\n"
            f"📁 Folder: <code>{html_escape(status['folder'] or '-')}</code>\n"
            f"\n"
            f"📦 Проверено: <b>{status['checked']}</b> IP (уник: {status['unique']})\n"
            f"📋 Цели: {self.target_summary()} · Whitelist: {self.target_ip_capacity()} IP\n"
            f"♻️ Пересозданий: {status['recreates']} · {bar} {ca}/{max_ca}\n"
            f"🕒 Последний IP: <code>{html_escape(status['last_ip'] or '-')}</code>{html_escape(last_zone_suffix)}\n"
            f"🔄 Обновлено: {status['updated_at']}\n"
            f"\n"
            f"📋 <b>Живой лог:</b>\n<pre>{log_lines}</pre>"
        )

    def draft_text(self) -> str:
        status = self.status()
        active = "работает" if status["running"] else ("ошибка" if status["failed"] else "остановлен")
        last_line = self.log_tail_text(1)
        return (
            f"{self.name}: {active} · {status['checked']} / {status['unique']}\n"
            f"IP: {status['last_ip'] or '-'} · {status['updated_at']}\n"
            f"{last_line}"
        )


class ControlBot:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = load_json(config_path)
        base_dir = config_path.parent.resolve()
        token_env = str(self.config.get("bot_token_env") or "TELEGRAM_BOT_TOKEN")
        token = str(self.config.get("bot_token") or os.getenv(token_env) or "").strip()
        self.allowed_chat_ids = {str(item) for item in self.config.get("allowed_chat_ids") or []}
        self.allow_any_chat = bool(self.config.get("allow_any_chat", False))
        self.poll_timeout = int(self.config.get("poll_timeout_seconds", 25))
        self.stop_timeout = int(self.config.get("stop_timeout_seconds", 45))
        self.live_log_interval = float(self.config.get("live_log_interval_seconds") or 5)
        self.drop_pending_updates_on_start = bool(
            self.config.get("drop_pending_updates_on_start", True)
        )
        self.offset_file = resolve_path(
            base_dir,
            self.config.get("offset_file"),
            ".telegram_bot.offset",
        )
        default_python = str(self.config.get("python") or sys.executable)
        account_configs = self.config.get("accounts") or []
        if not isinstance(account_configs, list) or not account_configs:
            raise BotConfigError("Config must contain at least one account.")
        self.accounts = [
            HunterAccount(raw, base_dir=base_dir, default_python=default_python)
            for raw in account_configs
            if isinstance(raw, dict)
        ]
        self.api = TelegramApi(token, proxy_url=str(self.config.get("telegram_proxy_url") or ""))
        self.monitor_threads: Dict[Tuple[str, int], threading.Thread] = {}
        self.sent_events: Dict[str, set[str]] = {}
        self.wizard_state: Dict[str, Dict[str, Any]] = {}  # chat_id -> wizard data

    def is_allowed(self, chat_id: Any) -> bool:
        return self.allow_any_chat or str(chat_id) in self.allowed_chat_ids

    def load_offset(self) -> Optional[int]:
        if not self.offset_file.exists():
            return None
        try:
            return int(self.offset_file.read_text(encoding="utf-8").strip())
        except ValueError:
            return None

    def save_offset(self, offset: int) -> None:
        self.offset_file.write_text(str(offset), encoding="utf-8")

    def initial_offset(self) -> int:
        saved = self.load_offset()
        if saved is not None:
            return saved
        if not self.drop_pending_updates_on_start:
            return 0
        try:
            updates = self.api.get_updates(-1, 0)
        except TelegramError as exc:
            print(f"Telegram pending update cleanup failed: {exc}", file=sys.stderr)
            return 0
        offset = 0
        for update in updates:
            offset = max(offset, int(update.get("update_id", 0)) + 1)
        if offset:
            self.save_offset(offset)
        return offset

    def accounts_text(self) -> str:
        lines = [f"👥 Аккаунты ({len(self.accounts)}):"]
        for account in self.accounts:
            status = account.status()
            active = "🟢" if status["running"] else ("🔴" if status["failed"] else "⚪")
            found = " ✅" if status["success"] else ""
            lines.append(
                f"{active} {account.name} · ♻️ {status['recreates']} · "
                f"{status['checked']} / {status['unique']}{found}"
            )
        return "\n".join(lines)

    def accounts_keyboard(self) -> Dict[str, Any]:
        rows = []
        for index, account in enumerate(self.accounts):
            rows.append([{"text": account.name, "callback_data": f"acct:{index}"}])
        rows.append(
            [
                {"text": "⏹ Остановить все", "callback_data": "all:stop"},
                {"text": "➕ Добавить", "callback_data": "help:add"},
            ]
        )
        return {"inline_keyboard": rows}

    def account_keyboard(self, index: int) -> Dict[str, Any]:
        account = self.accounts[index]
        start_stop = (
            {"text": "⏸ Остановить", "callback_data": f"act:{index}:stop"}
            if account.is_running()
            else {"text": "▶️ Запустить", "callback_data": f"act:{index}:start"}
        )
        return {
            "inline_keyboard": [
                [start_stop, {"text": "📊 Обновить", "callback_data": f"act:{index}:status"}],
                [{"text": "🧾 Лог", "callback_data": f"act:{index}:log"}],
                [
                    {"text": "⚙️ Настройки", "callback_data": f"act:{index}:settings"},
                    {"text": "📥 Экспорт", "callback_data": f"act:{index}:export"},
                ],
                [
                    {"text": "🌐 Прокси", "callback_data": f"act:{index}:proxy"},
                    {"text": "♻️ Пересоздать сейчас", "callback_data": f"act:{index}:recreate"},
                ],
                [
                    {"text": "🗑 Удалить", "callback_data": f"act:{index}:delete"},
                    {"text": "⬅️ К списку", "callback_data": "home"},
                ],
            ]
        }

    def monitor_keyboard(self, index: int) -> Dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "⏸ Остановить", "callback_data": f"act:{index}:stop"},
                    {"text": "📊 Обновить", "callback_data": f"act:{index}:monitor"},
                ],
                [
                    {"text": "♻️ Пересоздать сейчас", "callback_data": f"act:{index}:recreate"},
                    {"text": "⬅️ Карточка", "callback_data": f"acct:{index}"},
                ],
            ]
        }

    def send_home(self, chat_id: Any) -> None:
        self.api.send_message(chat_id, self.accounts_text(), self.accounts_keyboard())

    def show_home(self, chat_id: Any, message_id: Optional[Any] = None) -> None:
        if message_id:
            self.api.edit_message(chat_id, message_id, self.accounts_text(), self.accounts_keyboard())
        else:
            self.send_home(chat_id)

    def send_account(self, chat_id: Any, index: int) -> None:
        account = self.accounts[index]
        self.api.send_message(chat_id, account.dashboard_text(), self.account_keyboard(index), parse_mode="HTML")

    def show_account(self, chat_id: Any, index: int, message_id: Optional[Any] = None) -> None:
        account = self.accounts[index]
        if message_id:
            self.api.edit_message(chat_id, message_id, account.dashboard_text(), self.account_keyboard(index), parse_mode="HTML")
        else:
            self.send_account(chat_id, index)

    def show_text(
        self,
        chat_id: Any,
        text: str,
        reply_markup: Optional[Dict[str, Any]],
        message_id: Optional[Any] = None,
        parse_mode: str = "",
    ) -> None:
        if message_id:
            self.api.edit_message(chat_id, message_id, text, reply_markup, parse_mode=parse_mode)
        else:
            self.api.send_message(chat_id, text, reply_markup, parse_mode=parse_mode)

    def send_monitor(self, chat_id: Any, index: int) -> Optional[Any]:
        account = self.accounts[index]
        result = self.api.send_message(
            chat_id,
            account.monitor_text(),
            self.monitor_keyboard(index),
            parse_mode="HTML",
        )
        message_id = result.get("message_id")
        if message_id:
            self.start_monitor_thread(chat_id, index, message_id)
        return message_id

    def start_monitor_thread(self, chat_id: Any, index: int, message_id: Any) -> None:
        try:
            numeric_message_id = int(message_id)
        except (TypeError, ValueError):
            return
        key = (str(chat_id), numeric_message_id)
        existing = self.monitor_threads.get(key)
        if existing and existing.is_alive():
            return
        self.prime_event_cache(index)
        thread = threading.Thread(
            target=self.monitor_loop,
            args=(chat_id, index, message_id),
            daemon=True,
        )
        self.monitor_threads[key] = thread
        thread.start()

    def event_cache_key(self, index: int) -> str:
        return self.accounts[index].slug

    def prime_event_cache(self, index: int) -> None:
        account = self.accounts[index]
        cache = self.sent_events.setdefault(self.event_cache_key(index), set())
        for item in account.recent_allocations():
            event_id = account.allocation_event_id(item)
            if event_id:
                cache.add(event_id)
        for event_id, _ in account.notable_log_events():
            cache.add(event_id)

    def emit_new_events(self, chat_id: Any, index: int) -> None:
        account = self.accounts[index]
        cache = self.sent_events.setdefault(self.event_cache_key(index), set())
        for item in account.recent_allocations()[-25:]:
            event_id = account.allocation_event_id(item)
            if not event_id or event_id in cache:
                continue
            cache.add(event_id)
            self.api.send_message(chat_id, account.allocation_message(item))
        for event_id, message in account.notable_log_events()[-25:]:
            if event_id in cache:
                continue
            cache.add(event_id)
            self.api.send_message(chat_id, message)

    def monitor_loop(self, chat_id: Any, index: int, message_id: Any) -> None:
        account = self.accounts[index]
        try:
            draft_id = (index + 1) * 100_000_000 + int(message_id)
        except (TypeError, ValueError):
            draft_id = index + 1
        while True:
            try:
                self.api.send_chat_action(chat_id, "typing")
                self.api.send_message_draft(chat_id, draft_id, account.draft_text())
                self.emit_new_events(chat_id, index)
                self.api.edit_message(
                    chat_id,
                    message_id,
                    account.monitor_text(),
                    self.monitor_keyboard(index),
                    parse_mode="HTML",
                )
            except TelegramError as exc:
                print(f"Live monitor update failed: {exc}", file=sys.stderr)
            except Exception:
                traceback.print_exc()
            if not account.is_running():
                return
            time.sleep(max(2.0, self.live_log_interval))

    # ── Add-account wizard ────────────────────────────────────────────────────

    WIZARD_STEPS = [
        ("await_name",     "✏️ Шаг 1/5 — Введите имя аккаунта (например: Account2):"),
        ("await_org",      "🏢 Шаг 2/5 — Введите organization_id нового аккаунта:"),
        ("await_billing",  "💳 Шаг 3/5 — Введите billing_account_id:"),
        ("await_cloud",    "☁️ Шаг 4/5 — Введите service_cloud_id (ID сервисного облака):"),
        ("await_key",      "🔑 Шаг 5/5 — Отправьте файл sa-key.json для нового аккаунта:"),
    ]
    CANCEL_KB = {"inline_keyboard": [[{"text": "❌ Отмена", "callback_data": "wizard:cancel"}]]}

    def start_add_wizard(self, chat_id: Any) -> None:
        self.wizard_state[str(chat_id)] = {"step": "await_name"}
        self.api.send_message(chat_id, self.WIZARD_STEPS[0][1], self.CANCEL_KB)

    def _wizard_cancel(self, chat_id: Any, message_id: Any = None) -> None:
        self.wizard_state.pop(str(chat_id), None)
        self.show_text(chat_id, "❌ Добавление отменено.", self.accounts_keyboard(), message_id)

    def process_wizard_step(self, chat_id: Any, message: Dict[str, Any]) -> None:
        key = str(chat_id)
        state = self.wizard_state.get(key)
        if not state:
            return
        step = state.get("step")
        text = str(message.get("text") or "").strip()

        if step == "await_name":
            if not text:
                self.api.send_message(chat_id, "Имя не может быть пустым. Попробуйте ещё раз:", self.CANCEL_KB)
                return
            state["name"] = text
            state["step"] = "await_org"
            self.api.send_message(chat_id, self.WIZARD_STEPS[1][1], self.CANCEL_KB)

        elif step == "await_org":
            if not text:
                self.api.send_message(chat_id, "Введите organization_id:", self.CANCEL_KB)
                return
            state["org_id"] = text
            state["step"] = "await_billing"
            self.api.send_message(chat_id, self.WIZARD_STEPS[2][1], self.CANCEL_KB)

        elif step == "await_billing":
            if not text:
                self.api.send_message(chat_id, "Введите billing_account_id:", self.CANCEL_KB)
                return
            state["billing_id"] = text
            state["step"] = "await_cloud"
            self.api.send_message(chat_id, self.WIZARD_STEPS[3][1], self.CANCEL_KB)

        elif step == "await_cloud":
            if not text:
                self.api.send_message(chat_id, "Введите service_cloud_id:", self.CANCEL_KB)
                return
            state["cloud_id"] = text
            state["step"] = "await_key"
            self.api.send_message(chat_id, self.WIZARD_STEPS[4][1], self.CANCEL_KB)

        elif step == "await_key":
            doc = message.get("document")
            if not doc:
                self.api.send_message(chat_id, "Пришлите файл sa-key.json (как документ).", self.CANCEL_KB)
                return
            try:
                self.finish_add_wizard(chat_id, state, doc)
            except Exception as exc:  # noqa: BLE001
                self.wizard_state.pop(key, None)
                self.api.send_message(chat_id, f"❌ Ошибка при добавлении: {exc}\n\nПопробуйте снова через кнопку Добавить.")

    def finish_add_wizard(self, chat_id: Any, state: Dict[str, Any], doc: Dict[str, Any]) -> None:
        key = str(chat_id)
        base_dir = self.config_path.parent
        n = len(self.accounts) + 1

        # Save sa-key file
        key_data = self.api.download_file(str(doc.get("file_id") or ""))
        try:
            json.loads(key_data)
        except json.JSONDecodeError as exc:
            raise ValueError("Файл не является валидным JSON") from exc
        key_path = base_dir / f"sa-key{n}.json"
        key_path.write_bytes(key_data)

        # Build new config from existing one as template
        template_cfg: Dict[str, Any] = {}
        if self.accounts:
            try:
                template_cfg = json.loads(self.accounts[0].config_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                template_cfg = {}
        template_cfg.update({
            "organization_id": state["org_id"],
            "billing_account_id": state["billing_id"],
            "service_cloud_id": state["cloud_id"],
            "target_cloud_id": state["cloud_id"],
            "cloud_id": "",
            "folder_id": "",
            "auth": {
                "service_account_key_file": key_path.name,
                "iam_token_env": "YC_IAM_TOKEN",
            },
        })
        cfg_path = base_dir / f"config{n}.json"
        cfg_path.write_text(json.dumps(template_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

        # Add account to telegram_bot_config.json
        bot_cfg = load_json(self.config_path)
        accounts_list = bot_cfg.get("accounts") or []
        slug = safe_slug(str(state["name"]))
        new_account: Dict[str, Any] = {
            "name": state["name"],
            "workdir": ".",
            "config": cfg_path.name,
            "script": "yc_ip_hunter.py",
            "args": ["--run", "--yes-delete-cloud"],
            "proxy_url": "",
        }
        accounts_list.append(new_account)
        bot_cfg["accounts"] = accounts_list
        self.config_path.write_text(json.dumps(bot_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

        # Reload accounts in-place
        default_python = str(bot_cfg.get("python") or sys.executable)
        new_acct = HunterAccount(new_account, base_dir=base_dir, default_python=default_python)
        self.accounts.append(new_acct)
        self.wizard_state.pop(key, None)

        self.api.send_message(
            chat_id,
            f"✅ Аккаунт «{state['name']}» добавлен!\n\n"
            f"📄 Конфиг: {cfg_path.name}\n"
            f"🔑 Ключ: {key_path.name}\n\n"
            "Выберите его в списке и нажмите Запустить.",
            self.accounts_keyboard(),
        )

    # ── End wizard ────────────────────────────────────────────────────────────

    def handle_message(self, message: Dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not self.is_allowed(chat_id):
            self.api.send_message(
                chat_id,
                f"Этот чат не добавлен в allowed_chat_ids.\nChat ID: {chat_id}",
            )
            return
        # Active wizard takes priority over normal commands
        if str(chat_id) in self.wizard_state:
            self.process_wizard_step(chat_id, message)
            return
        text = str(message.get("text") or "")
        if text.startswith("/id"):
            self.api.send_message(chat_id, f"Chat ID: {chat_id}")
            return
        self.send_home(chat_id)

    def handle_callback(self, callback: Dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "")
        message = callback.get("message") or {}
        message_id = message.get("message_id")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not self.is_allowed(chat_id):
            self.api.answer_callback(callback_id, "Нет доступа.")
            return
        data = str(callback.get("data") or "")
        self.api.answer_callback(callback_id)

        if data == "home":
            self.show_home(chat_id, message_id)
            return
        if data == "all:stop":
            replies = [account.stop(self.stop_timeout)[1] for account in self.accounts]
            self.show_text(chat_id, "\n".join(replies), self.accounts_keyboard(), message_id)
            return
        if data == "help:add":
            self.start_add_wizard(chat_id)
            return
        if data == "wizard:cancel":
            self._wizard_cancel(chat_id, message_id)
            return
        if data.startswith("acct:"):
            self.show_account(chat_id, int(data.split(":", 1)[1]), message_id)
            return
        if not data.startswith("act:"):
            self.show_home(chat_id, message_id)
            return

        _, index_raw, action = data.split(":", 2)
        index = int(index_raw)
        account = self.accounts[index]
        if action == "start":
            ok, text = account.start()
            if ok:
                self.show_text(
                    chat_id,
                    f"{text}\n\nLive-лог открыт отдельным сообщением ниже.",
                    self.account_keyboard(index),
                    message_id,
                )
                self.send_monitor(chat_id, index)
            else:
                self.show_text(chat_id, text, self.account_keyboard(index), message_id)
        elif action == "stop":
            _, text = account.stop(self.stop_timeout)
            self.show_text(chat_id, f"{text}\n\n{account.monitor_text()}", self.monitor_keyboard(index), message_id, parse_mode="HTML")
        elif action == "status":
            self.show_account(chat_id, index, message_id)
        elif action == "monitor":
            self.show_text(chat_id, account.monitor_text(), self.monitor_keyboard(index), message_id, parse_mode="HTML")
            if account.is_running() and message_id:
                self.start_monitor_thread(chat_id, index, message_id)
        elif action == "log":
            self.show_text(chat_id, account.log_view_text(), self.account_keyboard(index), message_id, parse_mode="HTML")
        elif action == "settings":
            self.show_text(chat_id, account.settings_text(), self.account_keyboard(index), message_id)
        elif action == "export":
            path = account.export_targets()
            self.api.send_document(chat_id, path, f"📁 Отчет: {account.name}", self.account_keyboard(index))
            self.show_account(chat_id, index, message_id)
        elif action == "proxy":
            text = f"🌐 Прокси: {'есть' if account.proxy_url else 'нет'}"
            self.show_text(chat_id, text, self.account_keyboard(index), message_id)
        elif action == "recreate":
            _, text = account.recreate_now()
            self.show_text(chat_id, f"{text}\n\n{account.monitor_text()}", self.monitor_keyboard(index), message_id, parse_mode="HTML")
        elif action == "delete":
            self.show_text(
                chat_id,
                "Удаление аккаунта делается через telegram_bot_config.json, чтобы случайно не убрать рабочий профиль.",
                self.account_keyboard(index),
                message_id,
            )
        else:
            self.show_account(chat_id, index, message_id)

    def run(self) -> None:
        print(f"Telegram control bot is running with {len(self.accounts)} account(s).")
        offset = self.initial_offset()
        while True:
            try:
                updates = self.api.get_updates(offset, self.poll_timeout)
            except TelegramError as exc:
                print(exc, file=sys.stderr)
                time.sleep(5)
                continue
            for update in updates:
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                self.save_offset(offset)
                try:
                    if "message" in update:
                        self.handle_message(update["message"])
                    elif "callback_query" in update:
                        self.handle_callback(update["callback_query"])
                except TelegramError as exc:
                    print(f"Telegram send/edit failed: {exc}", file=sys.stderr)
                    continue
                except Exception:
                    traceback.print_exc()
                    continue


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram control panel for yc_ip_hunter.py")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        ControlBot(Path(args.config).resolve()).run()
        return 0
    except KeyboardInterrupt:
        return 130
    except BotConfigError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
