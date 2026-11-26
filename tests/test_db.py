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

    def test_same_title_keeps_more_episodes(self):
        def make(site_id, source, module, total):
            card = {
                "site_id": site_id,
                "source": source,
                "module": module,
                "title": "同名测试剧",
                "url": "https://example/video/" + site_id,
                "cover_url": "",
                "score": 0,
                "total_ep": total,
                "views": "",
                "tags": [],
                "badges": [],
            }
            drama_id, _, _ = self.db.upsert_card(card)
            detail = {
                "title": "同名测试剧",
                "description": "x",
                "cover_url": "",
                "score": 0,
                "total_ep": total,
                "tags": [],
                "episodes": [
                    {"ep": ep, "title": f"第{ep}集", "duration": 60}
                    for ep in range(1, total + 1)
                ],
            }
            self.db.apply_detail(site_id, detail)
            return drama_id

        huangguo_id = make("10010", "huangguo", "duanju", 5)
        yeguo_id = make("yeguo_10010", "yeguo", "yeguo", 10)

        huangguo = self.db.get_drama(huangguo_id)
        yeguo = self.db.get_drama(yeguo_id)
        self.assertEqual(huangguo["is_duplicate"], 1)
        self.assertEqual(huangguo["duplicate_of"], yeguo_id)
        self.assertEqual(yeguo["is_duplicate"], 0)
        self.assertEqual(self.db.list_dramas()["total"], 1)
        self.assertEqual(self.db.stats(ttl=0)["duplicates"], 1)

        # 红果版本更新到 12 集后应重新成为主版本
        make("10010", "huangguo", "duanju", 12)
        huangguo = self.db.get_drama(huangguo_id)
        yeguo = self.db.get_drama(yeguo_id)
        self.assertEqual(huangguo["is_duplicate"], 0)
        self.assertEqual(yeguo["is_duplicate"], 1)
        self.assertEqual(yeguo["duplicate_of"], huangguo_id)

    def test_excluded_site_and_custom_tags(self):
        card = {
            "site_id": "10020",
            "module": "duanju",
            "title": "标签测试",
            "url": "https://example/video/10020/",
            "cover_url": "",
            "score": 0,
            "total_ep": 1,
            "views": "",
            "tags": ["乱伦", "都市"],
            "badges": [],
        }
        drama_id, _, _ = self.db.upsert_card(card)
        detail = {
            "title": "标签测试",
            "description": "x",
            "cover_url": "",
            "score": 0,
            "total_ep": 1,
            "tags": ["乱伦", "都市"],
            "episodes": [{"ep": 1, "title": "第1集", "duration": 60}],
        }
        self.db.apply_detail("10020", detail)
        self.assertEqual(self.db.list_dramas()["total"], 1)
        self.db.add_excluded_tag("乱伦")
        self.assertTrue(self.db.is_tag_excluded(drama_id))
        self.assertEqual(self.db.list_dramas()["total"], 0)
        self.assertEqual(self.db.stats(ttl=0)["total"], 0)
        self.db.remove_excluded_tag("乱伦")
        self.assertEqual(self.db.list_dramas()["total"], 1)

        tag_id = self.db.ensure_tag("自建屏蔽", "#ff0000", "custom")
        self.db.add_tag_to_drama(drama_id, tag_id)
        self.db.add_excluded_tag("自建屏蔽")
        self.assertEqual(self.db.list_dramas()["total"], 0)
        self.db.remove_excluded_tag("自建屏蔽")
        self.assertEqual(self.db.list_dramas()["total"], 1)

    def test_pause_resume(self):
        manager = DownloadManager(self.db, Settings(self.db), None)
        self.assertFalse(manager.is_paused)
        manager.pause()
        self.assertTrue(manager.is_paused)
        manager.resume()
        self.assertFalse(manager.is_paused)

    def test_list_drama_ids_respects_filters(self):
        for site_id, module in (("10030", "duanju"), ("10031", "original")):
            card = {
                "site_id": site_id,
                "module": module,
                "title": f"筛选测试{site_id}",
                "url": f"https://example/video/{site_id}/",
                "cover_url": "", "score": 0, "total_ep": 1,
                "views": "", "tags": [], "badges": [],
            }
            self.db.upsert_card(card)
        ids = self.db.list_drama_ids(module="duanju")
        self.assertEqual(len(ids), 1)
        self.assertEqual(len(self.db.list_drama_ids()), 2)

    def test_manual_duplicate_preference(self):
        def make(site_id, total):
            card = {
                "site_id": site_id, "module": "duanju", "title": "手动主版本测试",
                "url": f"https://example/video/{site_id}/", "cover_url": "",
                "score": 0, "total_ep": total, "views": "", "tags": [], "badges": [],
            }
            drama_id, _, _ = self.db.upsert_card(card)
            self.db.apply_detail(site_id, {
                "title": "手动主版本测试", "description": "x", "cover_url": "",
                "score": 0, "total_ep": total, "tags": [],
                "episodes": [{"ep": i, "title": f"第{i}集", "duration": 60} for i in range(1, total + 1)],
            })
            return drama_id

        first = make("10040", 5)
        second = make("10041", 10)
        tag_id = self.db.ensure_tag("合并标签", "#ff0000", "custom")
        self.db.add_tag_to_drama(second, tag_id)
        self.db.set_drama_fields(second, favorite=1)
        groups = self.db.list_duplicate_groups()
        group = next(g for g in groups if g["title"] == "手动主版本测试")
        self.assertEqual(group["preferred"]["id"], second)

        self.db.prefer_duplicate(first, merge_metadata=True)
        groups = self.db.list_duplicate_groups()
        group = next(g for g in groups if g["title"] == "手动主版本测试")
        self.assertEqual(group["preferred"]["id"], first)
        self.assertTrue(group["manual"])
        self.assertEqual(group["preferred"]["is_duplicate"], False)
        self.assertTrue(self.db.get_drama(first)["favorite"])
        self.assertIn("合并标签", [t["name"] for t in self.db.tags_for_drama(first)])

        self.db.reset_duplicate_preference(first)
        groups = self.db.list_duplicate_groups()
        group = next(g for g in groups if g["title"] == "手动主版本测试")
        self.assertEqual(group["preferred"]["id"], second)
        self.assertFalse(group["manual"])

    def test_scan_run_history(self):
        run_id = self.db.create_scan_run(["original"], False, 1, False)
        self.db.finish_scan_run(run_id, "done", 10, 2, 3, "完成")
        runs = self.db.list_scan_runs()
        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["state"], "done")
        self.assertEqual(runs[0]["found"], 10)
        self.assertEqual(runs[0]["options"]["modules"], ["original"])

        running = self.db.create_scan_run(["yeguo"], True, None, False)
        self.db.mark_interrupted_scan_runs()
        self.assertEqual(self.db.get_scan_run(running)["state"], "interrupted")

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
