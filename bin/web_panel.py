#!/usr/bin/env python3
"""Локальная веб-панель для yc_ip_hunter.py.

Панель остаётся тонким управляющим слоем: данные аккаунтов хранятся в SQLite,
а ротация IP запускается через существующий CLI yc_ip_hunter.py.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import importlib.util
import ipaddress
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken


ROOT = Path(__file__).resolve().parent
DEFAULT_RUNTIME_DIR = ROOT / ".web-runtime"
DEFAULT_DB_NAME = "ip_rotator.sqlite3"
DEFAULT_WEB_DIR = ROOT / "web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
SCHEMA_VERSION = 7
DEFAULT_ZONES = ["ru-central1-a", "ru-central1-b", "ru-central1-c", "ru-central1-d", "ru-central1-e"]
YC_LIKE_ID_RE = re.compile(r"^[a-z0-9-]{6,64}$")
ROLL_MODES = {"cloud", "project"}
_YC_IP_HUNTER_MODULE: Optional[Any] = None


class WebPanelError(RuntimeError):
    pass


class WebPanelNotFound(WebPanelError):
    pass


def load_yc_ip_hunter_module() -> Any:
    global _YC_IP_HUNTER_MODULE
    if _YC_IP_HUNTER_MODULE is not None:
        return _YC_IP_HUNTER_MODULE
    module_path = ROOT / "yc_ip_hunter.py"
    spec = importlib.util.spec_from_file_location("yc_ip_hunter_module", module_path)
    if spec is None or spec.loader is None:
        raise WebPanelError("Не удалось загрузить Yandex Cloud cleanup-модуль.")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    _YC_IP_HUNTER_MODULE = module
    return module


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_json_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            value = json.loads(stripped)
        else:
            return [item.strip() for item in re.split(r"[\n,]+", stripped) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise WebPanelError("Ожидался список или строки, разделённые переносами.")


def config_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def unique_text_list(values: List[str]) -> List[str]:
    result = []
    seen = set()
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def normalize_yc_like_ids(value: Any, field_name: str, label: str) -> List[str]:
    if not isinstance(value, list):
        raise WebPanelError(f"{field_name} должен быть списком.")
    result = unique_text_list([str(item).strip() for item in value])
    invalid = [item for item in result if not YC_LIKE_ID_RE.fullmatch(item)]
    if invalid:
        raise WebPanelError(
            f"Некорректный {label} в изоляции: "
            + ", ".join(invalid)
            + ". Разрешены только a-z, 0-9 и дефис, длина 6-64 символа."
        )
    return result


def normalize_protected_cloud_ids(value: Any) -> List[str]:
    return normalize_yc_like_ids(value, "protected_cloud_ids", "cloud-id")


def normalize_protected_folder_ids(value: Any) -> List[str]:
    return normalize_yc_like_ids(value, "protected_folder_ids", "folder-id")


def normalize_target_cidrs(value: Any) -> List[str]:
    result = []
    seen = set()
    invalid = []
    for item in unique_text_list(parse_json_list(value)):
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            invalid.append(item)
            continue
        if network.version != 4:
            invalid.append(item)
            continue
        normalized = str(network)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    if invalid:
        raise WebPanelError("Некорректный целевой CIDR: " + ", ".join(invalid))
    return result


def normalize_target_ips(value: Any) -> List[str]:
    result = []
    seen = set()
    invalid = []
    for item in unique_text_list(parse_json_list(value)):
        try:
            address = ipaddress.ip_address(item)
        except ValueError:
            invalid.append(item)
            continue
        if address.version != 4:
            invalid.append(item)
            continue
        normalized = str(address)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    if invalid:
        raise WebPanelError("Некорректный целевой IP: " + ", ".join(invalid))
    return result


def target_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    target_cidrs = normalize_target_cidrs(payload.get("target_cidrs") or payload.get("targetCidrs"))
    target_ips = normalize_target_ips(payload.get("target_ips") or payload.get("targetIps"))
    if not target_cidrs and not target_ips:
        raise WebPanelError("Укажите хотя бы один целевой CIDR или IP.")
    return {
        "target_cidrs_json": json.dumps(target_cidrs),
        "target_ips_json": json.dumps(target_ips),
    }


def target_address_count(target_cidrs: List[str], target_ips: List[str]) -> int:
    ranges = []
    for cidr in target_cidrs:
        ranges.append(ipaddress.ip_network(str(cidr), strict=False))
    for ip in target_ips:
        ranges.append(ipaddress.ip_network(f"{ip}/32", strict=False))
    return sum(network.num_addresses for network in ipaddress.collapse_addresses(ranges))


def format_target_address_count(count: int) -> str:
    if count <= 0:
        return "-"
    return f"{count:,}".replace(",", " ") + " IP"


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_tail(path: Path, limit: int = 80) -> List[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []


def write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def safe_unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def backup_sqlite_before_schema_change(db_path: Path) -> Optional[Path]:
    if not db_path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.backup-{stamp}")
    backup_path.write_bytes(db_path.read_bytes())
    return backup_path


def mask_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    if len(text) <= 8:
        return text
    return f"{text[:4]}...{text[-3:]}"


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def load_or_create_fernet(runtime_dir: Path) -> Fernet:
    env_key = os.getenv("REDROLLER_SECRET_KEY") or os.getenv("IP_ROTATOR_SECRET_KEY")
    if env_key:
        return Fernet(env_key.encode("ascii"))
    runtime_dir.mkdir(parents=True, exist_ok=True)
    key_path = runtime_dir / "secret.key"
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key + b"\n")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
    return Fernet(key)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            organization_id TEXT NOT NULL,
            billing_account_id TEXT NOT NULL,
            service_cloud_id TEXT NOT NULL,
            target_cloud_id TEXT NOT NULL DEFAULT '',
            folder_id TEXT NOT NULL DEFAULT '',
            target_cidrs_json TEXT NOT NULL DEFAULT '[]',
            target_ips_json TEXT NOT NULL DEFAULT '[]',
            zones_json TEXT NOT NULL DEFAULT '[]',
            protected_cloud_ids_json TEXT NOT NULL DEFAULT '[]',
            protected_folder_ids_json TEXT NOT NULL DEFAULT '[]',
            continue_after_success INTEGER NOT NULL DEFAULT 0,
            roll_mode TEXT NOT NULL DEFAULT 'cloud',
            service_account_json_encrypted BLOB NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            pid INTEGER,
            status TEXT NOT NULL,
            runtime_dir TEXT NOT NULL,
            config_path TEXT NOT NULL,
            state_path TEXT NOT NULL,
            log_path TEXT NOT NULL,
            runner_log_path TEXT NOT NULL,
            stop_file TEXT NOT NULL,
            recreate_file TEXT NOT NULL,
            started_at TEXT NOT NULL,
            stopped_at TEXT,
            exit_code INTEGER
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            attempt_key TEXT NOT NULL UNIQUE,
            attempt_number INTEGER NOT NULL,
            at TEXT NOT NULL,
            ip TEXT NOT NULL,
            zone TEXT NOT NULL,
            cloud_id TEXT NOT NULL,
            folder_id TEXT NOT NULL,
            address_id TEXT NOT NULL,
            matched INTEGER NOT NULL DEFAULT 0,
            cleanup_status TEXT NOT NULL DEFAULT '',
            cleanup_at TEXT NOT NULL DEFAULT '',
            cleanup_error TEXT NOT NULL DEFAULT '',
            cloud_cleanup_status TEXT NOT NULL DEFAULT '',
            cloud_cleanup_at TEXT NOT NULL DEFAULT '',
            cloud_cleanup_error TEXT NOT NULL DEFAULT ''
        );
        """
    )
    ensure_column(conn, "accounts", "zones_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "accounts", "protected_cloud_ids_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "accounts", "protected_folder_ids_json", "TEXT NOT NULL DEFAULT '[]'")
    ensure_column(conn, "accounts", "continue_after_success", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "accounts", "roll_mode", "TEXT NOT NULL DEFAULT 'cloud'")
    ensure_column(conn, "attempts", "cleanup_status", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "attempts", "cleanup_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "attempts", "cleanup_error", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "attempts", "cloud_cleanup_status", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "attempts", "cloud_cleanup_at", "TEXT NOT NULL DEFAULT ''")
    ensure_column(conn, "attempts", "cloud_cleanup_error", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class WebPanelApp:
    def __init__(
        self,
        root: Path = ROOT,
        runtime_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
        web_dir: Optional[Path] = None,
        python: Optional[str] = None,
    ) -> None:
        self.root = root.resolve()
        self.runtime_dir = (runtime_dir or self.root / ".web-runtime").resolve()
        self.db_path = (db_path or self.runtime_dir / DEFAULT_DB_NAME).resolve()
        self.web_dir = (web_dir or self.root / "web").resolve()
        self.python = python or sys.executable
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.fernet = load_or_create_fernet(self.runtime_dir)
        self._lock = threading.Lock()
        self._processes: Dict[int, subprocess.Popen[Any]] = {}
        self.backup_database_if_needed()
        with self.connect() as conn:
            init_db(conn)

    def backup_database_if_needed(self) -> None:
        if not self.db_path.exists():
            return
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            account_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(accounts)")}
            attempt_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(attempts)")}
        if (
            "roll_mode" not in account_columns
            or "protected_folder_ids_json" not in account_columns
            or "continue_after_success" not in account_columns
            or "cleanup_status" not in attempt_columns
            or "cloud_cleanup_status" not in attempt_columns
        ):
            backup_sqlite_before_schema_change(self.db_path)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()

    def encrypt_service_account(self, raw_json: str) -> bytes:
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            raise WebPanelError("JSON-ключ сервисного аккаунта должен быть объектом.")
        missing = [key for key in ("id", "private_key", "service_account_id") if not data.get(key)]
        if missing:
            raise WebPanelError(f"В JSON-ключе не хватает полей: {', '.join(missing)}")
        return self.fernet.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def decrypt_service_account(self, token: bytes) -> str:
        try:
            return self.fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise WebPanelError("Не удалось расшифровать сохранённый JSON-ключ сервисного аккаунта.") from exc

    def encrypt_text(self, text: str) -> bytes:
        return self.fernet.encrypt(text.encode("utf-8"))

    def decrypt_text(self, token: bytes) -> str:
        try:
            return self.fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise WebPanelError("Could not decrypt saved settings.") from exc

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row is not None else default

    def set_settings(self, values: Dict[str, str]) -> None:
        with self._lock, self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)",
                    (key, value),
                )
            conn.commit()

    def telegram_settings(self, include_token: bool = False) -> Dict[str, Any]:
        token_encrypted = self.get_setting("telegram_bot_token_encrypted", "")
        result: Dict[str, Any] = {
            "enabled": config_bool(self.get_setting("telegram_enabled", "false"), default=False),
            "chat_id": self.get_setting("telegram_chat_id", ""),
            "has_bot_token": bool(token_encrypted),
        }
        if include_token:
            result["bot_token"] = self.decrypt_text(token_encrypted.encode("utf-8")) if token_encrypted else ""
        return result

    def public_telegram_settings(self) -> Dict[str, Any]:
        return self.telegram_settings(include_token=False)

    def update_telegram_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        current = self.telegram_settings(include_token=False)
        enabled = config_bool(payload.get("enabled"), default=False)
        chat_id = str(payload.get("chat_id") or payload.get("chatId") or "").strip()
        token = str(payload.get("bot_token") or payload.get("botToken") or "").strip()
        clear_token = config_bool(payload.get("clear_bot_token") or payload.get("clearBotToken"), default=False)
        values = {
            "telegram_enabled": "true" if enabled else "false",
            "telegram_chat_id": chat_id,
            "telegram_updated_at": utc_now(),
        }
        if clear_token:
            values["telegram_bot_token_encrypted"] = ""
        elif token:
            values["telegram_bot_token_encrypted"] = self.encrypt_text(token).decode("ascii")
        elif not current.get("has_bot_token"):
            values["telegram_bot_token_encrypted"] = ""
        self.set_settings(values)
        return {"ok": True, "telegram": self.public_telegram_settings()}

    def send_telegram_message(self, text: str) -> bool:
        settings = self.telegram_settings(include_token=True)
        token = str(settings.get("bot_token") or "").strip()
        chat_id = str(settings.get("chat_id") or "").strip()
        if not token or not chat_id:
            raise WebPanelError("Укажите Telegram bot token и chat_id.")
        url = f"https://api.telegram.org/bot{urllib.parse.quote(token, safe='')}/sendMessage"
        body = json.dumps(
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return 200 <= int(response.status) < 300
        except urllib.error.HTTPError as exc:
            raise WebPanelError(f"Telegram вернул ошибку {exc.code}.") from exc
        except OSError as exc:
            raise WebPanelError(f"Не удалось отправить Telegram-сообщение: {exc}") from exc

    def test_telegram_settings(self) -> Dict[str, Any]:
        self.send_telegram_message("Redroller: тестовое Telegram-уведомление")
        return {"ok": True}

    def default_zones(self) -> List[str]:
        template = read_json(self.root / "config.example.json")
        zones = template.get("zones") or []
        if not zones and template.get("zone"):
            zones = [template["zone"]]
        return unique_text_list(parse_json_list(zones) if zones else DEFAULT_ZONES)

    def account_payload(self, payload: Dict[str, Any], require_key: bool) -> Dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        org_id = str(payload.get("organization_id") or payload.get("organizationId") or "").strip()
        billing_id = str(payload.get("billing_account_id") or payload.get("billingAccountId") or "").strip()
        service_cloud_id = str(payload.get("service_cloud_id") or payload.get("serviceCloudId") or "").strip()
        if not name:
            raise WebPanelError("Укажите название аккаунта.")
        if not org_id:
            raise WebPanelError("Укажите ID организации.")
        if not billing_id:
            raise WebPanelError("Укажите ID платёжного аккаунта.")
        if not service_cloud_id:
            raise WebPanelError("Укажите ID сервисного облака.")
        roll_mode = str(payload.get("roll_mode") or payload.get("rollMode") or "cloud").strip().lower()
        if roll_mode not in ROLL_MODES:
            raise WebPanelError("roll_mode must be 'cloud' or 'project'.")
        target_cloud_id = str(payload.get("target_cloud_id") or payload.get("targetCloudId") or "").strip()
        folder_id = str(payload.get("folder_id") or payload.get("folderId") or "").strip()
        if roll_mode == "cloud":
            target_cloud_id = ""
            folder_id = ""
        elif not target_cloud_id or not folder_id:
            raise WebPanelError("For project roll mode, target_cloud_id and folder_id are required.")
        service_account_json = str(
            payload.get("service_account_json") or payload.get("serviceAccountJson") or ""
        ).strip()
        if require_key and not service_account_json:
            raise WebPanelError("JSON-ключ сервисного аккаунта обязателен.")
        zones_source = payload.get("zones")
        if zones_source is None:
            zones_source = payload.get("zones_json") or payload.get("zonesJson") or self.default_zones()
        zones = unique_text_list(parse_json_list(zones_source))
        if not zones:
            raise WebPanelError("Выберите хотя бы одну зону.")
        targets = target_payload(payload)
        continue_after_success = config_bool(
            payload.get("continue_after_success", payload.get("continueAfterSuccess")),
            default=False,
        )
        return {
            "name": name,
            "organization_id": org_id,
            "billing_account_id": billing_id,
            "service_cloud_id": service_cloud_id,
            "roll_mode": roll_mode,
            "target_cloud_id": target_cloud_id,
            "folder_id": folder_id,
            "target_cidrs_json": targets["target_cidrs_json"],
            "target_ips_json": targets["target_ips_json"],
            "zones_json": json.dumps(zones),
            "continue_after_success": continue_after_success,
            "service_account_json": service_account_json,
        }

    def public_account(self, row: sqlite3.Row) -> Dict[str, Any]:
        account_id = int(row["id"])
        running = bool(self.latest_run(account_id, only_active=True))
        return {
            "id": account_id,
            "name": row["name"],
            "organization_id": row["organization_id"],
            "billing_account_id": row["billing_account_id"],
            "service_cloud_id": row["service_cloud_id"],
            "roll_mode": row["roll_mode"] or "cloud",
            "target_cloud_id": row["target_cloud_id"],
            "folder_id": row["folder_id"],
            "organization_masked": mask_id(row["organization_id"]),
            "billing_masked": mask_id(row["billing_account_id"]),
            "service_cloud_masked": mask_id(row["service_cloud_id"]),
            "target_cloud_masked": mask_id(row["target_cloud_id"]),
            "folder_masked": mask_id(row["folder_id"]),
            "target_cidrs": json.loads(row["target_cidrs_json"] or "[]"),
            "target_ips": json.loads(row["target_ips_json"] or "[]"),
            "zones": json.loads(row["zones_json"] or "[]") or self.default_zones(),
            "protected_cloud_ids": json.loads(row["protected_cloud_ids_json"] or "[]"),
            "protected_folder_ids": json.loads(row["protected_folder_ids_json"] or "[]"),
            "continue_after_success": bool(row["continue_after_success"]),
            "is_active": bool(row["is_active"]),
            "running": running,
            "has_service_account_json": True,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_account(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = self.account_payload(payload, require_key=True)
        encrypted = self.encrypt_service_account(data.pop("service_account_json"))
        now = utc_now()
        with self._lock, self.connect() as conn:
            active_count = conn.execute("SELECT COUNT(*) FROM accounts WHERE is_active=1").fetchone()[0]
            cursor = conn.execute(
                """
                INSERT INTO accounts(
                    name, organization_id, billing_account_id, service_cloud_id,
                    roll_mode, target_cloud_id, folder_id, target_cidrs_json, target_ips_json,
                    zones_json, protected_cloud_ids_json, protected_folder_ids_json, continue_after_success,
                    service_account_json_encrypted, is_active, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["name"],
                    data["organization_id"],
                    data["billing_account_id"],
                    data["service_cloud_id"],
                    data["roll_mode"],
                    data["target_cloud_id"],
                    data["folder_id"],
                    data["target_cidrs_json"],
                    data["target_ips_json"],
                    data["zones_json"],
                    json.dumps(normalize_protected_cloud_ids(payload.get("protected_cloud_ids", []))),
                    json.dumps(normalize_protected_folder_ids(payload.get("protected_folder_ids", []))),
                    1 if data["continue_after_success"] else 0,
                    encrypted,
                    1 if active_count == 0 else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self.public_account(row)

    def update_account(self, account_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = self.account_payload(payload, require_key=False)
        service_account_json = data.pop("service_account_json")
        now = utc_now()
        with self._lock, self.connect() as conn:
            self.require_account(conn, account_id)
            fields = [
                "name=?",
                "organization_id=?",
                "billing_account_id=?",
                "service_cloud_id=?",
                "roll_mode=?",
                "target_cloud_id=?",
                "folder_id=?",
                "target_cidrs_json=?",
                "target_ips_json=?",
                "zones_json=?",
                "continue_after_success=?",
                "updated_at=?",
            ]
            values: List[Any] = [
                data["name"],
                data["organization_id"],
                data["billing_account_id"],
                data["service_cloud_id"],
                data["roll_mode"],
                data["target_cloud_id"],
                data["folder_id"],
                data["target_cidrs_json"],
                data["target_ips_json"],
                data["zones_json"],
                1 if data["continue_after_success"] else 0,
                now,
            ]
            if service_account_json:
                fields.append("service_account_json_encrypted=?")
                values.append(self.encrypt_service_account(service_account_json))
            values.append(account_id)
            conn.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id=?", values)
            conn.commit()
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return self.public_account(row)

    def update_account_isolation(self, account_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        if "protected_cloud_ids" not in payload and "protected_folder_ids" not in payload:
            raise WebPanelError("Укажите protected_cloud_ids или protected_folder_ids.")
        protected_cloud_ids: Optional[List[str]] = None
        protected_folder_ids: Optional[List[str]] = None
        if "protected_cloud_ids" in payload:
            protected_cloud_ids = normalize_protected_cloud_ids(payload["protected_cloud_ids"])
        if "protected_folder_ids" in payload:
            protected_folder_ids = normalize_protected_folder_ids(payload["protected_folder_ids"])
        now = utc_now()
        with self._lock, self.connect() as conn:
            row = self.require_account(conn, account_id)
            fields = ["updated_at=?"]
            values: List[Any] = [now]
            if protected_cloud_ids is not None:
                fields.insert(0, "protected_cloud_ids_json=?")
                values.insert(0, json.dumps(protected_cloud_ids))
            if protected_folder_ids is not None:
                fields.insert(0, "protected_folder_ids_json=?")
                values.insert(0, json.dumps(protected_folder_ids))
            values.append(account_id)
            conn.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id=?", values)
            conn.commit()
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        account = self.public_account(row)
        return {
            "ok": True,
            "account": {
                "id": account["id"],
                "name": account["name"],
                "protected_cloud_ids": account["protected_cloud_ids"],
                "protected_folder_ids": account["protected_folder_ids"],
            },
        }

    def update_account_targets(self, account_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        targets = target_payload(payload)
        now = utc_now()
        with self._lock, self.connect() as conn:
            self.require_account(conn, account_id)
            conn.execute(
                """
                UPDATE accounts
                SET target_cidrs_json=?, target_ips_json=?, updated_at=?
                WHERE id=?
                """,
                (
                    targets["target_cidrs_json"],
                    targets["target_ips_json"],
                    now,
                    account_id,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        account = self.public_account(row)
        return {
            "ok": True,
            "account": {
                "id": account["id"],
                "name": account["name"],
                "target_cidrs": account["target_cidrs"],
                "target_ips": account["target_ips"],
            },
        }

    def require_account(self, conn: sqlite3.Connection, account_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            raise WebPanelNotFound("Аккаунт не найден.")
        return row

    def list_accounts(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY is_active DESC, id ASC").fetchall()
        return [self.public_account(row) for row in rows]

    def get_account(self, account_id: int) -> Dict[str, Any]:
        with self.connect() as conn:
            row = self.require_account(conn, account_id)
        return self.public_account(row)

    def activate_account(self, account_id: int) -> Dict[str, Any]:
        with self._lock, self.connect() as conn:
            self.require_account(conn, account_id)
            conn.execute("UPDATE accounts SET is_active=0")
            conn.execute("UPDATE accounts SET is_active=1, updated_at=? WHERE id=?", (utc_now(), account_id))
            conn.commit()
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return self.public_account(row)

    def delete_account(self, account_id: int) -> Dict[str, Any]:
        if self.latest_run(account_id, only_active=True):
            raise WebPanelError("Перед удалением аккаунта остановите активную ротацию.")
        with self._lock, self.connect() as conn:
            self.require_account(conn, account_id)
            conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            active = conn.execute("SELECT id FROM accounts WHERE is_active=1").fetchone()
            if active is None:
                first = conn.execute("SELECT id FROM accounts ORDER BY id ASC LIMIT 1").fetchone()
                if first is not None:
                    conn.execute("UPDATE accounts SET is_active=1 WHERE id=?", (int(first["id"]),))
            conn.commit()
        return {"ok": True}

    def active_account(self) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE is_active=1 ORDER BY id LIMIT 1").fetchone()
        return self.public_account(row) if row is not None else None

    def runtime_paths(self, account_id: int) -> Dict[str, Path]:
        base = self.runtime_dir / "accounts" / str(account_id)
        return {
            "base": base,
            "key": base / "sa-key.json",
            "config": base / "config.json",
            "state": base / "state.json",
            "log": base / "run.log",
            "runner_log": base / "runner.log",
            "stop": base / ".ip-hunter.stop",
            "recreate": base / ".ip-hunter.recreate",
        }

    def build_runtime_files(self, account_id: int) -> Dict[str, Path]:
        with self.connect() as conn:
            row = self.require_account(conn, account_id)
        paths = self.runtime_paths(account_id)
        paths["base"].mkdir(parents=True, exist_ok=True)
        paths["key"].write_text(
            self.decrypt_service_account(row["service_account_json_encrypted"]),
            encoding="utf-8",
        )
        template = read_json(self.root / "config.example.json")
        target_cidrs = json.loads(row["target_cidrs_json"] or "[]")
        target_ips = json.loads(row["target_ips_json"] or "[]")
        if not target_cidrs and not target_ips:
            raise WebPanelError("Для запуска укажите хотя бы один целевой CIDR или IP.")
        zones = json.loads(row["zones_json"] or "[]") or self.default_zones()
        protected_cloud_ids = json.loads(row["protected_cloud_ids_json"] or "[]")
        protected_folder_ids = json.loads(row["protected_folder_ids_json"] or "[]")
        roll_mode = str(row["roll_mode"] or "cloud")
        target_cloud_id = str(row["target_cloud_id"] or "") if roll_mode == "project" else ""
        folder_id = str(row["folder_id"] or "") if roll_mode == "project" else ""
        if target_cloud_id and target_cloud_id in protected_cloud_ids:
            raise WebPanelError("Целевое облако находится в изоляции. Уберите его из изоляции или очистите ID целевого облака.")
        if folder_id and folder_id in protected_folder_ids:
            raise WebPanelError("Целевая папка находится в изоляции. Уберите её из изоляции или выберите другой Folder ID.")
        telegram = self.telegram_settings(include_token=True)
        config = dict(template)
        config.update(
            {
                "dry_run": False,
                "rotation_mode": "hybrid",
                "organization_id": row["organization_id"],
                "billing_account_id": row["billing_account_id"],
                "service_cloud_id": row["service_cloud_id"],
                "target_cloud_id": target_cloud_id,
                "cloud_id": "",
                "folder_id": folder_id,
                "zone": zones[0],
                "zones": zones,
                "target_cidrs": target_cidrs,
                "target_ips": target_ips,
                "protected_cloud_ids": protected_cloud_ids,
                "protected_folder_ids": protected_folder_ids,
                "continue_after_success": bool(row["continue_after_success"]),
                "auth": {
                    "service_account_key_file": paths["key"].name,
                    "iam_token_env": "YC_IAM_TOKEN",
                },
                "notifications": {
                    "enabled": bool(telegram["enabled"]),
                    "telegram": {
                        "enabled": bool(telegram["enabled"]),
                        "bot_token": str(telegram.get("bot_token") or ""),
                        "chat_id": str(telegram.get("chat_id") or ""),
                    },
                },
                "state_file": paths["state"].name,
                "log_file": paths["log"].name,
            }
        )
        write_json_atomic(paths["config"], config)
        return paths

    def latest_run(self, account_id: int, only_active: bool = False) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE account_id=? ORDER BY id DESC LIMIT 1",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        run = dict(row)
        self.reconcile_run(run)
        if only_active and run["status"] not in {"starting", "running", "stopping"}:
            return None
        return run

    def reconcile_run(self, run: Dict[str, Any]) -> None:
        if run["status"] not in {"starting", "running", "stopping"}:
            return
        process = self._processes.get(int(run["id"]))
        if process is not None:
            exit_code = process.poll()
            if exit_code is None:
                return
        else:
            exit_code = None
            if run.get("pid") and pid_exists(int(run["pid"])):
                return
        if exit_code is None:
            exit_code = 0 if run["status"] == "stopping" else None
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, stopped_at=?, exit_code=? WHERE id=?",
                ("stopped", utc_now(), exit_code, int(run["id"])),
            )
            conn.commit()
        run["status"] = "stopped"
        run["stopped_at"] = utc_now()
        run["exit_code"] = exit_code
        self._processes.pop(int(run["id"]), None)
        safe_unlink(Path(str(run["stop_file"])))
        safe_unlink(Path(str(run["recreate_file"])))

    def start_spin(self, account_id: int) -> Dict[str, Any]:
        active_run = self.latest_run(account_id, only_active=True)
        if active_run:
            return {"ok": True, "run": active_run, "message": "Ротация уже запущена."}
        paths = self.build_runtime_files(account_id)
        for control_path in (paths["stop"], paths["recreate"]):
            if not safe_unlink(control_path) and control_path.exists():
                raise WebPanelError(f"Не удалось очистить старый control-file: {control_path}")
        command = [
            self.python,
            str(self.root / "yc_ip_hunter.py"),
            "--config",
            str(paths["config"]),
            "--run",
            "--yes-delete-cloud",
            "--stop-file",
            str(paths["stop"]),
            "--recreate-file",
            str(paths["recreate"]),
        ]
        now = utc_now()
        with self._lock, self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runs(
                    account_id, pid, status, runtime_dir, config_path, state_path, log_path,
                    runner_log_path, stop_file, recreate_file, started_at
                ) VALUES(?, NULL, 'starting', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    str(paths["base"]),
                    str(paths["config"]),
                    str(paths["state"]),
                    str(paths["log"]),
                    str(paths["runner_log"]),
                    str(paths["stop"]),
                    str(paths["recreate"]),
                    now,
                ),
            )
            run_id = int(cursor.lastrowid)
            conn.commit()
        with paths["runner_log"].open("ab") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=str(self.root),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        self._processes[run_id] = process
        with self.connect() as conn:
            conn.execute("UPDATE runs SET pid=?, status='running' WHERE id=?", (process.pid, run_id))
            conn.commit()
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return {"ok": True, "run": dict(row), "command": command}

    def stop_run(self, account_id: int) -> Dict[str, Any]:
        run = self.latest_run(account_id, only_active=True)
        if not run:
            return {"ok": True, "message": "Активной ротации нет."}
        Path(str(run["stop_file"])).write_text("stop\n", encoding="utf-8")
        with self.connect() as conn:
            conn.execute("UPDATE runs SET status='stopping' WHERE id=?", (int(run["id"]),))
            conn.commit()
        return {"ok": True, "message": "Остановка запрошена."}

    def recreate_now(self, account_id: int) -> Dict[str, Any]:
        run = self.latest_run(account_id, only_active=True)
        if not run:
            return {"ok": False, "message": "Активной ротации нет."}
        Path(str(run["recreate_file"])).write_text("recreate\n", encoding="utf-8")
        return {"ok": True, "message": "Пересоздание запрошено."}

    def cleanup_attempt_result(self, account_id: int, attempt_id: int) -> Dict[str, Any]:
        if self.latest_run(account_id, only_active=True):
            raise WebPanelError("Перед ручным удалением результата остановите активную ротацию.")
        with self.connect() as conn:
            account_row = self.require_account(conn, account_id)
            attempt_row = conn.execute(
                "SELECT * FROM attempts WHERE id=? AND account_id=?",
                (attempt_id, account_id),
            ).fetchone()
            if attempt_row is None:
                raise WebPanelNotFound("Попытка не найдена.")
            account = dict(account_row)
            attempt = dict(attempt_row)

        if not int(attempt.get("matched") or 0):
            raise WebPanelError("Ручное удаление доступно только для сохранённого успешного IP.")
        cloud_id = str(attempt.get("cloud_id") or "").strip()
        folder_id = str(attempt.get("folder_id") or "").strip()
        address_id = str(attempt.get("address_id") or "").strip()
        cleanup_status = str(attempt.get("cleanup_status") or "")
        cloud_cleanup_status = str(attempt.get("cloud_cleanup_status") or "")
        configured_project_folder_id = str(account.get("folder_id") or "").strip()
        configured_project_cloud_id = str(account.get("target_cloud_id") or "").strip()
        already_cleaned_ip = cleanup_status == "deleted"
        already_cleaned_cloud = cloud_cleanup_status == "deleted"
        if already_cleaned_ip and already_cleaned_cloud:
            return {"ok": True, "attempt": attempt, "message": "Результат и cloud уже помечены как удалённые."}
        delete_result_folder = bool(folder_id and folder_id != configured_project_folder_id)
        if not already_cleaned_ip and (not address_id or not folder_id):
            raise WebPanelError("В истории нет address_id или folder_id для удаления.")
        if cloud_id and cloud_id == str(account.get("service_cloud_id") or ""):
            raise WebPanelError("Нельзя удалять ресурс из сервисного облака аккаунта.")

        yc = load_yc_ip_hunter_module()
        paths = self.runtime_paths(account_id)
        paths["base"].mkdir(parents=True, exist_ok=True)
        paths["key"].write_text(
            self.decrypt_service_account(account["service_account_json_encrypted"]),
            encoding="utf-8",
        )
        cleanup_config = {
            "auth": {
                "service_account_key_file": paths["key"].name,
                "iam_token_env": "YC_IAM_TOKEN",
            }
        }
        client = yc.YandexCloudClient(
            yc.TokenProvider(cleanup_config, paths["base"]),
            dry_run=False,
            operation_timeout_seconds=180,
            operation_poll_seconds=1.0,
        )

        def is_expected_cleanup_error(exc: Any) -> bool:
            text = exc.text().lower()
            return exc.status == 404 or "not found" in text or "deleted" in text or "deletion" in text

        def mark_cleanup_failed(exc: Exception) -> None:
            with self.connect() as conn:
                conn.execute(
                    "UPDATE attempts SET cleanup_status=?, cleanup_at=?, cleanup_error=? WHERE id=?",
                    ("failed", utc_now(), str(exc), attempt_id),
                )
                conn.commit()

        if not already_cleaned_ip:
            try:
                client.delete_address(address_id, wait=True)
            except yc.ApiError as exc:
                if not is_expected_cleanup_error(exc):
                    mark_cleanup_failed(exc)
                    raise WebPanelError(f"Не удалось удалить reserved address: {exc.message}") from exc

            if delete_result_folder:
                try:
                    client.delete_folder(folder_id, immediate=True, wait=False)
                except yc.ApiError as exc:
                    if not is_expected_cleanup_error(exc):
                        mark_cleanup_failed(exc)
                        raise WebPanelError(f"Не удалось отправить каталог на удаление: {exc.message}") from exc

        cloud_cleanup_result = {
            "status": cloud_cleanup_status,
            "error": str(attempt.get("cloud_cleanup_error") or ""),
        }
        if not already_cleaned_cloud:
            cloud_cleanup_result = self.cleanup_disposable_cloud_if_empty(
                yc=yc,
                client=client,
                cloud_id=cloud_id,
                deleted_folder_id=folder_id,
                delete_result_folder=delete_result_folder,
                configured_project_cloud_id=configured_project_cloud_id,
            )

        with self._lock, self.connect() as conn:
            row = self.require_account(conn, account_id)
            protected_cloud_ids = normalize_protected_cloud_ids(json.loads(row["protected_cloud_ids_json"] or "[]"))
            protected_folder_ids = normalize_protected_folder_ids(json.loads(row["protected_folder_ids_json"] or "[]"))
            protected_cloud_ids = [value for value in protected_cloud_ids if value != cloud_id]
            protected_folder_ids = [value for value in protected_folder_ids if value != folder_id]
            now = utc_now()
            conn.execute(
                """
                UPDATE accounts
                SET protected_cloud_ids_json=?, protected_folder_ids_json=?, updated_at=?
                WHERE id=?
                """,
                (json.dumps(protected_cloud_ids), json.dumps(protected_folder_ids), now, account_id),
            )
            conn.execute(
                "UPDATE attempts SET cleanup_status=?, cleanup_at=?, cleanup_error=? WHERE id=?",
                ("deleted", now, "", attempt_id),
            )
            if cloud_cleanup_result["status"]:
                conn.execute(
                    """
                    UPDATE attempts
                    SET cloud_cleanup_status=?, cloud_cleanup_at=?, cloud_cleanup_error=?
                    WHERE id=?
                    """,
                    (
                        cloud_cleanup_result["status"],
                        now,
                        cloud_cleanup_result["error"],
                        attempt_id,
                    ),
                )
            conn.commit()

        if cloud_cleanup_result["status"] == "deleted":
            message = f"IP {attempt['ip']} удалён, каталог и пустое cloud отправлены на удаление."
        elif cloud_cleanup_result["status"] == "skipped_not_empty":
            message = f"IP {attempt['ip']} удалён, каталог отправлен на удаление. Cloud пока не пустое."
        elif cloud_cleanup_result["status"] == "kept_project_cloud":
            message = f"IP {attempt['ip']} удалён. Cloud/каталог 1 проекта оставлены."
        elif cloud_cleanup_result["status"] == "skipped_not_managed":
            message = f"IP {attempt['ip']} удалён. Cloud не похоже на disposable cloud Redroller, поэтому оставлено."
        elif cloud_cleanup_result["status"] == "failed":
            message = f"IP {attempt['ip']} удалён, но cloud не удалось удалить: {cloud_cleanup_result['error']}"
        else:
            message = (
                f"IP {attempt['ip']} удалён, каталог отправлен на удаление."
                if delete_result_folder
                else f"IP {attempt['ip']} удалён. Каталог 1 проекта оставлен, защита снята."
            )
        return {
            "ok": True,
            "message": message,
            "attempt": {
                "id": attempt_id,
                "ip": attempt["ip"],
                "cloud_id": cloud_id,
                "folder_id": folder_id,
                "address_id": address_id,
                "cleanup_status": "deleted",
                "folder_cleanup": "delete_submitted" if delete_result_folder else "kept_project_folder",
                "cloud_cleanup_status": cloud_cleanup_result["status"],
            },
        }

    def cleanup_disposable_cloud_if_empty(
        self,
        *,
        yc: Any,
        client: Any,
        cloud_id: str,
        deleted_folder_id: str,
        delete_result_folder: bool,
        configured_project_cloud_id: str,
    ) -> Dict[str, str]:
        if not cloud_id:
            return {"status": "", "error": ""}
        if not delete_result_folder or (configured_project_cloud_id and cloud_id == configured_project_cloud_id):
            return {"status": "kept_project_cloud", "error": ""}

        def expected_deleted_error(exc: Any) -> bool:
            text = exc.text().lower()
            return exc.status == 404 or "not found" in text or "deleted" in text or "deletion" in text

        try:
            cloud = client.get_cloud(cloud_id)
        except yc.ApiError as exc:
            if expected_deleted_error(exc):
                return {"status": "deleted", "error": ""}
            return {"status": "failed", "error": exc.message}

        template = read_json(self.root / "config.example.json")
        name_prefix = str(template.get("cloud_name_prefix") or "ip-hunter")
        labels = cloud.get("labels") or {}
        name = str(cloud.get("name") or "")
        if labels.get("managed-by") != "yc-ip-hunter" and not name.startswith(name_prefix):
            return {"status": "skipped_not_managed", "error": ""}

        try:
            folders = client.list_folders(cloud_id)
        except yc.ApiError as exc:
            if expected_deleted_error(exc):
                return {"status": "deleted", "error": ""}
            return {"status": "failed", "error": exc.message}

        active_folder_ids = []
        for folder in folders:
            folder_id = str(folder.get("id") or "")
            if folder_id and folder_id == deleted_folder_id:
                continue
            status = str(folder.get("status") or "ACTIVE").upper()
            if status not in {"DELETING", "PENDING_DELETION"}:
                active_folder_ids.append(folder_id or str(folder.get("name") or "unknown"))
        if active_folder_ids:
            return {"status": "skipped_not_empty", "error": ", ".join(active_folder_ids[:5])}

        try:
            client.delete_cloud(cloud_id, immediate=True, wait=False)
        except yc.ApiError as exc:
            text = exc.text().lower()
            if expected_deleted_error(exc):
                return {"status": "deleted", "error": ""}
            if "folder" in text or "resource" in text or "not empty" in text:
                return {"status": "skipped_not_empty", "error": exc.message}
            return {"status": "failed", "error": exc.message}
        return {"status": "deleted", "error": ""}

    def sync_attempts(self, run: Optional[Dict[str, Any]]) -> None:
        if not run:
            return
        state = read_json(Path(str(run["state_path"])))
        recent = state.get("recent_allocations")
        if not isinstance(recent, list):
            return
        with self.connect() as conn:
            for offset, item in enumerate(recent, start=1):
                if not isinstance(item, dict):
                    continue
                attempt_key = "|".join(
                    str(item.get(key) or "")
                    for key in ["at", "address_id", "ip", "zone", "folder_id"]
                )
                if not attempt_key.strip("|"):
                    attempt_key = f"{run['id']}|{offset}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO attempts(
                        run_id, account_id, attempt_key, attempt_number, at, ip, zone,
                        cloud_id, folder_id, address_id, matched
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(run["id"]),
                        int(run["account_id"]),
                        attempt_key,
                        offset,
                        str(item.get("at") or ""),
                        str(item.get("ip") or ""),
                        str(item.get("zone") or ""),
                        str(item.get("cloud_id") or ""),
                        str(item.get("folder_id") or ""),
                        str(item.get("address_id") or ""),
                        1 if item.get("matched") else 0,
                    ),
                )
                conn.execute(
                    "UPDATE attempts SET attempt_number=? WHERE attempt_key=?",
                    (offset, attempt_key),
                )
            conn.commit()

    def sync_auto_protected_clouds(self, account_id: int, run: Optional[Dict[str, Any]]) -> bool:
        if not run:
            return False
        state_path = Path(str(run["state_path"]))
        state = read_json(state_path)
        auto_cloud_values = state.get("auto_protected_cloud_ids")
        auto_folder_values = state.get("auto_protected_folder_ids")
        has_cloud_values = isinstance(auto_cloud_values, list)
        has_folder_values = isinstance(auto_folder_values, list)
        if not has_cloud_values and not has_folder_values:
            return False
        auto_cloud_ids = normalize_protected_cloud_ids(auto_cloud_values) if has_cloud_values else []
        auto_folder_ids = normalize_protected_folder_ids(auto_folder_values) if has_folder_values else []
        if not auto_cloud_ids and not auto_folder_ids:
            if has_cloud_values and auto_cloud_values:
                state["auto_protected_cloud_ids"] = []
            if has_folder_values and auto_folder_values:
                state["auto_protected_folder_ids"] = []
            if (has_cloud_values and auto_cloud_values) or (has_folder_values and auto_folder_values):
                write_json_atomic(state_path, state)
            return False

        changed = False
        with self._lock, self.connect() as conn:
            row = self.require_account(conn, account_id)
            current = normalize_protected_cloud_ids(json.loads(row["protected_cloud_ids_json"] or "[]"))
            current_folders = normalize_protected_folder_ids(json.loads(row["protected_folder_ids_json"] or "[]"))
            merged_clouds = list(current)
            seen_clouds = set(current)
            for cloud_id in auto_cloud_ids:
                if cloud_id not in seen_clouds:
                    merged_clouds.append(cloud_id)
                    seen_clouds.add(cloud_id)
            merged_folders = list(current_folders)
            seen_folders = set(current_folders)
            for folder_id in auto_folder_ids:
                if folder_id not in seen_folders:
                    merged_folders.append(folder_id)
                    seen_folders.add(folder_id)
            if merged_clouds != current or merged_folders != current_folders:
                conn.execute(
                    """
                    UPDATE accounts
                    SET protected_cloud_ids_json=?, protected_folder_ids_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (json.dumps(merged_clouds), json.dumps(merged_folders), utc_now(), account_id),
                )
                conn.commit()
                changed = True

        if has_cloud_values:
            state["auto_protected_cloud_ids"] = []
        if has_folder_values:
            state["auto_protected_folder_ids"] = []
        write_json_atomic(state_path, state)
        return changed

    def attempts_for_account(self, account_id: int, limit: int = 40) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM attempts WHERE account_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
        attempts = [dict(row) for row in reversed(rows)]
        with self.connect() as conn:
            for display_number, attempt in enumerate(attempts, start=1):
                seen_before = bool(
                    conn.execute(
                        """
                        SELECT 1 FROM attempts
                        WHERE account_id=? AND ip=? AND id<?
                        LIMIT 1
                        """,
                        (account_id, attempt["ip"], attempt["id"]),
                    ).fetchone()
                )
                attempt["ip_seen_before"] = seen_before
                attempt["ip_uniqueness"] = "repeat" if seen_before else "unique"
                attempt["display_number"] = display_number
        return attempts

    def status_payload(self) -> Dict[str, Any]:
        account = self.active_account()
        if not account:
            return {
                "active_account": None,
                "run": None,
                "running": False,
                "current_ip": "-",
                "target_subnet": "-",
                "target_summary": "-",
                "target_count": 0,
                "reel": [],
                "attempts": [],
                "logs": [],
                "error": "",
                "updated_at": utc_now(),
            }
        run = self.latest_run(int(account["id"]))
        self.sync_attempts(run)
        if self.sync_auto_protected_clouds(int(account["id"]), run):
            account = self.get_account(int(account["id"]))
        state = read_json(Path(str(run["state_path"]))) if run else {}
        attempts = self.attempts_for_account(int(account["id"]))
        current_ip = (
            str(state.get("last_allocated_ip") or "")
            or (attempts[-1]["ip"] if attempts else "")
            or "-"
        )
        target_cidrs = account.get("target_cidrs") or []
        target_ips = account.get("target_ips") or []
        target_items = target_ips + target_cidrs
        target_subnet = target_items[0] if target_items else "-"
        target_capacity = target_address_count(target_cidrs, target_ips)
        target_summary = format_target_address_count(target_capacity)
        logs = []
        if run:
            logs = read_tail(Path(str(run["log_path"])), 40)
            if not logs:
                logs = read_tail(Path(str(run["runner_log_path"])), 40)
        running = bool(run and run["status"] in {"starting", "running", "stopping"})
        error = ""
        if run and not running and run.get("exit_code") not in {None, 0}:
            error = f"Процесс завершился с кодом {run.get('exit_code')}."
        if running:
            reel = [
                {"label": f"РОЛЛ_{index:02d}", "ip": "", "hidden": True, "matched": False}
                for index in range(1, 8)
            ]
        else:
            reel = [
                {
                    "label": f"IP_BLOCK_{index:02d}",
                    "ip": attempt["ip"] or "-",
                    "hidden": False,
                    "matched": bool(attempt["matched"]),
                }
                for index, attempt in enumerate(attempts[-7:], start=1)
            ]
        return {
            "active_account": account,
            "run": run,
            "running": running,
            "current_ip": current_ip,
            "target_subnet": target_subnet,
            "target_summary": target_summary,
            "target_count": len(target_cidrs) + len(target_ips),
            "target_address_count": target_capacity,
            "reel": reel,
            "attempts": attempts[-25:],
            "logs": logs[-40:],
            "error": error,
            "updated_at": utc_now(),
        }

    def event_payload(self) -> str:
        return json.dumps(self.status_payload(), ensure_ascii=False)


class WebPanelHandler(BaseHTTPRequestHandler):
    server: "WebPanelServer"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    @property
    def app(self) -> WebPanelApp:
        return self.server.app

    def send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    MAX_BODY_SIZE = 4 * 1024 * 1024  # 4 MiB

    def read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        if length > self.MAX_BODY_SIZE:
            raise WebPanelError(f"Тело запроса слишком большое ({length} байт, максимум {self.MAX_BODY_SIZE}).")
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise WebPanelError("Тело запроса должно быть JSON-объектом.")
        return data

    def handle_error(self, exc: Exception) -> None:
        if isinstance(exc, WebPanelNotFound):
            status = 404
        else:
            status = 400 if isinstance(exc, (WebPanelError, json.JSONDecodeError)) else 500
        self.send_json({"ok": False, "error": str(exc)}, status=status)

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/api/accounts":
                self.send_json({"accounts": self.app.list_accounts()})
            elif path == "/api/settings/telegram":
                self.send_json({"telegram": self.app.public_telegram_settings()})
            elif path == "/api/status":
                self.send_json(self.app.status_payload())
            elif path == "/api/events":
                self.stream_events()
            else:
                self.serve_static(path)
        except Exception as exc:
            self.handle_error(exc)

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/accounts":
                self.send_json({"account": self.app.create_account(self.read_json_body())}, status=201)
                return
            if path == "/api/settings/telegram/test":
                self.send_json(self.app.test_telegram_settings())
                return
            cleanup_match = re.fullmatch(r"/api/accounts/(\d+)/attempts/(\d+)/cleanup", path)
            if cleanup_match:
                self.send_json(
                    self.app.cleanup_attempt_result(
                        int(cleanup_match.group(1)),
                        int(cleanup_match.group(2)),
                    )
                )
                return
            match = re.fullmatch(r"/api/accounts/(\d+)/(activate|spin|stop|recreate)", path)
            if not match:
                self.send_json({"ok": False, "error": "Не найдено."}, status=404)
                return
            account_id = int(match.group(1))
            action = match.group(2)
            if action == "activate":
                self.send_json({"account": self.app.activate_account(account_id)})
            elif action == "spin":
                self.send_json(self.app.start_spin(account_id))
            elif action == "stop":
                self.send_json(self.app.stop_run(account_id))
            else:
                self.send_json(self.app.recreate_now(account_id))
        except Exception as exc:
            self.handle_error(exc)

    def do_PUT(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            isolation_match = re.fullmatch(r"/api/accounts/(\d+)/isolation", path)
            if isolation_match:
                self.send_json(self.app.update_account_isolation(int(isolation_match.group(1)), self.read_json_body()))
                return
            if path == "/api/settings/telegram":
                self.send_json(self.app.update_telegram_settings(self.read_json_body()))
                return
            targets_match = re.fullmatch(r"/api/accounts/(\d+)/targets", path)
            if targets_match:
                self.send_json(self.app.update_account_targets(int(targets_match.group(1)), self.read_json_body()))
                return
            match = re.fullmatch(r"/api/accounts/(\d+)", path)
            if not match:
                self.send_json({"ok": False, "error": "Не найдено."}, status=404)
                return
            self.send_json({"account": self.app.update_account(int(match.group(1)), self.read_json_body())})
        except Exception as exc:
            self.handle_error(exc)

    def do_DELETE(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            match = re.fullmatch(r"/api/accounts/(\d+)", path)
            if not match:
                self.send_json({"ok": False, "error": "Не найдено."}, status=404)
                return
            self.send_json(self.app.delete_account(int(match.group(1))))
        except Exception as exc:
            self.handle_error(exc)

    def stream_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        while True:
            payload = self.app.event_payload()
            event = f"event: status\ndata: {payload}\n\n".encode("utf-8")
            try:
                self.wfile.write(event)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(1)

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            target = self.app.web_dir / "index.html"
        else:
            relative = Path(urllib.parse.unquote(path.lstrip("/")))
            target = (self.app.web_dir / relative).resolve()
            if self.app.web_dir not in target.parents and target != self.app.web_dir:
                self.send_json({"ok": False, "error": "Доступ запрещён."}, status=403)
                return
        if not target.exists() or not target.is_file():
            self.send_json({"ok": False, "error": "Не найдено."}, status=404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WebPanelServer(ThreadingHTTPServer):
    def __init__(self, server_address: Tuple[str, int], app: WebPanelApp) -> None:
        super().__init__(server_address, WebPanelHandler)
        self.app = app


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local web dashboard for yc_ip_hunter.py")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR))
    parser.add_argument("--db", default="")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    runtime_dir = Path(args.runtime_dir).resolve()
    db_path = Path(args.db).resolve() if args.db else runtime_dir / DEFAULT_DB_NAME
    app = WebPanelApp(runtime_dir=runtime_dir, db_path=db_path)
    server = WebPanelServer((args.host, args.port), app)
    print(f"Redroller web panel: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
