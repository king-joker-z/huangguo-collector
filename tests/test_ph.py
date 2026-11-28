"""欧美影视（博主页）适配器与演员订阅测试。

fixture 按用户提供的页面结构构造：
- 博主页：#getAvatar 头像、ul[2]/li 卡片（div/div[1]/a 封面链接、
  div[2]/div[2]/span/a 标题）、底部翻页。
- 视频页：标签块（深嵌套 div）、视频区域内的 flashvars mediaDefinitions。
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from app.adapters import ph as ph_mod
from app.adapters.ph import PhScraper, duration_to_seconds
from app.config import MODULES


def _video_area_divs() -> str:
    """构造视频页主体，与用户提供的绝对 XPath 严格对应。

    视频区域：/html/body/div[7]/div[2]/div[3]/div[1]/div/div[1]/div[1]/div[1]/div[1]/div[1]
    标签块：  /html/body/div[7]/div[2]/div[3]/div[1]/div/div[1]/div[1]/div[1]/div[7]/div[1]/div[4]/div[2]/div[2]

    第 8 层节点有 7 个 div 子节点：子 1 = 视频区域（内含 flashvars），
    子 7 = 标签块祖先。
    """
    return """
    <div class="body-f1"></div><div class="body-f2"></div><div class="body-f3"></div>
    <div class="body-f4"></div><div class="body-f5"></div><div class="body-f6"></div>
    <div id="body-7">
      <div class="c1"></div>
      <div id="lvl2">
        <div class="c1"></div><div class="c2"></div>
        <div id="lvl3">
          <div id="lvl4">
            <div id="lvl5">
              <div id="lvl6">
                <div id="lvl7">
                  <div id="lvl8">
                    <!-- 子 1：视频区域 div[1]/div[1] -->
                    <div id="videoArea">
                      <div id="playerInner">
                        <script>
                          flashvars_987654 = {
                            "video_id": "987654",
                            "duration": 754,
                            "link": "/view_video.php?viewkey=ph987654",
                            "mediaDefinitions": [
                              {"format": "mp4", "videoUrl": "https:\\/\\/ev.phncdn.com\\/videos\\/987654\\/1080.mp4", "quality": "1080"},
                              {"format": "mp4", "videoUrl": "https:\\/\\/ev.phncdn.com\\/videos\\/987654\\/720.mp4", "quality": "720"},
                              {"format": "mp4", "videoUrl": "https:\\/\\/ev.phncdn.com\\/videos\\/987654\\/480.mp4", "quality": "480"},
                              {"format": "hls", "videoUrl": "https:\\/\\/ev.phncdn.com\\/videos\\/987654\\/index.m3u8", "quality": "ALL"}
                            ]
                          };
                        </script>
                        <video id="nicePlayer"></video>
                      </div>
                    </div>
                    <div class="filler2"></div><div class="filler3"></div>
                    <div class="filler4"></div><div class="filler5"></div>
                    <div class="filler6"></div>
                    <!-- 子 7：标签块祖先 div[7] -->
                    <div id="tagsRoot">
                      <div id="t1">
                        <div class="t1a"></div><div class="t1b"></div><div class="t1c"></div>
                        <div id="t4">
                          <div class="t4a"></div>
                          <div id="t42">
                            <div class="t42a"></div>
                            <div id="TAGS">
                              <a href="/video/tags/anal">Amateur</a>
                              <a href="/video/tags/blonde">Blonde</a>
                              <a href="/video/tags/lingerie">Lingerie</a>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    """


VIDEO_PAGE = f"""<!doctype html>
<html><head>
  <meta property="og:image" content="https://ei.phncdn.com/videos/987654/original.jpg">
