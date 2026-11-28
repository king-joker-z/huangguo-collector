import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.downloader import DownloadManager
from app.nfo import media_title
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

    def test_film_dramas_never_dedup(self):
        """欧美影视（ph 模块）不参与同名去重，只有短剧模块去重。"""

        def make(site_id, source, module, total, title):
            card = {
                "site_id": site_id,
                "source": source,
                "module": module,
                "title": title,
                "url": f"https://example/video/{site_id}/",
                "cover_url": "",
                "score": 0,
                "total_ep": total,
                "views": "",
                "tags": [],
                "badges": [],
            }
            drama_id, _, _ = self.db.upsert_card(card)
            self.db.apply_detail(site_id, {
                "title": title,
                "description": "x",
                "cover_url": "",
                "score": 0,
                "total_ep": total,
                "tags": [],
                "episodes": [
                    {"ep": ep, "title": f"第{ep}集", "duration": 60}
                    for ep in range(1, total + 1)
                ],
            })
            return drama_id

        # 同名影片在欧美影视内各自独立展示，不会被标记为重复
        first = make("ph_key1", "ph", "ph_creator", 1, "同名影片")
        second = make("ph_key2", "ph", "ph_creator", 2, "同名影片")
        self.assertFalse(bool(self.db.get_drama(first)["is_duplicate"]))
        self.assertFalse(bool(self.db.get_drama(second)["is_duplicate"]))
        self.assertEqual(self.db.list_dramas(group="film")["total"], 2)
        self.assertEqual(self.db.stats(group="film", ttl=0)["duplicates"], 0)
        self.assertEqual(self.db.list_duplicate_groups(scope="film"), [])
        self.assertEqual(self.db.list_duplicate_groups(), [])

        # 欧美影片与短剧同名：不合并，欧美影片保持可见
        film = make("ph_key3", "ph", "ph_creator", 5, "混合同名")
        duanju = make("10060", "huangguo", "duanju", 10, "混合同名")
        self.assertFalse(bool(self.db.get_drama(film)["is_duplicate"]))
        self.assertFalse(bool(self.db.get_drama(duanju)["is_duplicate"]))
        self.assertEqual(self.db.list_duplicate_groups(), [])

        # 同名短剧之间照常去重（集数多者为主版本），欧美影片不受影响
        yeguo = make("yeguo_10060", "yeguo", "yeguo", 20, "混合同名")
        self.assertFalse(bool(self.db.get_drama(yeguo)["is_duplicate"]))
        self.assertTrue(bool(self.db.get_drama(duanju)["is_duplicate"]))
        self.assertFalse(bool(self.db.get_drama(film)["is_duplicate"]))
        groups = self.db.list_duplicate_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(
            {int(i["id"]) for i in groups[0]["items"]},
            {int(duanju), int(yeguo)},
        )

        # 历史遗留的重复标记：重新打开库时自动清除
        self.db.execute(
            "UPDATE dramas SET is_duplicate=1,duplicate_of=?,keep_separate=1,manual_preferred=1 WHERE id=?",
            (int(yeguo), int(film)),
        )
        reopened = Database(Path(self.tmp.name) / "test.db")
        try:
            row = reopened.get_drama(film)
            self.assertFalse(bool(row["is_duplicate"]))
            self.assertEqual(int(row["duplicate_of"] or 0), 0)
            self.assertFalse(bool(row["keep_separate"]))
            self.assertFalse(bool(row["manual_preferred"]))
        finally:
            reopened.conn.close()

    def test_duration_persisted_and_backfilled(self):
        """影片时长：列表卡片先写入，详情时长优先；老数据重新打开库时回填。"""
        card = {
            "site_id": "ph_dur1",
            "source": "ph",
            "module": "ph_creator",
            "title": "时长测试",
            "url": "https://example/view_video.php?viewkey=ph_dur1",
            "cover_url": "",
            "score": 0,
            "total_ep": 1,
            "views": "",
            "tags": [],
            "badges": [],
            "duration": 754,
            "duration_text": "12:34",
        }
        drama_id, _, _ = self.db.upsert_card(card)
        row = self.db.get_drama(drama_id)
        self.assertEqual(int(row["duration"]), 754)
        self.assertEqual(row["duration_text"], "12:34")

        # 详情页时长优先
        self.db.apply_detail("ph_dur1", {
            "title": "时长测试",
            "total_ep": 1,
            "tags": [],
            "duration": 60,
            "duration_text": "01:00",
            "episodes": [{"ep": 1, "title": "正片", "duration": 60}],
        })
        self.assertEqual(int(self.db.get_drama(drama_id)["duration"]), 60)

        # 列表卡片再次写入不会覆盖已知时长
        self.db.upsert_card(card)
        self.assertEqual(int(self.db.get_drama(drama_id)["duration"]), 60)

        # 详情缺时长时用分集时长兜底
        card2 = dict(card, site_id="ph_dur2", title="回填测试", duration=0, duration_text="")
        drama2, _, _ = self.db.upsert_card(card2)
        self.db.apply_detail("ph_dur2", {
            "title": "回填测试",
            "total_ep": 1,
            "tags": [],
            "episodes": [{"ep": 1, "title": "正片", "duration": 95}],
        })
        self.assertEqual(int(self.db.get_drama(drama2)["duration"]), 95)

        # 老库缺时长：重新打开库时用分集时长回填（幂等）
        self.db.execute("UPDATE dramas SET duration=0 WHERE site_id IN ('ph_dur1','ph_dur2')")
        reopened = Database(Path(self.tmp.name) / "test.db")
        try:
            self.assertEqual(int(reopened.get_drama(drama_id)["duration"]), 60)
            self.assertEqual(int(reopened.get_drama(drama2)["duration"]), 95)
        finally:
            reopened.conn.close()

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

    def test_keep_separate_versions(self):
        ids = []
        for site_id, total in (("10050", 3), ("10051", 6)):
            card = {
                "site_id": site_id, "module": "duanju", "title": "独立保留测试",
                "url": f"https://example/video/{site_id}/", "cover_url": "",
                "score": 0, "total_ep": total, "views": "", "tags": [], "badges": [],
            }
            drama_id, _, _ = self.db.upsert_card(card)
            ids.append(drama_id)
        rows = [self.db.get_drama(i) for i in ids]
        hidden = next(r for r in rows if r["is_duplicate"])
        visible = next(r for r in rows if not r["is_duplicate"])
        self.assertEqual(self.db.list_dramas()["total"], 1)

        self.db.set_duplicate_keep(int(hidden["id"]), True)
        self.assertFalse(self.db.get_drama(int(hidden["id"]))["is_duplicate"])
        self.assertTrue(self.db.get_drama(int(hidden["id"]))["keep_separate"])
        self.assertEqual(self.db.list_dramas()["total"], 2)
        self.assertEqual(
            media_title({"title": "独立保留测试", "keep_separate": True, "site_id": "huangguo_10051"}),
            "独立保留测试 [huangguo_10051]",
        )

        self.db.set_duplicate_keep(int(hidden["id"]), False)
        self.assertTrue(self.db.get_drama(int(hidden["id"]))["is_duplicate"])
        self.assertEqual(self.db.list_dramas()["total"], 1)
        self.assertFalse(self.db.get_drama(int(visible["id"]))["is_duplicate"])

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
