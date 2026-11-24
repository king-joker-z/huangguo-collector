import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.downloader import DownloadManager
from app.settings import Settings


class DatabaseStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_empty_library_stats(self):
        stats = self.db.stats(ttl=0)
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["episodes_total"], 0)
        self.assertEqual(stats["status"]["all"], 0)

    def test_stats_cache_returns_independent_copy(self):
        first = self.db.stats(ttl=0)
        first["status"]["all"] = 999
        second = self.db.stats(ttl=10)
        self.assertEqual(second["status"]["all"], 0)

    def test_indexes_for_hot_paths(self):
        names = {
            row["name"] for row in self.db.query("PRAGMA index_list('episodes')")
        }
        self.assertIn("idx_episodes_drama_state", names)
        task_indexes = {
            row["name"] for row in self.db.query("PRAGMA index_list('tasks')")
        }
        self.assertIn("idx_tasks_drama_state", task_indexes)

    def test_card_and_detail_stats(self):
        card = {
            "site_id": "10001",
            "module": "duanju",
            "title": "测试剧",
            "url": "https://example/video/10001/",
            "cover_url": "",
            "score": 9.0,
            "total_ep": 2,
            "views": "1.2w",
            "tags": ["测试"],
            "badges": [],
        }
        drama_id, is_new, _ = self.db.upsert_card(card)
        self.assertTrue(is_new)
        detail = {
            "title": "测试剧",
            "description": "简介",
            "cover_url": "",
            "score": 9.0,
            "total_ep": 2,
            "tags": ["测试"],
            "year": 2026,
            "episodes": [
                {"ep": 1, "title": "第1集", "duration": 60},
                {"ep": 2, "title": "第2集", "duration": 60},
            ],
        }
        self.db.apply_detail("10001", detail)
        self.db.set_episode_state(drama_id, 1, "done", "x.mp4", 10)
        stats = self.db.stats(ttl=0)
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["by_module"]["duanju"], 1)
        self.assertEqual(stats["episodes_done"], 1)
        self.assertEqual(stats["status"]["partial"], 1)

    def test_retry_failed_and_clear(self):
        card = {
            "site_id": "10002",
            "module": "duanju",
            "title": "失败测试",
            "url": "https://example/video/10002/",
            "cover_url": "",
            "score": 0,
            "total_ep": 1,
            "views": "",
            "tags": [],
            "badges": [],
        }
        drama_id, _, _ = self.db.upsert_card(card)
        detail = {
            "title": "失败测试",
            "description": "x",
            "cover_url": "",
            "score": 0,
            "total_ep": 1,
            "tags": [],
            "episodes": [{"ep": 1, "title": "第1集", "duration": 60}],
        }
        self.db.apply_detail("10002", detail)
        task_id = self.db.upsert_task(drama_id, 1, "hevc")
        self.db.update_task(task_id, state="failed", error="boom")
        self.db.set_episode_state(drama_id, 1, "failed")

        manager = DownloadManager(self.db, Settings(self.db), None)
        result = manager.retry_failed()
        self.assertEqual(result["queued"], 1)
        self.assertEqual(manager.queue.qsize(), 1)
        self.assertEqual(self.db.get_task(task_id)["state"], "queued")

        self.db.update_task(task_id, state="failed", error="again")
        self.db.clear_tasks(["failed"])
        self.assertIsNone(self.db.get_task(task_id))


if __name__ == "__main__":
    unittest.main()
