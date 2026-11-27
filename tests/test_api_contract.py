"""API 契约测试：所有带 group 参数的接口必须能正常调用。

这个测试专门防止「参数签名不匹配」这类只有运行时才暴露的问题
（例如 /api/activity 调用 downloads.active(group=...)）。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import api
from app.covers import CoverBackfillManager
from app.db import Database
from app.downloader import DownloadManager
from app.scan import ScanManager
from app.settings import Settings
from app.translate import TitleTranslateManager, Translator


class FakeScheduler:
    active_schedule_id = None


class ApiGroupContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = Database(root / "test.db")
        self.settings = Settings(self.db)
        self.settings.update({"download_root": str(root / "library")})
        self.downloads = DownloadManager(self.db, self.settings, {})
        self.scan = ScanManager(self.db, self.settings, {}, self.downloads)
        self.covers = CoverBackfillManager(self.db, self.settings)
        self.translator = Translator(self.db, self.settings)
        self.title_translate = TitleTranslateManager(self.db, self.settings, self.translator)
        self.patches = [
            mock.patch.object(api, "db", self.db),
            mock.patch.object(api, "settings", self.settings),
            mock.patch.object(api, "downloads", self.downloads),
            mock.patch.object(api, "scan", self.scan),
            mock.patch.object(api, "covers", self.covers),
            mock.patch.object(api, "translator", self.translator),
            mock.patch.object(api, "title_translate", self.title_translate),
            mock.patch.object(api, "scheduler", FakeScheduler()),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.db.conn.close()
        self.tmp.cleanup()

    def test_all_group_endpoints_accept_group(self):
        group = "film"
        self.assertTrue(api.health(group=group)["ok"])
        stats = api.stats(group=group)
        self.assertEqual(stats["total"], 0)
        activity = api.activity(group=group)
        self.assertEqual(activity["stats"]["group"], group)
        self.assertEqual(api.tasks(state="all", limit=5, group=group)["tasks"], [])
        self.assertEqual(api.updates(limit=5, group=group)["total"], 0)
        self.assertEqual(api.subscriptions(group=group)["total"], 0)
        self.assertEqual(api.duplicates(group=group)["total"], 0)
        self.assertEqual(api.tags(group=group)["tags"], [])
        self.assertEqual(api.scan_history(limit=5, group=group)["items"], [])
        self.assertEqual(api.history(group=group)["items"], [])
        self.assertEqual(api.list_schedules(group=group)["items"], [])
        settings = api.get_settings(group=group)
        self.assertEqual(settings["group"], group)
        self.assertIn("quality", settings["settings"])
        translate = api.translate_status(group=group)
        self.assertEqual(translate["group"], group)
        self.assertEqual(api.list_dramas(group=group)["total"], 0)
        self.assertEqual(api.list_drama_ids(group=group)["total"], 0)

    def test_activity_uses_scoped_task_list(self):
        self.db.upsert_card(
            {
                "site_id": "1",
                "module": "duanju",
                "title": "示例剧",
                "url": "https://example/1",
                "cover_url": "",
                "score": 0,
                "total_ep": 1,
                "tags": [],
                "badges": [],
            }
        )
        activity = api.activity(group="duanju")
        self.assertEqual(activity["stats"]["group"], "duanju")
        self.assertEqual(activity["summary"]["queued"], 0)


if __name__ == "__main__":
    unittest.main()
