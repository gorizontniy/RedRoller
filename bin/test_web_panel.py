import importlib.util
import contextlib
import json
import shutil
import sqlite3
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("web_panel.py")
SPEC = importlib.util.spec_from_file_location("web_panel_module", MODULE_PATH)
web = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = web
SPEC.loader.exec_module(web)


def fresh_test_dir(name):
    path = MODULE_PATH.parent / ".test-tmp" / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def sample_key():
    return json.dumps(
        {
            "id": "key-id",
            "service_account_id": "service-account-id",
            "private_key": "-----BEGIN PRIVATE KEY-----\nSECRET-PRIVATE-KEY\n-----END PRIVATE KEY-----",
        }
    )


def sample_payload(**overrides):
    payload = {
        "name": "YC-SA-402",
        "organization_id": "org-1234567890",
        "billing_account_id": "billing-1234567890",
        "service_cloud_id": "cloud-1234567890",
        "roll_mode": "cloud",
        "target_cloud_id": "",
        "folder_id": "",
        "zones": ["ru-central1-a", "ru-central1-e"],
        "protected_cloud_ids": [],
        "protected_folder_ids": [],
        "target_cidrs": ["198.51.100.0/24"],
        "target_ips": [],
        "service_account_json": sample_key(),
    }
    payload.update(overrides)
    return payload


def make_app(root):
    return web.WebPanelApp(
        root=MODULE_PATH.parent,
        runtime_dir=root,
        db_path=root / "panel.sqlite3",
        web_dir=MODULE_PATH.parent / "web",
        python="python",
    )


