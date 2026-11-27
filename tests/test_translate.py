"""标题翻译：繁体→简体转换（GB2312 检测）+ Google 接口联动。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.translate import Translator, cjk_ratio, is_traditional_text, needs_script_conversion


class ScriptDetectionTests(unittest.TestCase):
    def test_detects_traditional(self):
        self.assertTrue(is_traditional_text("紅鑽細跟黃色螺旋飛機杯"))
        self.assertFalse(is_traditional_text("红钻细跟黄色螺旋飞机杯"))
        self.assertFalse(is_traditional_text("Hello World 123"))

    def test_needs_script_conversion(self):
        self.assertTrue(needs_script_conversion("紅鑽細跟黃色螺旋飛機杯", "zh-CN"))
        self.assertFalse(needs_script_conversion("红钻细跟黄色螺旋飞机杯", "zh-CN"))
        self.assertFalse(needs_script_conversion("Hello World", "zh-CN"))
        self.assertTrue(needs_script_conversion("简体中文标题", "zh-TW"))
        self.assertFalse(needs_script_conversion("繁體中文標題", "zh-TW"))

    def test_cjk_ratio(self):
        self.assertGreater(cjk_ratio("紅鑽細跟"), 0.9)
        self.assertLess(cjk_ratio("Hello World"), 0.1)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.get = mock.Mock(return_value=FakeResponse(payload))

    def get_direct(self, url, **kwargs):
        return FakeResponse(self.payload)


class TranslateDramaTests(unittest.TestCase):
    def setUp(self):
        from app.db import Database
        from app.settings import Settings

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "test.db")
        self.settings = Settings(self.db)
        self.settings.update({"download_root": str(self.root / "lib"), "translate_titles": "1"}, group="film")
        self.drama_id, _, _ = self.db.upsert_card(
            {
                "site_id": "ph_phXYZ",
                "module": "ph_creator",
                "title": "紅鑽細跟黃色螺旋飛機杯",
                "url": "https://x.example.com/view_video.php?viewkey=XYZ",
                "total_ep": 1,
                "tags": [],
                "badges": [],
                "source": "ph",
                "creator_key": "model/abc",
            }
        )

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_traditional_title_gets_converted(self):
        translator = Translator(self.db, self.settings)
        google_payload = [[["红钻细跟黄色螺旋飞机杯", "紅鑽細跟黃色螺旋飛機杯", None, None]]]
        with mock.patch.object(Translator, "_http", return_value=FakeSession(google_payload)):
            value = translator.translate_drama(self.drama_id, force=True)
        self.assertEqual(value, "红钻细跟黄色螺旋飞机杯")
        row = self.db.get_drama(self.drama_id)
        self.assertEqual(row["title_zh"], "红钻细跟黄色螺旋飞机杯")
        self.assertEqual(row["title"], "紅鑽細跟黃色螺旋飛機杯")  # 原文保留

    def test_simplified_chinese_title_is_skipped(self):
        self.db.execute(
            "UPDATE dramas SET title=? WHERE id=?", ("已经是简体中文标题", self.drama_id)
        )
        translator = Translator(self.db, self.settings)

        def no_http():
            raise AssertionError("不应请求翻译接口")

        with mock.patch.object(Translator, "_http", side_effect=no_http):
            value = translator.translate_drama(self.drama_id, force=True)
        self.assertEqual(value, "")

    def test_english_title_translated_and_cached(self):
        self.db.execute(
            "UPDATE dramas SET title=? WHERE id=?", ("Amateur blonde video", self.drama_id)
        )
        translator = Translator(self.db, self.settings)
        google_payload = [[["业余金发视频", "Amateur blonde video", None, None]]]
        session = FakeSession(google_payload)
        with mock.patch.object(Translator, "_http", return_value=session):
            value = translator.translate_drama(self.drama_id, force=True)
            self.assertEqual(value, "业余金发视频")
            calls = session.get.call_count
            # 二次调用走缓存，不再请求
            translator.translate_drama(self.drama_id, force=True)
        self.assertEqual(session.get.call_count, calls)


if __name__ == "__main__":
    unittest.main()
