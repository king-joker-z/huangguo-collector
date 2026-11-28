"""适配器扩展点测试：演员头像下载、播放代理域名白名单。"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app import actors
from app.hlsproxy import HlsProxy


class FakeHttp:
    def __init__(self, content: bytes, content_type: str = "image/jpeg") -> None:
        self.content = content
        self.content_type = content_type
        self.calls: list[str] = []

    def get_bytes(self, url: str, **_kwargs):
        self.calls.append(url)
        return self.content, self.content_type


class FakeScraper:
    site_base = "https://video.example.com"
    cdn_hosts = ("cdn.example.com",)

    def __init__(self, http: FakeHttp) -> None:
        self.http = http


def jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 60, 200)).save(buffer, "JPEG")
    return buffer.getvalue()


class ActorAvatarTests(unittest.TestCase):
    def test_downloads_avatar_and_reuses_cache(self):
        scraper = FakeScraper(FakeHttp(jpeg_bytes()))
        detail = {"actors": [{"name": "Jane Doe", "role": "演员", "thumb_url": "https://x/a.jpg"}]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "app.actors.AVATARS_DIR", Path(tmp)
        ):
            first = actors.ensure_avatars(scraper, detail)
            self.assertEqual(first[0]["name"], "Jane Doe")
            self.assertTrue(Path(first[0]["thumb"]).is_file())
            actors.ensure_avatars(scraper, {"actors": detail["actors"]})
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)
            self.assertEqual(len(scraper.http.calls), 1)

    def test_local_thumb_is_kept_and_broken_download_falls_back(self):
        scraper = FakeScraper(FakeHttp(b"too small"))
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / "local.jpg"
            local.write_bytes(jpeg_bytes())
            detail = {
                "actors": [
                    {"name": "Local", "thumb": str(local)},
                    {"name": "Broken", "thumb_url": "https://x/b.jpg"},
                    {"name": "", "thumb_url": "https://x/c.jpg"},
                ]
            }
            with mock.patch("app.actors.AVATARS_DIR", Path(tmp) / "avatars"):
                result = actors.ensure_avatars(scraper, detail)
            self.assertEqual([a["name"] for a in result], ["Local", "Broken"])
            self.assertEqual(result[0]["thumb"], str(local))
            self.assertEqual(result[1]["thumb"], "")


class ProviderCdnHostTests(unittest.TestCase):
    def test_provider_hosts_extend_allowlist(self):
        proxy = HlsProxy(None, None, {"demo": FakeScraper(FakeHttp(b""))})
        self.assertTrue(proxy._allowed("https://video.example.com/a.ts"))
        self.assertTrue(proxy._allowed("https://img.cdn.example.com/a.ts"))
        self.assertFalse(proxy._allowed("https://evil.example/a.ts"))
        self.assertTrue(proxy._allowed("https://x.qnlycc.cn/a.ts"))


class ScanWithAdapterTests(unittest.TestCase):
    """用内存适配器跑一遍完整扫描链路，验证 5 个接口契约。"""

    def setUp(self):
        from app.db import Database
        from app.settings import Settings

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "test.db")
        self.settings = Settings(self.db)
        self.settings.update({"download_root": str(self.root / "library")})

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_scan_writes_cards_detail_and_episodes(self):
        from app.scan import ScanManager

        class FakeAdapter:
            site_base = "https://video.example.com"
            check_url = site_base + "/"
            cdn_hosts = ("cdn.example.com",)

            def page_count(self, module, tab="latest"):
                return 1

            def fetch_listing_page(self, module, page, tab="latest"):
                return [
                    {
                        "site_id": "fake_1",
                        "module": module,
                        "title": "Example Film",
                        "url": "https://video.example.com/video/1/",
                        "cover_url": "",
                        "score": 8.0,
                        "total_ep": 3,
                        "tags": ["Demo"],
                        "views": "",
                        "badges": [],
                        "source": "fake",
                    }
                ], 1

            def fetch_detail(self, site_id):
                return {
                    "site_id": site_id,
                    "title": "Example Film",
                    "description": "",
                    "cover_url": "",
                    "score": 8.0,
                    "total_ep": 3,
                    "tags": ["Demo"],
                    "year": 2026,
                    "episodes": [
                        {"ep": n, "site_video_id": f"v{n}", "title": f"EP{n}"}
                        for n in (1, 2, 3)
                    ],
                    "actors": [{"name": "Jane Doe", "role": "演员"}],
                }

            def get_playback(self, site_id, ep):
                return {
                    "id": site_id,
                    "streams": {"main": "https://cdn.example.com/x.m3u8"},
                    "cdn": {},
                }

            def save_cover(self, site_id, cover_url):
                return ""

        extra = {
            "film_x": {
                "label": "影视",
                "icon": "▷",
                "base": "/",
                "desc": "测试影视",
                "source": "fake",
                "group": "film",
            }
        }
        with mock.patch.dict("app.config.MODULES", extra):
            scan = ScanManager(self.db, self.settings, {"fake": FakeAdapter()}, None)
            started, _ = scan.start(["film_x"], details=True)
            self.assertTrue(started)
            scan.thread.join(15)
            self.assertEqual(scan.status()["state"], "done")

            film = self.db.list_dramas(group="film")
            self.assertEqual(film["total"], 1)
            self.assertEqual(film["items"][0]["title"], "Example Film")
            drama_id = int(film["items"][0]["id"])
            self.assertEqual(len(self.db.get_episodes(drama_id)), 3)
            self.assertEqual(
                [a["name"] for a in film["items"][0]["actors"]], ["Jane Doe"]
            )


class MovieModeTests(unittest.TestCase):
    """media_type=movie 时按 Emby 电影结构刮削。"""

    def setUp(self):
        from app.db import Database
        from app.settings import Settings

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "test.db")
        self.settings = Settings(self.db)
        self.settings.update({"download_root": str(self.root / "library")})

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()

    def test_movie_paths_and_movie_nfo(self):
        from app import nfo

        extra = {
            "film_movie": {
                "label": "电影",
                "icon": "▷",
                "base": "/",
                "desc": "电影模块",
                "source": "fake",
                "group": "film",
                "media_type": "movie",
            }
        }
        drama = {
            "module": "film_movie",
            "source": "fake",
            "title": "Example Movie",
            "title_zh": "示例电影",
            "site_id": "fake_9",
            "year": 2026,
            "tags_site": ["Demo"],
            "actors": [],
        }
        with mock.patch.dict("app.config.MODULES", extra):
            folder = nfo.show_folder(self.settings, drama)
            path = nfo.episode_path(self.settings, drama, 1)
            self.assertEqual(path.parent, folder)
            self.assertEqual(path.name, "示例电影 (2026).mp4")
            # 电影 NFO 与视频同名（Emby 电影惯例）
            self.assertEqual(nfo.episode_nfo_path(self.settings, drama, 1), folder / "示例电影 (2026).nfo")

            info = nfo.write_show_nfo(self.settings, drama, [])
            self.assertEqual(info.name, "示例电影 (2026).nfo")
            text = info.read_text(encoding="utf-8")
            self.assertIn("<movie>", text)
            self.assertIn("<title>示例电影</title>", text)
            self.assertIn("<originaltitle>Example Movie</originaltitle>", text)
            self.assertNotIn("<episode>", text)
            self.assertIsNone(nfo.write_episode_nfo(self.settings, drama, {"ep": 1}))

            prepared = nfo.prepare_show(self.settings, drama, [])
            self.assertEqual(prepared, folder)
            self.assertFalse((folder / "Season 01").exists())

    def test_tv_mode_keeps_season_layout(self):
        from app import nfo

        drama = {
            "module": "duanju",
            "source": "huangguo",
            "title": "TV Show",
            "site_id": "10086",
            "year": 2026,
            "tags_site": [],
            "actors": [],
        }
        folder = nfo.prepare_show(self.settings, drama, [])
        self.assertTrue((folder / "Season 01").is_dir())
        self.assertEqual(nfo.episode_path(self.settings, drama, 2).name, "TV Show - S01E02.mp4")


if __name__ == "__main__":
    unittest.main()