class WebPanelTests(unittest.TestCase):
    def test_schema_initializes_idempotently(self):
        root = fresh_test_dir("web-schema")
        try:
            make_app(root)
            make_app(root)
            with contextlib.closing(sqlite3.connect(root / "panel.sqlite3")) as conn:
                version = conn.execute(
                    "SELECT value FROM settings WHERE key='schema_version'"
                ).fetchone()[0]
            self.assertEqual(version, str(web.SCHEMA_VERSION))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_schema_backup_is_created_before_roll_mode_migration(self):
        root = fresh_test_dir("web-schema-backup")
        try:
            db_path = root / "panel.sqlite3"
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

            make_app(root)

            backups = list(root.glob("panel.sqlite3.backup-*"))
            self.assertEqual(len(backups), 1)
            with contextlib.closing(sqlite3.connect(db_path)) as conn:
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(accounts)")}
            self.assertIn("roll_mode", columns)
            self.assertIn("protected_folder_ids_json", columns)
            self.assertIn("continue_after_success", columns)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_account_crud_and_activation(self):
        root = fresh_test_dir("web-crud")
        try:
            app = make_app(root)
            first = app.create_account(sample_payload(name="First"))
            second = app.create_account(sample_payload(name="Second", service_cloud_id="cloud-2"))

            self.assertTrue(first["is_active"])
            self.assertFalse(second["is_active"])
            self.assertEqual(len(app.list_accounts()), 2)

            activated = app.activate_account(second["id"])
            self.assertTrue(activated["is_active"])

            updated = app.update_account(
                second["id"],
                sample_payload(
                    name="Second Updated",
                    service_cloud_id="cloud-3",
                    service_account_json="",
                ),
            )
            self.assertEqual(updated["name"], "Second Updated")
            self.assertEqual(updated["service_cloud_id"], "cloud-3")
            self.assertEqual(updated["roll_mode"], "cloud")

            self.assertEqual(app.delete_account(first["id"]), {"ok": True})
            self.assertEqual(len(app.list_accounts()), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_project_roll_mode_requires_target_cloud_and_folder(self):
        root = fresh_test_dir("web-roll-mode-required")
        try:
            app = make_app(root)
            with self.assertRaisesRegex(web.WebPanelError, "target_cloud_id"):
                app.create_account(sample_payload(roll_mode="project", target_cloud_id="", folder_id=""))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cloud_roll_mode_clears_project_fields(self):
        root = fresh_test_dir("web-roll-mode-cloud")
        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(roll_mode="cloud", target_cloud_id="ignored-cloud", folder_id="ignored-folder")
            )
            paths = app.build_runtime_files(account["id"])
            config = json.loads(paths["config"].read_text(encoding="utf-8"))

            self.assertEqual(account["roll_mode"], "cloud")
            self.assertEqual(account["target_cloud_id"], "")
            self.assertEqual(account["folder_id"], "")
            self.assertEqual(config["target_cloud_id"], "")
            self.assertEqual(config["folder_id"], "")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_project_roll_mode_writes_target_cloud_and_folder(self):
        root = fresh_test_dir("web-roll-mode-project")
        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(
                    roll_mode="project",
                    target_cloud_id="project-cloud",
                    folder_id="project-folder",
                )
            )
            paths = app.build_runtime_files(account["id"])
            config = json.loads(paths["config"].read_text(encoding="utf-8"))

            self.assertEqual(account["roll_mode"], "project")
            self.assertEqual(config["rotation_mode"], "hybrid")
            self.assertEqual(config["target_cloud_id"], "project-cloud")
            self.assertEqual(config["folder_id"], "project-folder")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_service_account_json_is_encrypted_in_database(self):
        root = fresh_test_dir("web-encryption")
        try:
            app = make_app(root)
            app.create_account(sample_payload())

            raw_db = (root / "panel.sqlite3").read_bytes()
            self.assertNotIn(b"SECRET-PRIVATE-KEY", raw_db)

            with app.connect() as conn:
                token = conn.execute(
                    "SELECT service_account_json_encrypted FROM accounts LIMIT 1"
                ).fetchone()[0]
            self.assertIn("SECRET-PRIVATE-KEY", app.decrypt_service_account(token))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_telegram_settings_store_token_encrypted_and_public_shape(self):
        root = fresh_test_dir("web-telegram-settings")
        try:
            app = make_app(root)
            result = app.update_telegram_settings(
                {"enabled": True, "chat_id": "12345", "bot_token": "123456:SECRET"}
            )

            self.assertEqual(result["telegram"], {"enabled": True, "chat_id": "12345", "has_bot_token": True})
            self.assertNotIn("123456:SECRET", json.dumps(result))
            raw_db = (root / "panel.sqlite3").read_bytes()
            self.assertNotIn(b"123456:SECRET", raw_db)
            self.assertEqual(app.telegram_settings(include_token=True)["bot_token"], "123456:SECRET")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_telegram_empty_token_preserves_existing_and_clear_removes_it(self):
        root = fresh_test_dir("web-telegram-preserve")
        try:
            app = make_app(root)
            app.update_telegram_settings({"enabled": True, "chat_id": "1", "bot_token": "token-one"})
            app.update_telegram_settings({"enabled": False, "chat_id": "2", "bot_token": ""})
            self.assertEqual(app.telegram_settings(include_token=True)["bot_token"], "token-one")
            self.assertEqual(app.public_telegram_settings()["chat_id"], "2")

            app.update_telegram_settings({"enabled": False, "chat_id": "2", "clear_bot_token": True})
            self.assertFalse(app.public_telegram_settings()["has_bot_token"])
            self.assertEqual(app.telegram_settings(include_token=True)["bot_token"], "")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_telegram_test_endpoint_uses_saved_settings(self):
        root = fresh_test_dir("web-telegram-test")
        try:
            app = make_app(root)
            app.update_telegram_settings({"enabled": True, "chat_id": "chat-1", "bot_token": "token-1"})
            calls = []
            app.send_telegram_message = lambda text: calls.append(text) or True

            self.assertEqual(app.test_telegram_settings(), {"ok": True})
            self.assertEqual(len(calls), 1)
            self.assertIn("Redroller", calls[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_writes_fresh_telegram_settings(self):
        root = fresh_test_dir("web-telegram-runtime")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            app.update_telegram_settings({"enabled": True, "chat_id": "chat-1", "bot_token": "token-1"})

            paths = app.build_runtime_files(account["id"])
            config = json.loads(paths["config"].read_text(encoding="utf-8"))

            self.assertTrue(config["notifications"]["enabled"])
            self.assertTrue(config["notifications"]["telegram"]["enabled"])
            self.assertEqual(config["notifications"]["telegram"]["chat_id"], "chat-1")
            self.assertEqual(config["notifications"]["telegram"]["bot_token"], "token-1")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_keeps_hybrid_target_cloud_empty(self):
        root = fresh_test_dir("web-runtime-config")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(target_cloud_id=""))

            paths = app.build_runtime_files(account["id"])

            config = json.loads(paths["config"].read_text(encoding="utf-8"))
            self.assertFalse(config["dry_run"])
            self.assertEqual(config["rotation_mode"], "hybrid")
            self.assertEqual(config["target_cloud_id"], "")
            self.assertEqual(config["service_cloud_id"], "cloud-1234567890")
            self.assertFalse(config["notifications"]["enabled"])
            self.assertFalse(config["notifications"]["telegram"]["enabled"])
            self.assertEqual(config["zone"], "ru-central1-a")
            self.assertEqual(config["zones"], ["ru-central1-a", "ru-central1-e"])
            self.assertEqual(config["protected_cloud_ids"], [])
            self.assertEqual(config["protected_folder_ids"], [])
            self.assertFalse(config["continue_after_success"])
            self.assertEqual(config["auth"]["service_account_key_file"], "sa-key.json")
            self.assertTrue(paths["key"].exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_can_continue_after_success_when_enabled(self):
        root = fresh_test_dir("web-runtime-continue-success")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(continue_after_success=True))

            paths = app.build_runtime_files(account["id"])

            config = json.loads(paths["config"].read_text(encoding="utf-8"))
            self.assertTrue(account["continue_after_success"])
            self.assertTrue(config["continue_after_success"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_writes_selected_zones_and_protected_ids(self):
        root = fresh_test_dir("web-runtime-zones")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(zones=["ru-central1-b", "ru-central1-d"]))
            app.update_account_isolation(
                account["id"],
                {
                    "protected_cloud_ids": ["protected-1", "protected-2"],
                    "protected_folder_ids": ["folder-1", "folder-2"],
                },
            )

            paths = app.build_runtime_files(account["id"])

            config = json.loads(paths["config"].read_text(encoding="utf-8"))
            self.assertEqual(config["zone"], "ru-central1-b")
            self.assertEqual(config["zones"], ["ru-central1-b", "ru-central1-d"])
            self.assertEqual(config["protected_cloud_ids"], ["protected-1", "protected-2"])
            self.assertEqual(config["protected_folder_ids"], ["folder-1", "folder-2"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_rejects_isolated_target_cloud(self):
        root = fresh_test_dir("web-runtime-isolated-target")
        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(roll_mode="project", target_cloud_id="target-cloud", folder_id="folder-1")
            )
            app.update_account_isolation(account["id"], {"protected_cloud_ids": ["target-cloud"]})

            with self.assertRaisesRegex(web.WebPanelError, "изоляции"):
                app.build_runtime_files(account["id"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_rejects_isolated_target_folder(self):
        root = fresh_test_dir("web-runtime-isolated-folder")
        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(roll_mode="project", target_cloud_id="target-cloud", folder_id="folder-1")
            )
            app.update_account_isolation(account["id"], {"protected_folder_ids": ["folder-1"]})

            with self.assertRaisesRegex(web.WebPanelError, "папка находится в изоляции"):
                app.build_runtime_files(account["id"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolation_endpoint_updates_only_protected_ids(self):
        root = fresh_test_dir("web-isolation-update")
        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(
                    name="Original",
                    protected_cloud_ids=["old-cloud"],
                    protected_folder_ids=["old-folder"],
                )
            )

            result = app.update_account_isolation(
                account["id"],
                {
                    "protected_cloud_ids": ["  b1gabc  ", "", "aoe-def", "b1gabc"],
                    "protected_folder_ids": ["  folder-1  ", "", "folder-2", "folder-1"],
                    "name": "Ignored",
                    "zones": ["ru-central1-d"],
                },
            )
            updated = app.get_account(account["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["account"]["name"], "Original")
            self.assertEqual(result["account"]["protected_cloud_ids"], ["b1gabc", "aoe-def"])
            self.assertEqual(result["account"]["protected_folder_ids"], ["folder-1", "folder-2"])
            self.assertEqual(updated["name"], "Original")
            self.assertEqual(updated["zones"], ["ru-central1-a", "ru-central1-e"])
            self.assertEqual(updated["protected_cloud_ids"], ["b1gabc", "aoe-def"])
            self.assertEqual(updated["protected_folder_ids"], ["folder-1", "folder-2"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_targets_endpoint_updates_only_targets(self):
        root = fresh_test_dir("web-targets-update")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(name="Targets", target_cidrs=["198.51.100.0/24"]))

            result = app.update_account_targets(
                account["id"],
                {
                    "target_cidrs": [" 198.51.100.10/24 ", "203.0.113.0/24", "198.51.100.0/24"],
                    "target_ips": [" 192.0.2.44 ", "192.0.2.44"],
                    "name": "Ignored",
                },
            )
            updated = app.get_account(account["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["account"]["name"], "Targets")
            self.assertEqual(result["account"]["target_cidrs"], ["198.51.100.0/24", "203.0.113.0/24"])
            self.assertEqual(result["account"]["target_ips"], ["192.0.2.44"])
            self.assertEqual(updated["name"], "Targets")
            self.assertEqual(updated["target_cidrs"], ["198.51.100.0/24", "203.0.113.0/24"])
            self.assertEqual(updated["target_ips"], ["192.0.2.44"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_targets_reject_invalid_and_empty_lists(self):
        root = fresh_test_dir("web-targets-errors")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(target_cidrs=["198.51.100.0/24"]))

            with self.assertRaisesRegex(web.WebPanelError, "CIDR"):
                app.update_account_targets(account["id"], {"target_cidrs": ["bad-cidr"], "target_ips": []})
            with self.assertRaisesRegex(web.WebPanelError, "IP"):
                app.update_account_targets(account["id"], {"target_cidrs": [], "target_ips": ["bad-ip"]})
            with self.assertRaisesRegex(web.WebPanelError, "хотя бы один"):
                app.update_account_targets(account["id"], {"target_cidrs": [], "target_ips": []})

            self.assertEqual(app.get_account(account["id"])["target_cidrs"], ["198.51.100.0/24"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_target_address_count_collapses_overlaps(self):
        self.assertEqual(
            web.target_address_count(
                ["198.51.100.0/24", "198.51.100.128/25", "203.0.113.0/30"],
                ["198.51.100.44", "192.0.2.10"],
            ),
            261,
        )
        self.assertEqual(web.format_target_address_count(261), "261 IP")

    def test_isolation_http_contract_statuses_and_all_or_nothing(self):
        root = fresh_test_dir("web-isolation-http")
        server = None
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(name="HTTP Account", protected_cloud_ids=["old-id"]))
            server = web.WebPanelServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"

            def put_json(path, payload):
                request = urllib.request.Request(
                    base_url + path,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read().decode("utf-8"))

            status, body = put_json(
                f"/api/accounts/{account['id']}/isolation",
                {
                    "protected_cloud_ids": [" b1gabc ", "aoe-def", "b1gabc"],
                    "protected_folder_ids": [" folder-1 ", "folder-2", "folder-1"],
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(
                body,
                {
                    "ok": True,
                    "account": {
                        "id": account["id"],
                        "name": "HTTP Account",
                        "protected_cloud_ids": ["b1gabc", "aoe-def"],
                        "protected_folder_ids": ["folder-1", "folder-2"],
                    },
                },
            )

            status, body = put_json(
                f"/api/accounts/{account['id']}/isolation",
                {"protected_cloud_ids": ["valid-id", "bad/url"], "protected_folder_ids": ["new-folder"]},
            )
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], ["b1gabc", "aoe-def"])
            self.assertEqual(app.get_account(account["id"])["protected_folder_ids"], ["folder-1", "folder-2"])

            status, body = put_json("/api/accounts/999/isolation", {"protected_cloud_ids": []})
            self.assertEqual(status, 404)
            self.assertFalse(body["ok"])
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            shutil.rmtree(root, ignore_errors=True)

    def test_general_account_update_does_not_clear_isolation(self):
        root = fresh_test_dir("web-isolation-general-update")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            app.update_account_isolation(
                account["id"],
                {"protected_cloud_ids": ["b1gabc"], "protected_folder_ids": ["folder-1"]},
            )

            updated = app.update_account(
                account["id"],
                sample_payload(name="Renamed", service_account_json=""),
            )

            self.assertEqual(updated["name"], "Renamed")
            self.assertEqual(updated["protected_cloud_ids"], ["b1gabc"])
            self.assertEqual(updated["protected_folder_ids"], ["folder-1"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolation_validation_is_all_or_nothing(self):
        root = fresh_test_dir("web-isolation-validation")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(protected_cloud_ids=["b1gabc"], protected_folder_ids=["folder-1"]))

            with self.assertRaisesRegex(web.WebPanelError, "Некорректный cloud-id"):
                app.update_account_isolation(
                    account["id"],
                    {"protected_cloud_ids": ["valid-id", "https://bad.example"]},
                )

            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], ["b1gabc"])
            self.assertEqual(app.get_account(account["id"])["protected_folder_ids"], ["folder-1"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolation_empty_list_clears_and_repeat_save_is_idempotent(self):
        root = fresh_test_dir("web-isolation-empty")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(protected_cloud_ids=["b1gabc"], protected_folder_ids=["folder-1"]))

            first = app.update_account_isolation(account["id"], {"protected_cloud_ids": [], "protected_folder_ids": []})
            second = app.update_account_isolation(account["id"], {"protected_cloud_ids": [], "protected_folder_ids": []})

            self.assertEqual(first["account"]["protected_cloud_ids"], [])
            self.assertEqual(first["account"]["protected_folder_ids"], [])
            self.assertEqual(second["account"]["protected_cloud_ids"], [])
            self.assertEqual(second["account"]["protected_folder_ids"], [])
            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], [])
            self.assertEqual(app.get_account(account["id"])["protected_folder_ids"], [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolation_rejects_missing_non_list_and_unknown_account(self):
        root = fresh_test_dir("web-isolation-errors")
        try:
            app = make_app(root)
            app.create_account(sample_payload())

            with self.assertRaisesRegex(web.WebPanelError, "protected_cloud_ids"):
                app.update_account_isolation(1, {"name": "ignored"})
            with self.assertRaisesRegex(web.WebPanelError, "списком"):
                app.update_account_isolation(1, {"protected_cloud_ids": "b1gabc"})
            with self.assertRaisesRegex(web.WebPanelError, "folder-id"):
                app.update_account_isolation(1, {"protected_folder_ids": ["bad/url"]})
            with self.assertRaises(web.WebPanelNotFound):
                app.update_account_isolation(999, {"protected_cloud_ids": []})
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_uses_fresh_isolation_from_database(self):
        root = fresh_test_dir("web-isolation-runtime-fresh")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            app.update_account_isolation(
                account["id"],
                {"protected_cloud_ids": ["first-id"], "protected_folder_ids": ["first-folder"]},
            )
            first_paths = app.build_runtime_files(account["id"])
            first_config = json.loads(first_paths["config"].read_text(encoding="utf-8"))
            self.assertEqual(first_config["protected_cloud_ids"], ["first-id"])
            self.assertEqual(first_config["protected_folder_ids"], ["first-folder"])

            app.update_account_isolation(
                account["id"],
                {"protected_cloud_ids": ["second-id"], "protected_folder_ids": ["second-folder"]},
            )
            second_paths = app.build_runtime_files(account["id"])
            second_config = json.loads(second_paths["config"].read_text(encoding="utf-8"))

            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], ["second-id"])
            self.assertEqual(app.get_account(account["id"])["protected_folder_ids"], ["second-folder"])
            self.assertEqual(second_config["protected_cloud_ids"], ["second-id"])
            self.assertEqual(second_config["protected_folder_ids"], ["second-folder"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_auto_protected_clouds_sync_to_account_and_clear_state(self):
        root = fresh_test_dir("web-auto-protect-sync")
        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(protected_cloud_ids=["manual-cloud"], protected_folder_ids=["manual-folder"])
            )
            state_path = root / "state.json"
            write_state = {
                "auto_protected_cloud_ids": ["auto-cloud", "manual-cloud", "auto-cloud"],
                "auto_protected_folder_ids": ["auto-folder", "manual-folder", "auto-folder"],
            }
            state_path.write_text(json.dumps(write_state), encoding="utf-8")
            run = {"state_path": str(state_path)}

            self.assertTrue(app.sync_auto_protected_clouds(account["id"], run))

            updated = app.get_account(account["id"])
            synced_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["protected_cloud_ids"], ["manual-cloud", "auto-cloud"])
            self.assertEqual(updated["protected_folder_ids"], ["manual-folder", "auto-folder"])
            self.assertEqual(synced_state["auto_protected_cloud_ids"], [])
            self.assertEqual(synced_state["auto_protected_folder_ids"], [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_auto_protected_sync_is_idempotent_without_updated_at_change(self):
        root = fresh_test_dir("web-auto-protect-idempotent")
        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(protected_cloud_ids=["manual-cloud"], protected_folder_ids=["manual-folder"])
            )
            fixed_updated_at = "2026-01-01T00:00:00Z"
            with app.connect() as conn:
                conn.execute("UPDATE accounts SET updated_at=? WHERE id=?", (fixed_updated_at, account["id"]))
                conn.commit()
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps({
                    "auto_protected_cloud_ids": ["manual-cloud"],
                    "auto_protected_folder_ids": ["manual-folder"],
                }),
                encoding="utf-8",
            )

            self.assertFalse(app.sync_auto_protected_clouds(account["id"], {"state_path": str(state_path)}))

            updated = app.get_account(account["id"])
            synced_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["updated_at"], fixed_updated_at)
            self.assertEqual(updated["protected_cloud_ids"], ["manual-cloud"])
            self.assertEqual(updated["protected_folder_ids"], ["manual-folder"])
            self.assertEqual(synced_state["auto_protected_cloud_ids"], [])
            self.assertEqual(synced_state["auto_protected_folder_ids"], [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_empty_zone_selection_is_rejected(self):
        root = fresh_test_dir("web-empty-zones")
        try:
            app = make_app(root)
            with self.assertRaisesRegex(web.WebPanelError, "зону"):
                app.create_account(sample_payload(zones=[]))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_start_spin_builds_expected_subprocess_command(self):
        root = fresh_test_dir("web-start")

        class FakeProcess:
            pid = 4321

            def poll(self):
                return None

        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            paths = app.runtime_paths(account["id"])
            paths["base"].mkdir(parents=True, exist_ok=True)
            paths["stop"].write_text("stop\n", encoding="utf-8")
            paths["recreate"].write_text("recreate\n", encoding="utf-8")

            with mock.patch.object(web.subprocess, "Popen", return_value=FakeProcess()) as popen:
                result = app.start_spin(account["id"])

            self.assertTrue(result["ok"])
            self.assertFalse(paths["stop"].exists())
            self.assertFalse(paths["recreate"].exists())
            command = popen.call_args.args[0]
            self.assertEqual(command[0], "python")
            self.assertIn("yc_ip_hunter.py", command[1])
            self.assertIn("--run", command)
            self.assertIn("--yes-delete-cloud", command)
            self.assertIn("--stop-file", command)
            self.assertIn("--recreate-file", command)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_reconcile_run_removes_control_files_after_stop(self):
        root = fresh_test_dir("web-reconcile-control")

        class FinishedProcess:
            pid = 4321

            def poll(self):
                return 0

        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            with mock.patch.object(web.subprocess, "Popen", return_value=FinishedProcess()):
                result = app.start_spin(account["id"])
            run = result["run"]
            stop_path = Path(run["stop_file"])
            recreate_path = Path(run["recreate_file"])
            stop_path.write_text("stop\n", encoding="utf-8")
            recreate_path.write_text("recreate\n", encoding="utf-8")

            app.latest_run(account["id"])

            self.assertFalse(stop_path.exists())
            self.assertFalse(recreate_path.exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_status_maps_recent_allocations_to_reel_and_attempts(self):
        root = fresh_test_dir("web-status")

        class FakeProcess:
            pid = 4321

            def poll(self):
                return None

        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            with mock.patch.object(web.subprocess, "Popen", return_value=FakeProcess()):
                result = app.start_spin(account["id"])
            state_path = Path(result["run"]["state_path"])
            state_path.write_text(
                json.dumps(
                    {
                        "last_allocated_ip": "198.51.100.44",
                        "recent_allocations": [
                            {
                                "at": "2026-05-06T10:00:00Z",
                                "cloud_id": "cloud-1",
                                "folder_id": "folder-1",
                                "zone": "ru-central1-a",
                                "ip": "198.51.100.44",
                                "address_id": "addr-1",
                                "matched": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            status = app.status_payload()

            self.assertTrue(status["running"])
            self.assertEqual(status["current_ip"], "198.51.100.44")
            self.assertEqual(status["target_subnet"], "198.51.100.0/24")
            self.assertEqual(status["target_summary"], "256 IP")
            self.assertEqual(status["target_address_count"], 256)
            self.assertEqual(status["attempts"][0]["ip"], "198.51.100.44")
            self.assertFalse(status["attempts"][0]["ip_seen_before"])
            self.assertEqual(status["attempts"][0]["ip_uniqueness"], "unique")
            self.assertEqual(status["reel"][0]["ip"], "")
            self.assertTrue(status["reel"][0]["hidden"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_attempt_display_numbers_stay_dense_after_resync(self):
        root = fresh_test_dir("web-attempt-dense")

        class FakeProcess:
            pid = 4321

            def poll(self):
                return None

        first_attempt = {
            "at": "2026-05-06T10:00:00Z",
            "cloud_id": "cloud-1",
            "folder_id": "folder-1",
            "zone": "ru-central1-a",
            "ip": "198.51.100.44",
            "address_id": "addr-1",
            "matched": True,
        }
        second_attempt = {
            "at": "2026-05-06T10:00:02Z",
            "cloud_id": "cloud-1",
            "folder_id": "folder-1",
            "zone": "ru-central1-a",
            "ip": "198.51.100.44",
            "address_id": "addr-2",
            "matched": False,
        }

        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            with mock.patch.object(web.subprocess, "Popen", return_value=FakeProcess()):
                result = app.start_spin(account["id"])
            state_path = Path(result["run"]["state_path"])
            state_path.write_text(json.dumps({"recent_allocations": [first_attempt]}), encoding="utf-8")
            app.status_payload()

            state_path.write_text(
                json.dumps({"recent_allocations": [first_attempt, second_attempt]}),
                encoding="utf-8",
            )
            status = app.status_payload()

            self.assertEqual([item["attempt_number"] for item in status["attempts"]], [1, 2])
            self.assertEqual([item["display_number"] for item in status["attempts"]], [1, 2])
            self.assertEqual([item["ip_seen_before"] for item in status["attempts"]], [False, True])
            self.assertEqual([item["ip_uniqueness"] for item in status["attempts"]], ["unique", "repeat"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cleanup_attempt_result_deletes_address_folder_empty_cloud_and_unprotects(self):
        root = fresh_test_dir("web-cleanup-attempt")
        calls = []

        class FakeApiError(Exception):
            status = 500
            message = "boom"

            def text(self):
                return self.message

        class FakeTokenProvider:
            def __init__(self, config, config_dir):
                self.config = config
                self.config_dir = config_dir

        class FakeYandexCloudClient:
            def __init__(self, token_provider, dry_run, operation_timeout_seconds, operation_poll_seconds):
                self.token_provider = token_provider
                self.dry_run = dry_run
                self.operation_timeout_seconds = operation_timeout_seconds
                self.operation_poll_seconds = operation_poll_seconds

            def delete_address(self, address_id, wait=True):
                calls.append(("address", address_id, wait))

            def delete_folder(self, folder_id, immediate=True, wait=True):
                calls.append(("folder", folder_id, immediate, wait))

            def get_cloud(self, cloud_id):
                calls.append(("get_cloud", cloud_id))
                return {"id": cloud_id, "name": "ip-hunter-20260507", "labels": {"managed-by": "yc-ip-hunter"}}

            def list_folders(self, cloud_id):
                calls.append(("list_folders", cloud_id))
                return [{"id": "folder-1", "cloudId": cloud_id, "status": "PENDING_DELETION"}]

            def delete_cloud(self, cloud_id, immediate=True, wait=True):
                calls.append(("cloud", cloud_id, immediate, wait))

        fake_yc = type(
            "FakeYc",
            (),
            {
                "ApiError": FakeApiError,
                "TokenProvider": FakeTokenProvider,
                "YandexCloudClient": FakeYandexCloudClient,
            },
        )

        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(
                    protected_cloud_ids=["cloud-1", "cloud-keep"],
                    protected_folder_ids=["folder-1", "folder-keep"],
                )
            )
            with app.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO attempts(
                        run_id, account_id, attempt_key, attempt_number, at, ip,
                        zone, cloud_id, folder_id, address_id, matched
                    )
                    VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account["id"],
                        "cleanup-1",
                        1,
                        "2026-05-06T10:00:00Z",
                        "198.51.100.44",
                        "ru-central1-a",
                        "cloud-1",
                        "folder-1",
                        "addr-1",
                        1,
                    ),
                )
                attempt_id = cursor.lastrowid
                conn.commit()

            with mock.patch.object(web, "load_yc_ip_hunter_module", return_value=fake_yc):
                result = app.cleanup_attempt_result(account["id"], attempt_id)

            self.assertTrue(result["ok"])
            self.assertEqual(
                calls,
                [
                    ("address", "addr-1", True),
                    ("folder", "folder-1", True, False),
                    ("get_cloud", "cloud-1"),
                    ("list_folders", "cloud-1"),
                    ("cloud", "cloud-1", True, False),
                ],
            )
            self.assertEqual(result["attempt"]["cloud_cleanup_status"], "deleted")
            updated = app.get_account(account["id"])
            self.assertEqual(updated["protected_cloud_ids"], ["cloud-keep"])
            self.assertEqual(updated["protected_folder_ids"], ["folder-keep"])
            with app.connect() as conn:
                attempt = conn.execute(
                    "SELECT cleanup_status, cleanup_error, cloud_cleanup_status FROM attempts WHERE id=?",
                    (attempt_id,),
                ).fetchone()
            self.assertEqual(attempt["cleanup_status"], "deleted")
            self.assertEqual(attempt["cleanup_error"], "")
            self.assertEqual(attempt["cloud_cleanup_status"], "deleted")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cleanup_attempt_result_can_retry_cloud_cleanup_for_deleted_ip(self):
        root = fresh_test_dir("web-cleanup-cloud-retry")
        calls = []

        class FakeApiError(Exception):
            status = 500
            message = "boom"

            def text(self):
                return self.message

        class FakeTokenProvider:
            def __init__(self, config, config_dir):
                self.config = config
                self.config_dir = config_dir

        class FakeYandexCloudClient:
            def __init__(self, token_provider, dry_run, operation_timeout_seconds, operation_poll_seconds):
                self.token_provider = token_provider
                self.dry_run = dry_run
                self.operation_timeout_seconds = operation_timeout_seconds
                self.operation_poll_seconds = operation_poll_seconds

            def delete_address(self, address_id, wait=True):
                calls.append(("address", address_id, wait))

            def delete_folder(self, folder_id, immediate=True, wait=True):
                calls.append(("folder", folder_id, immediate, wait))

            def get_cloud(self, cloud_id):
                calls.append(("get_cloud", cloud_id))
                return {"id": cloud_id, "name": "ip-hunter-20260507", "labels": {"managed-by": "yc-ip-hunter"}}

            def list_folders(self, cloud_id):
                calls.append(("list_folders", cloud_id))
                return [{"id": "folder-1", "cloudId": cloud_id, "status": "PENDING_DELETION"}]

            def delete_cloud(self, cloud_id, immediate=True, wait=True):
                calls.append(("cloud", cloud_id, immediate, wait))

        fake_yc = type(
            "FakeYc",
            (),
            {
                "ApiError": FakeApiError,
                "TokenProvider": FakeTokenProvider,
                "YandexCloudClient": FakeYandexCloudClient,
            },
        )

        try:
            app = make_app(root)
            account = app.create_account(sample_payload(protected_cloud_ids=[], protected_folder_ids=[]))
            with app.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO attempts(
                        run_id, account_id, attempt_key, attempt_number, at, ip,
                        zone, cloud_id, folder_id, address_id, matched, cleanup_status
                    )
                    VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account["id"],
                        "cleanup-cloud-retry-1",
                        1,
                        "2026-05-06T10:00:00Z",
                        "198.51.100.44",
                        "ru-central1-a",
                        "cloud-1",
                        "folder-1",
                        "addr-1",
                        1,
                        "deleted",
                    ),
                )
                attempt_id = cursor.lastrowid
                conn.commit()

            with mock.patch.object(web, "load_yc_ip_hunter_module", return_value=fake_yc):
                result = app.cleanup_attempt_result(account["id"], attempt_id)

            self.assertTrue(result["ok"])
            self.assertEqual(calls, [("get_cloud", "cloud-1"), ("list_folders", "cloud-1"), ("cloud", "cloud-1", True, False)])
            self.assertEqual(result["attempt"]["cloud_cleanup_status"], "deleted")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cleanup_attempt_result_keeps_cloud_with_active_folder(self):
        root = fresh_test_dir("web-cleanup-cloud-not-empty")
        calls = []

        class FakeApiError(Exception):
            status = 500
            message = "boom"

            def text(self):
                return self.message

        class FakeTokenProvider:
            def __init__(self, config, config_dir):
                self.config = config
                self.config_dir = config_dir

        class FakeYandexCloudClient:
            def __init__(self, token_provider, dry_run, operation_timeout_seconds, operation_poll_seconds):
                self.token_provider = token_provider
                self.dry_run = dry_run
                self.operation_timeout_seconds = operation_timeout_seconds
                self.operation_poll_seconds = operation_poll_seconds

            def delete_address(self, address_id, wait=True):
                calls.append(("address", address_id, wait))

            def delete_folder(self, folder_id, immediate=True, wait=True):
                calls.append(("folder", folder_id, immediate, wait))

            def get_cloud(self, cloud_id):
                calls.append(("get_cloud", cloud_id))
                return {"id": cloud_id, "name": "ip-hunter-20260507", "labels": {"managed-by": "yc-ip-hunter"}}

            def list_folders(self, cloud_id):
                calls.append(("list_folders", cloud_id))
                return [
                    {"id": "folder-1", "cloudId": cloud_id, "status": "PENDING_DELETION"},
                    {"id": "folder-active", "cloudId": cloud_id, "status": "ACTIVE"},
                ]

            def delete_cloud(self, cloud_id, immediate=True, wait=True):
                calls.append(("cloud", cloud_id, immediate, wait))

        fake_yc = type(
            "FakeYc",
            (),
            {
                "ApiError": FakeApiError,
                "TokenProvider": FakeTokenProvider,
                "YandexCloudClient": FakeYandexCloudClient,
            },
        )

        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            with app.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO attempts(
                        run_id, account_id, attempt_key, attempt_number, at, ip,
                        zone, cloud_id, folder_id, address_id, matched
                    )
                    VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account["id"],
                        "cleanup-not-empty-1",
                        1,
                        "2026-05-06T10:00:00Z",
                        "198.51.100.44",
                        "ru-central1-a",
                        "cloud-1",
                        "folder-1",
                        "addr-1",
                        1,
                    ),
                )
                attempt_id = cursor.lastrowid
                conn.commit()

            with mock.patch.object(web, "load_yc_ip_hunter_module", return_value=fake_yc):
                result = app.cleanup_attempt_result(account["id"], attempt_id)

            self.assertTrue(result["ok"])
            self.assertNotIn(("cloud", "cloud-1", True, False), calls)
            self.assertEqual(result["attempt"]["cloud_cleanup_status"], "skipped_not_empty")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cleanup_attempt_result_keeps_configured_project_folder(self):
        root = fresh_test_dir("web-cleanup-project-folder")
        calls = []

        class FakeApiError(Exception):
            status = 500
            message = "boom"

            def text(self):
                return self.message

        class FakeTokenProvider:
            def __init__(self, config, config_dir):
                self.config = config
                self.config_dir = config_dir

        class FakeYandexCloudClient:
            def __init__(self, token_provider, dry_run, operation_timeout_seconds, operation_poll_seconds):
                self.token_provider = token_provider
                self.dry_run = dry_run
                self.operation_timeout_seconds = operation_timeout_seconds
                self.operation_poll_seconds = operation_poll_seconds

            def delete_address(self, address_id, wait=True):
                calls.append(("address", address_id, wait))

            def delete_folder(self, folder_id, immediate=True, wait=True):
                calls.append(("folder", folder_id, immediate, wait))

        fake_yc = type(
            "FakeYc",
            (),
            {
                "ApiError": FakeApiError,
                "TokenProvider": FakeTokenProvider,
                "YandexCloudClient": FakeYandexCloudClient,
            },
        )

        try:
            app = make_app(root)
            account = app.create_account(
                sample_payload(
                    roll_mode="project",
                    target_cloud_id="project-cloud",
                    folder_id="project-folder",
                    protected_cloud_ids=["project-cloud"],
                    protected_folder_ids=["project-folder"],
                )
            )
            with app.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO attempts(
                        run_id, account_id, attempt_key, attempt_number, at, ip,
                        zone, cloud_id, folder_id, address_id, matched
                    )
                    VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account["id"],
                        "cleanup-project-1",
                        1,
                        "2026-05-06T10:00:00Z",
                        "198.51.100.44",
                        "ru-central1-a",
                        "project-cloud",
                        "project-folder",
                        "addr-1",
                        1,
                    ),
                )
                attempt_id = cursor.lastrowid
                conn.commit()

            with mock.patch.object(web, "load_yc_ip_hunter_module", return_value=fake_yc):
                result = app.cleanup_attempt_result(account["id"], attempt_id)

            self.assertTrue(result["ok"])
            self.assertEqual(calls, [("address", "addr-1", True)])
            self.assertEqual(result["attempt"]["folder_cleanup"], "kept_project_folder")
            updated = app.get_account(account["id"])
            self.assertEqual(updated["protected_cloud_ids"], [])
            self.assertEqual(updated["protected_folder_ids"], [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_cleanup_attempt_result_rejects_non_matching_attempt(self):
        root = fresh_test_dir("web-cleanup-miss")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            with app.connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO attempts(
                        run_id, account_id, attempt_key, attempt_number, at, ip,
                        zone, cloud_id, folder_id, address_id, matched
                    )
                    VALUES(NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account["id"],
                        "cleanup-miss-1",
                        1,
                        "2026-05-06T10:00:00Z",
                        "198.51.100.45",
                        "ru-central1-a",
                        "cloud-1",
                        "folder-1",
                        "addr-1",
                        0,
                    ),
                )
                attempt_id = cursor.lastrowid
                conn.commit()

            with self.assertRaisesRegex(web.WebPanelError, "успешного IP"):
                app.cleanup_attempt_result(account["id"], attempt_id)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_event_payload_is_valid_for_empty_database(self):
        root = fresh_test_dir("web-empty-events")
        try:
            app = make_app(root)
            payload = json.loads(app.event_payload())

            self.assertIsNone(payload["active_account"])
            self.assertFalse(payload["running"])
            self.assertEqual(payload["current_ip"], "-")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_static_ui_contains_russian_copy_and_setup_help(self):
        index = (MODULE_PATH.parent / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (MODULE_PATH.parent / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("Документация", index)
        self.assertIn("Изоляция", index)
        self.assertIn("Подробный лог", index)
        self.assertIn("Сохранить изоляцию", index)
        self.assertIn("Telegram", index)
        self.assertIn("Режим крутки", index)
        self.assertIn("Гибридная крутка", index)
        self.assertIn("Крутка 1 проекта", index)
        self.assertIn("ID каталога 1 проекта", index)
        self.assertIn("REDROLLER", index)
        self.assertIn("modeConfig", index)
        self.assertIn("mode-card", index)
        self.assertIn("Каталог 1 проекта", app_js)
        self.assertIn("ПОВТОР", app_js)
        self.assertNotIn("УНИК", app_js)
        self.assertIn("Действие", index)
        self.assertIn("cleanupAttempt", app_js)
        self.assertIn("/cleanup", app_js)
        self.assertIn("Удалить cloud", app_js)
        self.assertIn("Отправить тест", index)
        self.assertIn("isolationAccountSelect", index)
        self.assertIn("targetsAccountSelect", index)
        self.assertIn("Сохранить цели", index)
        self.assertIn("protectedFolderIds", index)
        self.assertIn("Поддержать проект", index)
        self.assertIn("https://dalink.to/gorizontniy", index)
        self.assertIn("supportBtn", index)
        self.assertIn("supportBlock", index)
        self.assertIn("donation-alerts-button", index)
        self.assertIn("ton-button", index)
        self.assertIn("copyDonationLinkBtn", index)
        self.assertIn("copyTonWalletBtn", index)
        self.assertIn("UQAG7KAzuYJDQ96JGYyN8wD5GOkq1sCRM787IAqOgSKPyL_z", index)
        self.assertIn("openSupportBlock", app_js)
        self.assertIn("Ссылка DaLink скопирована", app_js)
        self.assertIn("TON-кошелёк скопирован", app_js)
        self.assertIn("execCommand(\"copy\")", app_js)
        self.assertIn("Скачать файл с ключами", index)
        self.assertIn("Ваш закрытый ключ", index)
        self.assertIn("successMode", index)
        self.assertIn("Продолжать и собирать несколько IP", index)
        self.assertIn("console.yandex.cloud/folders", app_js)
        self.assertIn("Зоны ролла", index)
        self.assertIn("ID организации", index)
        self.assertIn("КРУТИТЬ БСы", index)
        self.assertIn("Аккаунт сохранён", app_js)
        self.assertIn("/isolation", app_js)
        self.assertIn("/targets", app_js)
        self.assertIn("/api/settings/telegram", app_js)
        self.assertIn("selectedRollMode", app_js)
        self.assertNotIn("protected_cloud_ids: lineList", app_js)
        self.assertIn("•••.•••.•••.•••", app_js)
        self.assertIn("Сначала добавьте и активируйте аккаунт", app_js)

    def test_static_reel_css_is_looping_and_motion_safe(self):
        css = (MODULE_PATH.parent / "web" / "app.css").read_text(encoding="utf-8")
        app_js = (MODULE_PATH.parent / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("animation-direction: normal", css)
        self.assertNotIn("alternate", css)
        self.assertNotIn("alternate-reverse", css)
        self.assertNotIn("animation-direction: reverse", css)
        self.assertIn("[...maskedReelItems(), ...maskedReelItems()]", app_js)

    def test_account_payload_errors_are_russian(self):
        root = fresh_test_dir("web-russian-errors")
        try:
            app = make_app(root)
            with self.assertRaisesRegex(web.WebPanelError, "ID организации"):
                app.create_account(sample_payload(organization_id=""))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