</head>
<body>
  <h1 class="title"><span class="inlineFree">Amateur blonde in lingerie</span></h1>
  <!-- 陷阱：推荐位视频的时长，绝不能被抓成本视频时长 -->
  <var class="duration">38:48</var>
  <section class="userInfo">
    <a href="/model/janedoe" class="username"><span>Jane Doe</span></a>
    <div class="userAvatar"><img src="https://ei.phncdn.com/avatars/jane.jpg"></div>
  </section>
  {_video_area_divs()}
  <meta itemprop="duration" content="PT12M34S">
  <meta itemprop="uploadDate" content="2025-11-02">
  <span id="votePercentText">96%</span>
  <var class="views">1,234,567 views</var>
</body></html>"""


CREATOR_PAGE = """<!doctype html>
<html><head><title>Jane Doe's videos - ExampleSite</title></head>
<body>
  <div id="outer"><div id="mid"><div id="inner">
  <section>
    <div class="head"><div><div>
      <div id="getAvatar"><img src="https://ei.phncdn.com/avatars/jane_main.jpg" alt="Jane Doe"></div>
      <h1 class="username">Jane Doe</h1>
    </div></div></div>
    <div class="content"><div>
      <div class="grid"><div>
        <ul class="playlists"><li>playlists here</li></ul>
        <ul id="mostRecentVideosSection">
          <li>
            <div class="videoBox">
              <div class="thumb"><div><a href="/view_video.php?viewkey=ph987654">
                <img data-thumb_url="https://ei.phncdn.com/videos/987654/original.jpg" alt="Amateur blonde in lingerie">
              </a></div></div>
              <div class="details"><div class="meta"><div><span>
                <a href="/view_video.php?viewkey=ph987654">Amateur blonde in lingerie</a>
              </span></div></div></div>
              <var class="duration">12:34</var>
              <var class="views">1.2M</var>
            </div>
          </li>
          <li>
            <div class="videoBox">
              <div class="thumb"><div><a href="/view_video.php?viewkey=ph111222">
                <img data-thumb_url="https://ei.phncdn.com/videos/111222/original.jpg" alt="Second video title">
              </a></div></div>
              <div class="details"><div class="meta"><div><span>
                <a href="/view_video.php?viewkey=ph111222">Second video title</a>
              </span></div></div></div>
              <var class="duration">08:02</var>
            </div>
          </li>
        </ul>
      </div></div>
      <div class="pager"><div class="center"><div class="pagination">
        <ul><li><a href="?page=1">1</a></li><li><a href="?page=2">2</a></li><li><a href="?page=3">3</a></li></ul>
      </div></div></div>
      <!-- 翻页之后的底部推荐位：其他博主的视频，不应被采集 -->
      <div class="recommended">
        <ul id="recommendedVideos">
          <li><div><a href="/view_video.php?viewkey=phOTHER1"><img alt="Other video one"></a></div></li>
          <li><div><a href="/view_video.php?viewkey=phOTHER2"><img alt="Other video two"></a></div></li>
          <li><div><a href="/view_video.php?viewkey=phOTHER3"><img alt="Other video three"></a></div></li>
          <li><div><a href="/view_video.php?viewkey=phOTHER4"><img alt="Other video four"></a></div></li>
          <li><div><a href="/view_video.php?viewkey=phOTHER5"><img alt="Other video five"></a></div></li>
        </ul>
      </div>
    </div>
  </section>
  </div></div></div>
