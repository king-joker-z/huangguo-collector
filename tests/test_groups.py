"""模块分区、标题翻译与 NFO 演员输出的单元测试。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app import nfo
from app.config import modules_in_group
from app.db import Database
from app.settings import Settings
from app.translate import Translator


def make_card(site_id, module, title, total=1):
    return {
        "site_id": site_id,
        "module": module,
        "title": title,
        "url": f"https://example/video/{site_id}/",
        "cover_url": "",
        "score": 8.0,
        "total_ep": total,
        "views": "",
        "tags": [],
        "badges": [],
    }


class ModuleGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.settings = Settings(self.db)
        self.settings.update({"download_root": self.tmp.name})

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_existing_modules_are_in_duanju_group(self):
        self.assertEqual(
            modules_in_group("duanju"),
            ["original", "duanju", "manju", "yeguo"],
        )
        # 欧美影视分区已接入博主影片模块（ph 源）。
        self.assertEqual(modules_in_group("film"), ["ph_creator"])

    def test_group_filter_isolates_modules(self):
        self.db.upsert_card(make_card("1", "duanju", "短剧A"))
        self.db.upsert_card(make_card("2", "yeguo", "短剧B"))
        self.assertEqual(self.db.list_dramas(group="duanju")["total"], 2)
        self.assertEqual(self.db.list_dramas(group="film")["total"], 0)
        stats = self.db.stats(ttl=0)
        self.assertEqual(stats["by_group"]["duanju"], 2)
        self.assertEqual(stats["by_group"].get("film", 0), 0)

    def test_group_filter_works_for_future_adapter(self):
        # 模拟后期接入的影视模块：只加配置 + 卡片，不动查询代码。
        self.db.upsert_card(make_card("1", "duanju", "短剧A"))
        self.db.upsert_card(make_card("2", "film_x", "影视A"))

        def group_modules(group):
            return ["original", "duanju", "manju", "yeguo"] if group == "duanju" else ["film_x"]

        with mock.patch("app.db.modules_in_group", group_modules):
            self.assertEqual(self.db.list_dramas(group="film")["total"], 1)
            self.assertEqual(self.db.list_dramas(group="duanju")["total"], 1)
            self.assertEqual(self.db.list_dramas(group="film")["items"][0]["title"], "影视A")

    def test_translated_title_is_display_only(self):
        drama_id, _, _ = self.db.upsert_card(make_card("1", "duanju", "Original Title"))
        self.db.set_title_zh(drama_id, "中文译名")
        item = self.db.list_dramas(group="duanju")["items"][0]
        self.assertEqual(item["title"], "中文译名")
        self.assertEqual(item["title_original"], "Original Title")
        self.assertTrue(item["title_translated"])
        # 去重键仍基于原文，翻译不会制造/破坏同名分组。
        row = self.db.get_drama(drama_id)
        self.assertEqual(row["title"], "Original Title")

    def test_translation_cache_roundtrip(self):
        self.assertEqual(self.db.get_translation("Hello", "zh-CN"), "")
        self.db.save_translation("Hello", "zh-CN", "你好")
        self.assertEqual(self.db.get_translation("Hello", "zh-CN"), "你好")
        self.db.save_translation("Hello", "zh-CN", "您好")
        self.assertEqual(self.db.get_translation("Hello", "zh-CN"), "您好")

    def test_chinese_title_needs_no_translation(self):
        translator = Translator(self.db, self.settings)
        drama_id, _, _ = self.db.upsert_card(make_card("1", "duanju", "中文标题"))
        self.assertEqual(translator.translate_drama(drama_id), "")

    def test_stats_lists_and_settings_are_group_scoped(self):
        extra = {
            "film_x": {
                "label": "影视",
                "icon": "▷",
                "desc": "影视适配器",
                "source": "demo",
                "group": "film",
            }
        }

        def group_modules(group):
            if group == "film":
                return ["film_x"]
            return ["duanju"]

        with mock.patch.dict("app.config.MODULES", extra), mock.patch(
            "app.db.modules_in_group", group_modules
        ):
            film_id, _, _ = self.db.upsert_card(make_card("f1", "film_x", "Film A"))
            self.db.upsert_card(make_card("d1", "duanju", "Drama B", total=3))
            self.db.execute("UPDATE dramas SET has_update=1")

            film = self.db.stats(ttl=0, group="film")
            self.assertEqual(film["total"], 1)
            self.assertEqual(film["status"]["none"], 1)
            self.assertEqual(film["by_module"], {"film_x": 1})
            self.assertEqual(film["by_group"]["duanju"], 1)
            self.assertEqual([item["title"] for item in self.db.updates(10, group="film")], ["Film A"])
            self.assertEqual(
                [item["title"] for item in self.db.list_dramas(group="film")["items"]], ["Film A"]
            )
            self.assertEqual(
                [item["title"] for item in self.db.list_dramas(group="duanju")["items"]], ["Drama B"]
            )
            tag_id = self.db.ensure_tag("影视标签", "#66ccff", "custom")
            self.db.add_tag_to_drama(film_id, tag_id)
            self.assertEqual([t["name"] for t in self.db.list_tags(group="film")], ["影视标签"])
            self.assertEqual([t["name"] for t in self.db.list_tags(group="duanju")], [])

            # 设置按分区存储，未设置的分区回退默认值。
            self.settings.update({"quality": "720p"}, group="film")
            self.assertEqual(self.settings.get("quality", group="film"), "720p")
            self.assertEqual(self.settings.get("quality", group="duanju"), "hevc")
            self.assertEqual(self.settings.get("quality"), "hevc")

            film_root = str(Path(self.tmp.name) / "film-root")
            self.settings.update({"download_root": film_root}, group="film")
            self.assertEqual(str(self.settings.root_for("film")), film_root)
            self.assertNotEqual(str(self.settings.root_for("duanju")), film_root)

            drama = self.db.get_drama(film_id)
            self.assertTrue(str(nfo.show_folder(self.settings, dict(drama))).startswith(film_root))


class NfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "test.db")
        self.settings = Settings(self.db)
        self.settings.update({"download_root": str(self.root / "library")})
        self.avatar = self.root / "avatar.jpg"
        Image.new("RGB", (48, 48), (180, 30, 60)).save(self.avatar)

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_show_nfo_uses_translated_title_and_actors(self):
        drama = {
            "module": "duanju",
            "source": "huangguo",
            "title": "Original Title",
            "title_zh": "中文译名",
            "site_id": "8888",
            "year": 2026,
            "tags_site": [],
            "actors": [{"name": "Jane Doe", "role": "演员", "thumb": str(self.avatar)}],
        }
        path = nfo.write_show_nfo(self.settings, drama, [])
        text = path.read_text(encoding="utf-8")
        self.assertIn("<title>中文译名</title>", text)
        self.assertIn("<originaltitle>Original Title</originaltitle>", text)
        self.assertIn("<name>Jane Doe</name>", text)
        self.assertIn("<thumb>actors/Jane Doe.jpg</thumb>", text)
        copied = path.parent / "actors" / "Jane Doe.jpg"
        self.assertTrue(copied.is_file())

    def test_plot_falls_back_to_title(self):
        drama = {
            "module": "duanju",
            "source": "huangguo",
            "title": "Only Title",
            "site_id": "9999",
            "tags_site": [],
        }
        text = nfo.write_show_nfo(self.settings, drama, []).read_text(encoding="utf-8")
        self.assertIn("<plot>Only Title</plot>", text)

    def test_media_title_prefers_translation(self):
        self.assertEqual(
            nfo.media_title({"title": "Original", "title_zh": "译名"}), "译名"
        )
        self.assertEqual(nfo.media_title({"title": "Original"}), "Original")


if __name__ == "__main__":
    unittest.main()
