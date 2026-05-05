import importlib.util
import json
import shutil
import sys
import unittest
import datetime as dt
from unittest import mock
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("telegram_bot.py")
SPEC = importlib.util.spec_from_file_location("telegram_bot_module", MODULE_PATH)
bot = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = bot
SPEC.loader.exec_module(bot)


def fresh_test_dir(name):
    path = MODULE_PATH.parent / ".test-tmp" / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


class HunterAccountTests(unittest.TestCase):
    def test_status_reads_state_stats(self):
        root = fresh_test_dir("bot-status")
        try:
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "zone": "ru-central1-b",
                        "state_file": "state.json",
                        "log_file": "run.log",
                        "target_cidrs": ["198.51.100.0/24"],
                    }
                ),
                encoding="utf-8",
            )
            (root / "state.json").write_text(
                json.dumps(
                    {
                        "checked_count": 7,
                        "unique_checked_count": 6,
                        "last_allocated_ip": "198.51.100.7",
                        "last_zone": "ru-central1-b",
                        "success": {"ip": "198.51.100.7"},
                        "hybrid_cloud_id": "old-cloud",
                        "hybrid_folder_id": "old-folder",
                        "recent_allocations": [
                            {
                                "cloud_id": "active-cloud",
                                "folder_id": "active-folder",
                                "ip": "198.51.100.7",
                                "zone": "ru-central1-b",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            account = bot.HunterAccount(
                {"name": "Main", "workdir": str(root), "config": "config.json"},
                base_dir=root,
                default_python="python",
            )

            status = account.status()

            self.assertEqual(status["checked"], 7)
            self.assertEqual(status["unique"], 6)
            self.assertEqual(status["found_ip"], "198.51.100.7")
            self.assertEqual(status["cloud"], "active-cloud")
            self.assertEqual(status["folder"], "active-folder")
            self.assertTrue(status["success"])
            self.assertEqual(account.target_summary(), "1 CIDR + 0 IP")
            self.assertEqual(account.target_ip_capacity(), 256)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_export_targets_writes_subnet_file(self):
        root = fresh_test_dir("bot-export")
        try:
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "target_ips": ["203.0.113.10"],
                        "target_cidrs": ["198.51.100.0/24"],
                    }
                ),
                encoding="utf-8",
            )
            account = bot.HunterAccount(
                {"name": "Main", "workdir": str(root), "config": "config.json"},
                base_dir=root,
                default_python="python",
            )

            path = account.export_targets()

            text = path.read_text(encoding="utf-8")
            self.assertIn("198.51.100.0/24", text)
            self.assertIn("203.0.113.10", text)
            self.assertEqual(account.target_summary(), "1 CIDR + 1 IP")
            self.assertEqual(account.target_ip_capacity(), 257)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_allocation_stats_use_full_log_not_tail(self):
        root = fresh_test_dir("bot-log-stats")
        try:
            (root / "config.json").write_text(
                json.dumps({"log_file": "run.log", "state_file": "state.json"}),
                encoding="utf-8",
            )
            lines = [
                f"2026-05-05 13:00:{i:02d},000 INFO Allocated IP 198.51.100.{i % 10} is not in target ranges."
                for i in range(40)
            ]
            (root / "run.log").write_text("\n".join(lines), encoding="utf-8")
            account = bot.HunterAccount(
                {"name": "Main", "workdir": str(root), "config": "config.json"},
                base_dir=root,
                default_python="python",
            )

            self.assertEqual(account.allocation_stats_from_log(), (40, 10))
            status = account.status()
            self.assertEqual(status["checked"], 40)
            self.assertEqual(status["unique"], 10)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_log_event_message_detects_auto_recreate(self):
        root = fresh_test_dir("bot-log-event")
        try:
            (root / "config.json").write_text("{}", encoding="utf-8")
            account = bot.HunterAccount(
                {"name": "Main", "workdir": str(root), "config": "config.json"},
                base_dir=root,
                default_python="python",
            )

            message = account.log_event_message(
                "2026-05-05 13:00:00,000 WARNING Address rotation in cloud cloud-1 hit a limit; rotating cloud."
            )

            self.assertIsNotNone(message)
            self.assertIn("Авто", message)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_status_marks_stopped_error(self):
        root = fresh_test_dir("bot-stopped-error")
        try:
            (root / "config.json").write_text(
                json.dumps({"log_file": "run.log"}),
                encoding="utf-8",
            )
            (root / "run.log").write_text(
                "2026-05-05 14:24:38,520 ERROR Permission denied during create_address. Check service account roles for this step.\n",
                encoding="utf-8",
            )
            account = bot.HunterAccount(
                {"name": "Main", "workdir": str(root), "config": "config.json"},
                base_dir=root,
                default_python="python",
            )

            status = account.status()

            self.assertTrue(status["failed"])
            self.assertIn("🔴 ошибка", account.monitor_text())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_is_running_removes_stale_pid_file(self):
        with mock.patch.object(bot, "pid_exists", return_value=False):
            root = fresh_test_dir("bot-stale-pid")
            try:
                (root / "config.json").write_text("{}", encoding="utf-8")
                account = bot.HunterAccount(
                    {"name": "Main", "workdir": str(root), "config": "config.json"},
                    base_dir=root,
                    default_python="python",
                )
                account.pid_file.write_text("999999", encoding="utf-8")

                self.assertFalse(account.is_running())
                self.assertFalse(account.pid_file.exists())
            finally:
                shutil.rmtree(root, ignore_errors=True)

    def test_stop_confirms_recent_state_stop_without_pid(self):
        root = fresh_test_dir("bot-stop-confirmed")
        try:
            stopped_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
            (root / "config.json").write_text(
                json.dumps({"state_file": "state.json"}),
                encoding="utf-8",
            )
            (root / "state.json").write_text(
                json.dumps({"stopped_at": stopped_at}),
                encoding="utf-8",
            )
            account = bot.HunterAccount(
                {"name": "Main", "workdir": str(root), "config": "config.json"},
                base_dir=root,
                default_python="python",
            )

            ok, text = account.stop(timeout=1)

            self.assertTrue(ok)
            self.assertIn("остановлен", text)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TelegramApiTests(unittest.TestCase):
    def test_answer_callback_does_not_raise_on_network_error(self):
        api = object.__new__(bot.TelegramApi)
        api.request = mock.Mock(side_effect=bot.TelegramError("timeout"))

        self.assertFalse(api.answer_callback("callback-id"))

    def test_answer_callback_ignores_stale_query_errors(self):
        api = object.__new__(bot.TelegramApi)
        api.request = mock.Mock(
            side_effect=bot.TelegramError(
                "Telegram HTTP 400: query is too old and response timeout expired"
            )
        )

        self.assertFalse(api.answer_callback("callback-id"))

    def test_clamp_telegram_text_keeps_message_under_limit(self):
        text = bot.clamp_telegram_text("x" * (bot.TELEGRAM_MAX_TEXT + 100))

        self.assertLessEqual(len(text), bot.TELEGRAM_MAX_TEXT)
        self.assertIn("обрезано", text)


class ControlBotTests(unittest.TestCase):
    def test_initial_offset_drops_pending_updates_once(self):
        root = fresh_test_dir("bot-offset")
        try:
            (root / "config.json").write_text("{}", encoding="utf-8")
            config_path = root / "telegram_bot_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "bot_token": "123:token",
                        "allowed_chat_ids": [1],
                        "offset_file": "offset.txt",
                        "drop_pending_updates_on_start": True,
                        "accounts": [
                            {
                                "name": "Main",
                                "workdir": str(root),
                                "config": "config.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            control = bot.ControlBot(config_path)
            control.api = mock.Mock()
            control.api.get_updates.return_value = [{"update_id": 41}]

            self.assertEqual(control.initial_offset(), 42)
            self.assertEqual((root / "offset.txt").read_text(encoding="utf-8"), "42")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_add_wizard_keeps_target_cloud_empty_for_hybrid(self):
        root = fresh_test_dir("bot-add-wizard")
        try:
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "rotation_mode": "hybrid",
                        "target_cloud_id": "",
                        "hybrid_use_service_cloud_first": False,
                    }
                ),
                encoding="utf-8",
            )
            config_path = root / "telegram_bot_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "bot_token": "123:token",
                        "allowed_chat_ids": [1],
                        "accounts": [
                            {
                                "name": "Main",
                                "workdir": str(root),
                                "config": "config.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            control = bot.ControlBot(config_path)
            control.api = mock.Mock()
            control.api.download_file.return_value = b'{"id":"key-id"}'

            control.finish_add_wizard(
                1,
                {
                    "name": "Second",
                    "org_id": "org-1",
                    "billing_id": "billing-1",
                    "cloud_id": "service-cloud-1",
                },
                {"file_id": "file-1"},
            )

            created_config = json.loads((root / "config2.json").read_text(encoding="utf-8"))
            self.assertEqual(created_config["service_cloud_id"], "service-cloud-1")
            self.assertEqual(created_config["target_cloud_id"], "")
            self.assertEqual(created_config["cloud_id"], "")
            self.assertEqual(created_config["folder_id"], "")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