</body></html>"""


class PhBase(unittest.TestCase):
    def setUp(self):
        from app.db import Database
        from app.settings import Settings

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = Database(self.root / "test.db")
        self.settings = Settings(self.db)
        self.settings.update(
            {"download_root": str(self.root / "library"), "ph_base": "https://www.example-site.com"},
            group="film",
        )
        self.scraper = PhScraper(self.db, self.settings)
        # 断网测试：所有 HTTP 都走 fixture
        self.pages = {}
        self.text_patcher = mock.patch.object(
            PhScraper, "_get_text", side_effect=lambda url: self._page(url)
        )
        self.text_patcher.start()
        self.addCleanup(self.text_patcher.stop)
        # 封面/头像下载一律失败（模拟无网络），避免测试访问外网
        self.bytes_patcher = mock.patch.object(
            ph_mod.PhHttp, "get_bytes", side_effect=ph_mod.ScrapeError("no network in tests")
        )
        self.bytes_patcher.start()
        self.addCleanup(self.bytes_patcher.stop)

    def _page(self, url: str) -> str:
        for key, html in self.pages.items():
            if key in url:
                return html
        if "viewkey=ph111222" in url:
            return VIDEO_PAGE.replace("Amateur blonde in lingerie", "Second video title")
        if "viewkey=" in url:
            return VIDEO_PAGE
        return CREATOR_PAGE

    def tearDown(self):
        self.db.conn.close()
        self.tmp.cleanup()


class ParsingTests(PhBase):
    def test_creator_from_url_normalization(self):
        info = self.scraper.creator_from_url("https://www.example-site.com/model/janedoe/videos")
        self.assertEqual(info["key"], "model/janedoe")
        self.assertEqual(info["page_url"], "https://www.example-site.com/model/janedoe/videos")

        info = self.scraper.creator_from_url("https://cn.example-site.com/channels/foo/")
        self.assertEqual(info["key"], "channels/foo")
        self.assertEqual(info["page_url"], "https://cn.example-site.com/channels/foo/videos")

        info = self.scraper.creator_from_url("model/janedoe")
        self.assertEqual(info["key"], "model/janedoe")
        self.assertTrue(info["page_url"].startswith("https://www.example-site.com/"))

    def test_parse_creator_info_avatar(self):
        info = self.scraper.parse_creator_info(CREATOR_PAGE)
        self.assertEqual(info["name"], "Jane Doe")
        self.assertEqual(info["avatar_url"], "https://ei.phncdn.com/avatars/jane_main.jpg")

    def test_parse_listing_cards(self):
        cards = self.scraper.parse_listing(CREATOR_PAGE, "ph_creator")
        self.assertEqual(len(cards), 2)
        ids = {card["site_id"] for card in cards}
        self.assertEqual(ids, {"ph_ph987654", "ph_ph111222"})
        first = cards[0]
        self.assertEqual(first["site_id"], "ph_ph987654")
        self.assertEqual(first["module"], "ph_creator")
        self.assertEqual(first["title"], "Amateur blonde in lingerie")
        self.assertEqual(first["url"], "https://www.example-site.com/view_video.php?viewkey=ph987654")
        self.assertEqual(first["cover_url"], "https://ei.phncdn.com/videos/987654/original.jpg")
        self.assertEqual(first["source"], "ph")
        self.assertEqual(first["total_ep"], 1)
        self.assertEqual(first["duration"], 754)
        self.assertEqual(cards[1]["site_id"], "ph_ph111222")
        self.assertEqual(cards[1]["duration"], 482)
        # 推荐位（其他博主）的视频必须被排除：只解析主视频列表
        for card in cards:
            self.assertNotIn("OTHER", card["site_id"])

    def test_parse_pages(self):
        self.assertEqual(self.scraper.parse_pages(CREATOR_PAGE), 3)

    def test_scope_main_list_prefers_biggest_ul(self):
        """没有标准 id 时：翻页之前的列表是主列表，翻页后的推荐位被排除。"""
        html = CREATOR_PAGE.replace('id="mostRecentVideosSection"', 'id="whatever"')
        cards = self.scraper.parse_listing(html, "ph_creator")
        self.assertEqual(len(cards), 2)
        self.assertEqual({c["site_id"] for c in cards}, {"ph_ph987654", "ph_ph111222"})

    def test_scope_main_list_without_pager_takes_first(self):
        """没有翻页时：主列表在文档顺序第一位，推荐位在后面。"""
        html = CREATOR_PAGE.replace('id="mostRecentVideosSection"', 'id="whatever"')
        html = html.replace('class="pager"', 'class="pager-x"')
        html = html.replace('class="pagination"', 'class="pagenav"')
        cards = self.scraper.parse_listing(html, "ph_creator")
        self.assertEqual(len(cards), 2)
        self.assertEqual({c["site_id"] for c in cards}, {"ph_ph987654", "ph_ph111222"})

    def test_extract_media_from_flashvars(self):
        media = self.scraper.extract_media(VIDEO_PAGE)
        self.assertEqual(
            media["variants"],
            {
                "1080": "https://ev.phncdn.com/videos/987654/1080.mp4",
                "720": "https://ev.phncdn.com/videos/987654/720.mp4",
                "480": "https://ev.phncdn.com/videos/987654/480.mp4",
            },
        )
        self.assertEqual(media["hls"], "https://ev.phncdn.com/videos/987654/index.m3u8")

    def test_extract_media_ignores_get_media_api(self):
        """mediaDefinitions 的 mp4 条目可能指向 /video/get_media JSON 接口，必须排除。

        否则 ffmpeg 会拿到 JSON（无视频流）报 "Stream map '0:v:0' matches no streams"。
        只有 hls 时 stream_url 应回退到 hls 主流。
        """
        from app.adapters.ph import PhScraper as PS

        html = r"""<script>
        flashvars_1 = {"mediaDefinitions": [
          {"format": "mp4", "videoUrl": "https://cn.example.com/video/get_media?s=abc&v=123&e=0&t=p", "quality": "1080"},
          {"format": "hls", "videoUrl": "https://ev-h.phncdn.com/hls/videos/1/1080P_4000K.mp4/master.m3u8?validfrom=1", "quality": "ALL"}
        ]};
        </script>"""
        media = self.scraper.extract_media(html)
        self.assertEqual(media["variants"], {})
        self.assertEqual(media["hls"], "https://ev-h.phncdn.com/hls/videos/1/1080P_4000K.mp4/master.m3u8?validfrom=1")

        payload = {
            "streams": {"main": media["hls"], "hevc": ""},
            "variants": media["variants"],
        }
        self.assertEqual(PS.stream_url(payload, "hevc"), media["hls"])
        self.assertEqual(PS.stream_url(payload, "720p"), media["hls"])

    def test_parse_detail_contract(self):
        detail = self.scraper.parse_detail(VIDEO_PAGE, "ph_ph987654")
        self.assertEqual(detail["site_id"], "ph_ph987654")
        self.assertEqual(detail["title"], "Amateur blonde in lingerie")
        self.assertEqual(detail["cover_url"], "https://ei.phncdn.com/videos/987654/original.jpg")
        self.assertEqual(detail["total_ep"], 1)
        self.assertEqual(detail["creator_key"], "model/janedoe")
        self.assertEqual(detail["author"], "Jane Doe")
        self.assertEqual(detail["score"], 9.6)
        self.assertEqual(detail["premiered"], "2025-11-02")
        self.assertEqual(detail["year"], 2025)
        self.assertEqual(detail["episodes"][0]["duration"], 754)
        # 时长来自本视频 flashvars（754s），不是推荐位的 38:48 也不是 meta 的兜底
        self.assertEqual(detail["duration_text"], "12:34")
        self.assertEqual(detail["description"], "")
        self.assertEqual(
            detail["actors"],
            [{"name": "Jane Doe", "role": "演员", "thumb_url": "https://ei.phncdn.com/avatars/jane.jpg"}],
        )
        # 标签：CSS 策略无匹配时走用户 XPath 兜底
        self.assertEqual(detail["tags"], ["Amateur", "Blonde", "Lingerie"])

    def test_get_playback_and_stream_url(self):
        # 探测在真实环境跑网络，单测里 mock 成全部可用
        with mock.patch.object(PhScraper, "_probe_hls_candidate", return_value=True), \
             mock.patch.object(PhScraper, "_probe_media", return_value=True):
            payload = self.scraper.get_playback("ph_ph987654", 1)
        self.assertEqual(payload["streams"]["main"], "https://ev.phncdn.com/videos/987654/index.m3u8")
        self.assertEqual(payload["streams"]["mp4"], "https://ev.phncdn.com/videos/987654/1080.mp4")
        self.assertEqual(
            PhScraper.stream_url(payload, "hevc"), "https://ev.phncdn.com/videos/987654/1080.mp4"
        )
        self.assertEqual(
            PhScraper.stream_url(payload, "720p"), "https://ev.phncdn.com/videos/987654/720.mp4"
        )
        headers = PhScraper.cdn_headers(payload)
        self.assertEqual(headers["Referer"], "https://www.example-site.com/")
        self.assertIn("ev.phncdn.com", self.scraper.allowed_hosts())

    def test_get_playback_probes_and_drops_broken_streams(self):
        """探测失败的候选流被丢弃；全部失败时退回第一条 hls。"""
        media = self.scraper.extract_media(VIDEO_PAGE)
        self.assertEqual(media["hls_list"], [("", "https://ev.phncdn.com/videos/987654/index.m3u8")])

        # hls 可用、mp4 全部失效（如 CDN 轮换 token 过期）
        with mock.patch.object(PhScraper, "_probe_hls_candidate", return_value=True), \
             mock.patch.object(PhScraper, "_probe_media", return_value=False):
            payload = self.scraper.get_playback("ph_ph987654", 1)
        self.assertEqual(payload["streams"]["main"], "https://ev.phncdn.com/videos/987654/index.m3u8")
        self.assertNotIn("mp4", payload["streams"])
        self.assertEqual(PhScraper.stream_url(payload, "hevc"), "https://ev.phncdn.com/videos/987654/index.m3u8")

        # 全部失效：退回页面第一条 hls，仍然可下载
        with mock.patch.object(PhScraper, "_probe_hls_candidate", return_value=False), \
             mock.patch.object(PhScraper, "_probe_media", return_value=False):
            payload2 = self.scraper.get_playback("ph_ph987654", 1)
        self.assertEqual(payload2["streams"]["main"], "https://ev.phncdn.com/videos/987654/index.m3u8")

    def test_hls_quality_variants_sorted_desc(self):
        """多条带画质的 hls 按画质降序进入画质表，探测后按画质选用。"""
        html = r"""<script>
        flashvars_2 = {"mediaDefinitions": [
          {"format": "hls", "videoUrl": "https://ev.phncdn.com/hls/240P/master.m3u8", "quality": "240"},
          {"format": "hls", "videoUrl": "https://ev.phncdn.com/hls/1080P/master.m3u8", "quality": "1080"},
          {"format": "hls", "videoUrl": "https://ev.phncdn.com/hls/720P/master.m3u8", "quality": "720"}
        ]};
        </script>"""
        media = self.scraper.extract_media(html)
        self.assertEqual(
            [q for q, _ in media["hls_list"]], ["1080", "720", "240"]
        )
        self.pages["viewkey=zz9"] = html
        with mock.patch.object(PhScraper, "_probe_hls_candidate", return_value=True):
            payload = self.scraper.get_playback("ph_zz9", 1)
        self.assertEqual(payload["streams"]["main"], "https://ev.phncdn.com/hls/1080P/master.m3u8")
        self.assertEqual(
            PhScraper.stream_url(payload, "720p"), "https://ev.phncdn.com/hls/720P/master.m3u8"
        )
        self.assertEqual(
            PhScraper.stream_url(payload, "hevc"), "https://ev.phncdn.com/hls/1080P/master.m3u8"
        )

    def test_duration_helpers(self):
        self.assertEqual(duration_to_seconds("12:34"), 754)
        self.assertEqual(duration_to_seconds("1:02:03"), 3723)
        self.assertEqual(duration_to_seconds("PT12M34S"), 754)
        self.assertEqual(duration_to_seconds(""), 0)

    def test_unconfigured_base_raises(self):
        self.settings.update({"ph_base": ""}, group="film")
        with self.assertRaises(ph_mod.ScrapeError):
            self.scraper._require_base()
        self.assertEqual(self.scraper.site_base, "")


class CreatorSubscriptionTests(PhBase):
    def setUp(self):
        super().setUp()
        from app.creators import CreatorManager

        self.avatar_file = self.root / "avatar.jpg"
        Image.new("RGB", (48, 48), (200, 100, 50)).save(self.avatar_file, "JPEG")
        self.creator_patcher = mock.patch(
            "app.creators.download_avatar", return_value=str(self.avatar_file)
        )
        self.creator_patcher.start()
        self.addCleanup(self.creator_patcher.stop)
        self.manager = CreatorManager(self.db, self.settings, {"ph": self.scraper})

    def test_add_creator_stores_name_and_avatar(self):
        item, warning = self.manager.add("https://www.example-site.com/model/janedoe")
        self.assertEqual(warning, "")
        self.assertEqual(item["name"], "Jane Doe")
        self.assertEqual(item["creator_key"], "model/janedoe")
        self.assertEqual(item["avatar_path"], str(self.avatar_file))
        self.assertEqual(item["page_url"], "https://www.example-site.com/model/janedoe/videos")
        self.assertEqual(item["source"], "ph")

        rows = self.manager.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["videos"], 0)

    def test_add_rejects_bad_url(self):
        with self.assertRaises(ph_mod.ScrapeError):
            self.manager.add("")

    def test_update_toggle_and_delete(self):
        item, _ = self.manager.add("https://www.example-site.com/model/janedoe")
        creator_id = int(item["id"])
        updated = self.manager.update(creator_id, auto_download=False, quality="720p", max_pages=2)
        self.assertEqual(updated["auto_download"], 0)
        self.assertEqual(updated["quality"], "720p")
        self.assertEqual(updated["max_pages"], 2)

        self.manager.update(creator_id, enabled=False)
        self.assertEqual(self.db.list_creator_subs(enabled_only=True), [])

        self.manager.delete(creator_id)
        self.assertIsNone(self.db.get_creator_sub(creator_id))
        with self.assertRaises(KeyError):
            self.manager.delete(creator_id)

    def test_list_tabs_follows_enabled_subs(self):
        self.assertEqual(self.scraper.list_tabs("ph_creator"), [])
        item, _ = self.manager.add("https://www.example-site.com/model/janedoe")
        self.manager.add("https://www.example-site.com/channels/other")
        tabs = self.scraper.list_tabs("ph_creator")
        self.assertEqual(tabs, [("sub:1", "Jane Doe"), ("sub:2", "Jane Doe")])
        self.manager.update(int(item["id"]), enabled=False)
        self.assertEqual(len(self.scraper.list_tabs("ph_creator")), 1)


class SegmentDownloadTests(PhBase):
    """分片断点续传：已下载分片跳过、失败刷新 token、本地播放列表生成。"""

    def _payload(self):
        return {
            "streams": {"main": "https://x.example.com/master.m3u8", "hevc": ""},
            "variants": {"1080": "https://x.example.com/master.m3u8"},
        }

    def test_resume_skips_cached_segments(self):
        payload = self._payload()
        segs = [{"url": f"https://x/seg-{i}.ts", "duration": 6.0} for i in range(4)]
        workdir = self.root / "segs"
        workdir.mkdir(parents=True)
        # 预置两个已下载分片（模拟上次中断）
        (workdir / "seg_0000.ts").write_bytes(b"a" * 100)
        (workdir / "seg_0001.ts").write_bytes(b"b" * 100)
        fetched = []

        def fake_fetch(url, target, headers, proxies):
            fetched.append(target.name)
            target.write_bytes(b"c" * 50)
            return True

        with mock.patch.object(PhScraper, "_load_segments", return_value=segs), \
             mock.patch.object(PhScraper, "_fetch_segment", side_effect=fake_fetch):
            info = self.scraper.download_stream(payload, "ph_abc", 1, "hevc", workdir)
        # 只下载缺失的两片，已有分片零流量
        self.assertEqual(sorted(fetched), ["seg_0002.ts", "seg_0003.ts"])
        self.assertEqual(info["segments"], 4)
        self.assertAlmostEqual(info["duration"], 24.0)
        text = Path(info["playlist"]).read_text(encoding="utf-8")
        self.assertIn("#EXT-X-ENDLIST", text)
        self.assertIn("seg_0003.ts", text)
        self.assertIn("#EXTINF:6.000,", text)

    def test_refreshes_token_on_segment_failure(self):
        payload = self._payload()
        segs = [{"url": f"https://x/seg-{i}.ts", "duration": 6.0} for i in range(2)]
        workdir = self.root / "segs2"
        calls = {"fetch": 0, "playback": 0}

        def fake_fetch(url, target, headers, proxies):
            calls["fetch"] += 1
            if calls["fetch"] == 1:
                return False  # 第一片失败 → 触发 token 刷新
            target.write_bytes(b"c" * 50)
            return True

        def fake_playback(site_id, ep):
            calls["playback"] += 1
            return payload

        with mock.patch.object(PhScraper, "_load_segments", return_value=segs), \
             mock.patch.object(PhScraper, "_fetch_segment", side_effect=fake_fetch), \
             mock.patch.object(PhScraper, "get_playback", side_effect=fake_playback):
            info = self.scraper.download_stream(payload, "ph_abc", 1, "hevc", workdir)
        self.assertEqual(calls["playback"], 1)  # 刷新了一次 token 后从断点继续
        self.assertEqual(info["segments"], 2)

    def test_direct_url_fallback_when_no_hls(self):
        payload = {
            "streams": {"main": "https://x/video.mp4", "mp4": "https://x/video.mp4"},
            "variants": {"1080": "https://x/video.mp4"},
        }
        info = self.scraper.download_stream(payload, "ph_abc", 1, "hevc", self.root / "segs3")
        self.assertEqual(info.get("playlist"), "")
        self.assertEqual(info.get("direct_url"), "https://x/video.mp4")


class ScanFlowTests(PhBase):
    """演员订阅驱动的完整扫描链路：列表 → 详情 → creator_key → 自动下载。"""

    class FakeDownloads:
        def __init__(self):
            self.calls: list[tuple[int, str]] = []

        def enqueue_new(self, drama_id, quality, force=False):
            self.calls.append((drama_id, quality))
            return {"queued": 1, "skipped": 0}

    def setUp(self):
        super().setUp()
        from app.creators import CreatorManager
        from app.scan import ScanManager

        self.downloads = self.FakeDownloads()
        self.manager = CreatorManager(self.db, self.settings, {"ph": self.scraper})
        with mock.patch("app.creators.download_avatar", return_value=str(self.root / "a.jpg")):
            self.manager.add("https://www.example-site.com/model/janedoe", quality="h264")
        self.scan = ScanManager(self.db, self.settings, {"ph": self.scraper}, self.downloads)

    def test_scan_creator_writes_movies_and_auto_downloads(self):
        self.assertIn("ph_creator", MODULES)
        started, _ = self.scan.start(["ph_creator"], details=True, tab="sub:1")
        self.assertTrue(started)
        self.scan.thread.join(15)
        self.assertEqual(self.scan.status()["state"], "done")

        films = self.db.list_dramas(group="film")
        self.assertEqual(films["total"], 2)
        titles = {item["title"] for item in films["items"]}
        self.assertEqual(titles, {"Amateur blonde in lingerie", "Second video title"})

        for item in films["items"]:
            self.assertEqual(item["source"], "ph")
            row = self.db.get_drama(int(item["id"]))
            self.assertEqual(row["creator_key"], "model/janedoe")
            self.assertEqual(row["author"], "Jane Doe")
            actors = json.loads(row["actors_json"])
            self.assertEqual(actors[0]["name"], "Jane Doe")
            episodes = self.db.get_episodes(int(item["id"]))
            self.assertEqual(len(episodes), 1)

        # 自动下载：画质跟随演员订阅（h264）
        self.assertEqual(len(self.downloads.calls), 2)
        self.assertTrue(all(quality == "h264" for _, quality in self.downloads.calls))

        sub = self.db.get_creator_sub(1)
        self.assertEqual(sub["video_count"], 2)
        self.assertEqual(sub["last_new_count"], 2)
        # 每部影片单独入队：最后一次自动下载批量为 1
        self.assertEqual(sub["last_auto_count"], 1)
        self.assertTrue(sub["last_auto_at"])

    def test_scan_without_subs_reports_error(self):
        self.db.execute("DELETE FROM creator_subs")
        started, _ = self.scan.start(["ph_creator"], details=True)
        self.assertTrue(started)
        self.scan.thread.join(15)
        self.assertEqual(self.scan.status()["state"], "done")
        self.assertTrue(
            any("没有可采集的列表" in err for err in self.scan.status()["errors"])
        )


class MovieScrapeTests(PhBase):
    """电影模式：横版 poster 原样保留、NFO 与视频同名、演员头像进 actors/。"""

    def test_movie_nfo_with_actor_thumb_and_landscape_poster(self):
        from app import nfo

        avatar = self.root / "jane.jpg"
        Image.new("RGB", (60, 60), (120, 60, 200)).save(avatar, "JPEG")
        cover = self.root / "cover.jpg"
        Image.new("RGB", (640, 360), (30, 90, 160)).save(cover, "JPEG")

        drama = {
            "module": "ph_creator",
            "source": "ph",
            "title": "Amateur blonde in lingerie",
            "title_zh": "业余金发",
            "site_id": "ph_ph987654",
            "year": 2025,
            "premiered": "2025-11-02",
            "tags_site": ["Amateur", "Blonde"],
            "cover_path": str(cover),
            "actors": [{"name": "Jane Doe", "role": "演员", "thumb": str(avatar)}],
        }
        nfo.write_art(self.settings, drama)
        poster = nfo.show_folder(self.settings, drama) / "poster.jpg"
        self.assertTrue(poster.exists())
        with Image.open(poster) as image:
            # 电影模块：poster 保留横版原样，不再转 2:3
            self.assertEqual(image.size, (640, 360))

        path = nfo.write_show_nfo(self.settings, drama, [])
        # NFO 与视频同名（Emby 电影惯例），不再用 movie.nfo
        self.assertEqual(path.name, "业余金发 (2025).nfo")
        text = path.read_text(encoding="utf-8")
        self.assertIn("<movie>", text)
        self.assertIn("<title>业余金发</title>", text)
        self.assertIn("<originaltitle>Amateur blonde in lingerie</originaltitle>", text)
        self.assertIn("<plot>业余金发</plot>", text)  # 简介用标题兜底
        self.assertIn("<name>Jane Doe</name>", text)
        self.assertTrue((path.parent / "actors").is_dir())
        self.assertIn("<thumb>actors/", text)
        self.assertIn("欧美影视", text)

    def test_write_art_force_regenerates_and_cleans_legacy(self):
        from app import nfo

        cover = self.root / "cover2.jpg"
        Image.new("RGB", (640, 360), (10, 60, 120)).save(cover, "JPEG")
        drama = {
            "module": "ph_creator",
            "source": "ph",
            "title": "Force Rebuild",
            "site_id": "ph_force1",
            "year": 2026,
            "tags_site": [],
            "actors": [],
            "cover_path": str(cover),
        }
        folder = nfo.show_folder(self.settings, drama)
        # 模拟历史产物：旧的 2:3 poster + 旧 movie.nfo
        folder.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (600, 900), (0, 0, 0)).save(folder / "poster.jpg", "JPEG")
        (folder / "movie.nfo").write_text("<movie>old</movie>", encoding="utf-8")

        nfo.write_art(self.settings, drama, force=True)
        nfo.write_show_nfo(self.settings, drama, [])
        with Image.open(folder / "poster.jpg") as image:
            self.assertEqual(image.size, (640, 360))  # 重建后为横版
        self.assertFalse((folder / "movie.nfo").exists())  # 旧命名已清理
        self.assertTrue((folder / "Force Rebuild (2026).nfo").exists())


if __name__ == "__main__":
    unittest.main()
