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
        "target_cloud_id": "",
        "folder_id": "",
        "zones": ["ru-central1-a", "ru-central1-e"],
        "protected_cloud_ids": [],
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

            self.assertEqual(app.delete_account(first["id"]), {"ok": True})
            self.assertEqual(len(app.list_accounts()), 1)
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
            self.assertEqual(config["zone"], "ru-central1-a")
            self.assertEqual(config["zones"], ["ru-central1-a", "ru-central1-e"])
            self.assertEqual(config["protected_cloud_ids"], [])
            self.assertEqual(config["auth"]["service_account_key_file"], "sa-key.json")
            self.assertTrue(paths["key"].exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_writes_selected_zones_and_protected_clouds(self):
        root = fresh_test_dir("web-runtime-zones")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(zones=["ru-central1-b", "ru-central1-d"]))
            app.update_account_isolation(
                account["id"],
                {"protected_cloud_ids": ["protected-1", "protected-2"]},
            )

            paths = app.build_runtime_files(account["id"])

            config = json.loads(paths["config"].read_text(encoding="utf-8"))
            self.assertEqual(config["zone"], "ru-central1-b")
            self.assertEqual(config["zones"], ["ru-central1-b", "ru-central1-d"])
            self.assertEqual(config["protected_cloud_ids"], ["protected-1", "protected-2"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_rejects_isolated_target_cloud(self):
        root = fresh_test_dir("web-runtime-isolated-target")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(target_cloud_id="target-cloud"))
            app.update_account_isolation(account["id"], {"protected_cloud_ids": ["target-cloud"]})

            with self.assertRaisesRegex(web.WebPanelError, "изоляции"):
                app.build_runtime_files(account["id"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolation_endpoint_updates_only_protected_clouds(self):
        root = fresh_test_dir("web-isolation-update")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(name="Original", protected_cloud_ids=["old-cloud"]))

            result = app.update_account_isolation(
                account["id"],
                {
                    "protected_cloud_ids": ["  b1gabc  ", "", "aoe-def", "b1gabc"],
                    "name": "Ignored",
                    "zones": ["ru-central1-d"],
                },
            )
            updated = app.get_account(account["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["account"]["name"], "Original")
            self.assertEqual(result["account"]["protected_cloud_ids"], ["b1gabc", "aoe-def"])
            self.assertEqual(updated["name"], "Original")
            self.assertEqual(updated["zones"], ["ru-central1-a", "ru-central1-e"])
            self.assertEqual(updated["protected_cloud_ids"], ["b1gabc", "aoe-def"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

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
                {"protected_cloud_ids": [" b1gabc ", "aoe-def", "b1gabc"]},
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
                    },
                },
            )

            status, body = put_json(
                f"/api/accounts/{account['id']}/isolation",
                {"protected_cloud_ids": ["valid-id", "bad/url"]},
            )
            self.assertEqual(status, 400)
            self.assertFalse(body["ok"])
            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], ["b1gabc", "aoe-def"])

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
            app.update_account_isolation(account["id"], {"protected_cloud_ids": ["b1gabc"]})

            updated = app.update_account(
                account["id"],
                sample_payload(name="Renamed", service_account_json=""),
            )

            self.assertEqual(updated["name"], "Renamed")
            self.assertEqual(updated["protected_cloud_ids"], ["b1gabc"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolation_validation_is_all_or_nothing(self):
        root = fresh_test_dir("web-isolation-validation")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(protected_cloud_ids=["b1gabc"]))

            with self.assertRaisesRegex(web.WebPanelError, "Некорректный cloud-id"):
                app.update_account_isolation(
                    account["id"],
                    {"protected_cloud_ids": ["valid-id", "https://bad.example"]},
                )

            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], ["b1gabc"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_isolation_empty_list_clears_and_repeat_save_is_idempotent(self):
        root = fresh_test_dir("web-isolation-empty")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload(protected_cloud_ids=["b1gabc"]))

            first = app.update_account_isolation(account["id"], {"protected_cloud_ids": []})
            second = app.update_account_isolation(account["id"], {"protected_cloud_ids": []})

            self.assertEqual(first["account"]["protected_cloud_ids"], [])
            self.assertEqual(second["account"]["protected_cloud_ids"], [])
            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], [])
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
            with self.assertRaises(web.WebPanelNotFound):
                app.update_account_isolation(999, {"protected_cloud_ids": []})
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_runtime_config_uses_fresh_isolation_from_database(self):
        root = fresh_test_dir("web-isolation-runtime-fresh")
        try:
            app = make_app(root)
            account = app.create_account(sample_payload())
            app.update_account_isolation(account["id"], {"protected_cloud_ids": ["first-id"]})
            first_paths = app.build_runtime_files(account["id"])
            first_config = json.loads(first_paths["config"].read_text(encoding="utf-8"))
            self.assertEqual(first_config["protected_cloud_ids"], ["first-id"])

            app.update_account_isolation(account["id"], {"protected_cloud_ids": ["second-id"]})
            second_paths = app.build_runtime_files(account["id"])
            second_config = json.loads(second_paths["config"].read_text(encoding="utf-8"))

            self.assertEqual(app.get_account(account["id"])["protected_cloud_ids"], ["second-id"])
            self.assertEqual(second_config["protected_cloud_ids"], ["second-id"])
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

            with mock.patch.object(web.subprocess, "Popen", return_value=FakeProcess()) as popen:
                result = app.start_spin(account["id"])

            self.assertTrue(result["ok"])
            command = popen.call_args.args[0]
            self.assertEqual(command[0], "python")
            self.assertIn("yc_ip_hunter.py", command[1])
            self.assertIn("--run", command)
            self.assertIn("--yes-delete-cloud", command)
            self.assertIn("--stop-file", command)
            self.assertIn("--recreate-file", command)
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
            self.assertEqual(status["attempts"][0]["ip"], "198.51.100.44")
            self.assertEqual(status["reel"][0]["ip"], "")
            self.assertTrue(status["reel"][0]["hidden"])
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
        self.assertIn("Изоляция облака", index)
        self.assertIn("Подробный лог", index)
        self.assertIn("Сохранить изоляцию", index)
        self.assertIn("isolationAccountSelect", index)
        self.assertIn("Зоны ролла", index)
        self.assertIn("ID организации", index)
        self.assertIn("КРУТИТЬ БСы", index)
        self.assertIn("Аккаунт сохранён", app_js)
        self.assertIn("/isolation", app_js)
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
