"""适配器模板。

复制这个文件为 ``app/adapters/mysite.py``，改掉 BASE / 解析逻辑，然后在
``app/providers.py`` 里注册。

注意：模板指向 example.com 占位地址，不要直接注册使用。
"""
from __future__ import annotations

import io
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from PIL import Image

from ..config import COVERS_DIR, USER_AGENT
from ..scraper import HttpClient, ScrapeError

BASE = "https://video.example.com"


class ExampleScraper:
    """站点适配器：扫描器/下载器只依赖下面这几个方法。

    构造函数签名固定为 (db, settings)，由 app/providers.py 创建。
    """

    # 播放代理白名单：站点 CDN 域名，m3u8 分片域名也要包含。
    cdn_hosts = ("cdn.example.com",)

    def __init__(self, db, settings) -> None:
        self.db = db
        self.settings = settings
        self.http = HttpClient(settings)  # 复用重试 / 代理 / Cookie / 请求间隔
        self.site_base = BASE
        self.check_url = BASE + "/"

    # ------------------------------------------------------------------ 列表
    def list_url(self, module: str, page: int, tab: str = "latest") -> str:
        # TODO: 按模块/分页/列表类型拼 URL
        base = f"{BASE}/{module}/"
        return base if page <= 1 else f"{base}page/{page}/"

    def page_count(self, module: str, tab: str = "latest") -> int:
        html = self.http.get_text(self.list_url(module, 1, tab))
        return self.parse_pages(html)

    def fetch_listing_page(self, module: str, page: int, tab: str = "latest"):
        html = self.http.get_text(self.list_url(module, page, tab))
        return self.parse_listing(html, module), self.parse_pages(html)

    @staticmethod
    def parse_pages(html: str) -> int:
        soup = BeautifulSoup(html, "lxml")
        pages = 1
        for link in soup.select("a[href*='/page/']"):
            match = re.search(r"/page/(\d+)", link.get("href", ""))
            if match:
                pages = max(pages, int(match.group(1)))
        return pages

    def parse_listing(self, html: str, module: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        cards: list[dict] = []
        for node in soup.select("article.item"):  # TODO: 换成真实选择器
            link = node.select_one("a[href]")
            if not link:
                continue
            href = link.get("href", "")
            match = re.search(r"/video/(\d+)", href)
            if not match:
                continue
            site_id = match.group(1)
            img = node.select_one("img")
            cover = (img.get("data-src") or img.get("src") or "") if img else ""
            if cover.startswith("//"):
                cover = "https:" + cover
            title_el = node.select_one(".title")
            title = (title_el.get_text(" ", strip=True) if title_el else "") or (
                img.get("alt") if img else ""
            )
            cards.append(
                {
                    "site_id": site_id,
                    "module": module,
                    "title": title.strip(),
                    "url": urljoin(BASE, href),
                    "cover_url": cover.strip(),
                    "score": None,
                    "total_ep": 1,  # 单片站点固定 1；剧集站点填真实集数
                    "tags": [],
                    "views": "",
                    "badges": [],
                    "source": "example",  # 必须与 config.SOURCES 的 key 一致
                }
            )
        return cards

    # ------------------------------------------------------------------ 详情
    def fetch_detail(self, site_id: str) -> dict:
        url = f"{BASE}/video/{site_id}/"
        html = self.http.get_text(url)
        soup = BeautifulSoup(html, "lxml")

        title = soup.select_one("h1")
        cover = soup.select_one("meta[property='og:image']")
        tags = [a.get_text(" ", strip=True) for a in soup.select("a[href*='/tag/']")]
        year = None
        date_el = soup.select_one("time")
        if date_el:
            match = re.search(r"(\d{4})", date_el.get("datetime", "") or date_el.get_text())
            if match:
                year = int(match.group(1))

        # 演员：name 必填；thumb_url 会由上层下载到 data/avatars/ 并写进 NFO
        actors = []
        for node in soup.select(".cast .person"):
            name = node.get_text(" ", strip=True)
            avatar = node.select_one("img")
            if name:
                actors.append(
                    {
                        "name": name,
                        "role": "演员",
                        "thumb_url": (avatar.get("data-src") or avatar.get("src") or "") if avatar else "",
                    }
                )

        # 剧集：ep 必须从 1 连续递增；单片站点就返回 1 集
        episodes = [{"ep": 1, "site_video_id": str(site_id), "title": "正片", "duration": 0}]

        return {
            "site_id": str(site_id),
            "title": (title.get_text(" ", strip=True) if title else "").strip(),
            "description": "",
            "cover_url": (cover.get("content") if cover else "") or "",
            "score": None,
            "total_ep": len(episodes),
            "author": "",
            "status_text": "",
            "views": "",
            "premiered": "",
            "duration_text": "",
            "category_text": "",
            "tags": tags,
            "year": year,
            "episodes": episodes,
            "actors": actors,
        }

    # ------------------------------------------------------------------ 播放
    def get_playback(self, site_id: str, ep: int) -> dict:
        # TODO: 有的站点是嵌在页面里的 m3u8，有的是独立接口（可能需要 POST / 签名）
        html = self.http.get_text(f"{BASE}/video/{site_id}/")
        match = re.search(r"https?://[^\"']+?\.m3u8[^\"']*", html)
        if not match:
            raise ScrapeError(f"没有找到播放地址: {site_id} ep{ep}")
        return {
            "id": str(site_id),
            "streams": {"main": match.group(0)},
            "cdn": {"authKey": "", "authValue": ""},
        }

    @staticmethod
    def stream_url(payload: dict, quality: str) -> str:
        return (payload.get("streams") or {}).get("main") or ""

    @staticmethod
    def cdn_headers(payload: dict) -> dict[str, str]:
        return {"Referer": BASE + "/", "User-Agent": USER_AGENT}

    # ------------------------------------------------------------------ 封面
    def save_cover(self, site_id: str, cover_url: str) -> str:
        if not cover_url:
            return ""
        try:
            content, _ = self.http.get_bytes(
                cover_url, headers={"Referer": self.site_base + "/", "Accept": "image/*,*/*"}
            )
        except ScrapeError:
            return ""
        try:
            image = Image.open(io.BytesIO(content))
            image.load()
        except Exception:
            return ""
        target = COVERS_DIR / f"{site_id}.jpg"
        try:
            image.convert("RGB").save(target, "JPEG", quality=84, optimize=True)
        except Exception:
            return ""
        self.db.execute("UPDATE dramas SET cover_path=? WHERE site_id=?", (str(target), str(site_id)))
        return str(target)
