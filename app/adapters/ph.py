"""欧美影视（博主页）站点适配器。

站点结构与 Pornhub 一致：

- 博主页（model / channels / users / 任意博主列表页）：
  - ``#getAvatar`` 是博主头像（img 或 background-image）；
  - 视频卡在 ``ul > li`` 里：``li/div/div[1]/a`` 是封面+详情页链接，
    ``li/div/div[2]/div[2]/span/a`` 是标题；
  - 翻页在 section 底部（``?page=N``），需要遍历全部页。
- 视频详情页：
  - 标签在视频区域下方的标签块里；
  - 下载地址藏在播放区域（``flashvars`` / ``mediaDefinitions``）：
    mp4 直链带画质标签（1080/720/480…）+ 可选 hls。

站点根地址在设置页填写（``ph_base``，按 film 分区保存）；博主页面由
「演员订阅」（``creator_subs`` 表）维护，扫描时每个启用的订阅都是
``ph_creator`` 模块的一个列表（tab = ``sub:{id}``）。
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests
from PIL import Image

from ..config import COVERS_DIR, USER_AGENT
from ..scraper import HttpClient, ScrapeError

SITE_PREFIX = "ph_"
SOURCE_KEY = "ph"
GROUP_KEY = "film"

# 视频 ID：Pornhub 风格 viewkey（view_video.php?viewkey=xxx）
VIEWKEY_RE = re.compile(r"viewkey=([A-Za-z0-9_-]+)")
# 博主路径：/model/xxx /channels/xxx /users/xxx /pornstar/xxx
CREATOR_PATH_RE = re.compile(
    r"^/(model|channels|channel|users|user|pornstar|creator)s?/([A-Za-z0-9_.\-]+)/?", re.I
)
PAGE_PARAM_RE = re.compile(r"[?&]page=(\d+)")
DURATION_TEXT_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})\s*$")
ISO_DURATION_RE = re.compile(r"^P(?:T)?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
DATE_TEXT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
MP4_URL_RE = re.compile(r"https?://[^\"'\\\s<>]+?\.mp4[^\"'\\\s<>]*", re.I)
M3U8_URL_RE = re.compile(r"https?://[^\"'\\\s<>]+?\.m3u8[^\"'\\\s<>]*", re.I)

# 详情页标签块的选择器（按优先级尝试，兼容不同站点皮肤）。
TAG_SELECTORS = (
    ".video-detailed-tags a",
    ".tagsWrapper a",
    ".tags a",
    ".tag-list a",
    ".video-tags a",
    "div[class*='tags'] a[href*='search=']",
    "section[class*='tag'] a",
)

# 视频详情页博主页选择器（用户提供的绝对 XPath，作为兜底）。
TAG_XPATH = (
    "/html/body/div[7]/div[2]/div[3]/div[1]/div/div[1]/div[1]/div[1]"
    "/div[7]/div[1]/div[4]/div[2]/div[2]//a"
)


class PhHttp(HttpClient):
    """欧美站 HTTP：沿用重试/代理/Cookie，默认 Referer 指向当前站点。"""

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.base = ""

    def _session(self):
        session = super()._session()
        if self.base:
            session.headers["Referer"] = self.base.rstrip("/") + "/"
        return session


def duration_to_seconds(text: str) -> int:
    """把 ``12:34`` / ``1:02:03`` / ISO ``PT12M34S`` / ``754`` 转成秒。"""
    raw = str(text or "").strip()
    if not raw:
        return 0
    match = DURATION_TEXT_RE.match(raw)
    if match:
        hours = int(match.group(1) or 0)
        return hours * 3600 + int(match.group(2) or 0) * 60 + int(match.group(3) or 0)
    iso = ISO_DURATION_RE.match(raw.upper())
    if iso and any(iso.groups()):
        return int(iso.group(1) or 0) * 3600 + int(iso.group(2) or 0) * 60 + int(iso.group(3) or 0)
    if raw.isdigit():
        return int(raw)
    return 0


def _clean_url(url: str, base: str) -> str:
    text = str(url or "").strip().replace("\\/", "/")
    if not text or text.startswith(("data:", "blob:", "javascript:")):
        return ""
    if text.startswith("//"):
        text = "https:" + text
    if text.startswith("http"):
        return text
    return urljoin((base or "http://localhost").rstrip("/") + "/", text)


def _quality_key(label: str) -> str:
    match = re.search(r"(\d{3,4})", str(label or ""))
    return match.group(1) if match else ""


# 站点 API 路径：mediaDefinitions 里的 "mp4" 条目有时指向 /video/get_media
# 这类 JSON 接口（返回 []，不是媒体文件），ffmpeg 拿到会报
# "Stream map '0:v:0' matches no streams"。必须按路径/后缀过滤。
MEDIA_API_PATH_RE = re.compile(r"/(?:get_media|getmedia|api|json)(?:/|\?|$)", re.I)


def looks_like_media(url: str) -> bool:
    """判断 URL 是否像真正的媒体直链（.mp4 / .m3u8），排除 JSON API。"""
    text = str(url or "")
    if not text.startswith(("http://", "https://")):
        return False
    if MEDIA_API_PATH_RE.search(text):
        return False
    path = urlparse(text).path.lower()
    return ".mp4" in path or ".m3u8" in path or path.endswith((".ts", ".webm", ".mov"))


class PhScraper:
    """欧美影视站点适配器：博主订阅列表 → 视频卡片 → 详情 → mp4/hls。"""

    cdn_hosts = ("phncdn.com",)  # Pornhub 系图床/视频 CDN；其他域名运行时自动补充

    def __init__(self, db, settings) -> None:
        self.db = db
        self.settings = settings
        self.http = PhHttp(settings)
        self._seen_hosts: set[str] = set()
        self._creator_cache: dict[str, tuple[float, dict | None]] = {}
        self._creator_cache_lock = threading.Lock()

    # ------------------------------------------------------------------ 配置
    @property
    def site_base(self) -> str:
        base = self.settings.get("ph_base", "", group=GROUP_KEY).strip()
        return base.rstrip("/")

    def _require_base(self) -> str:
        base = self.site_base
        if not base:
            raise ScrapeError("欧美站点地址未配置：请到「欧美影视 → 设置」填写站点根地址")
        return base

    @property
    def check_url(self) -> str:
        return self.site_base or "about:blank"

    def allowed_hosts(self) -> set[str]:
        """HLS 播放代理白名单：站点域名 + 已见过的媒体域名。"""
        hosts: set[str] = set(self.cdn_hosts)
        base_host = (urlparse(self.site_base).hostname or "").lower()
        if base_host:
            hosts.add(base_host)
        hosts.update(h.lower() for h in self._seen_hosts if h)
        return hosts

    # ------------------------------------------------------------------ ids
    @staticmethod
    def _sid(viewkey: str) -> str:
        return viewkey if viewkey.startswith(SITE_PREFIX) else SITE_PREFIX + viewkey

    @staticmethod
    def raw_id(site_id: str) -> str:
        text = str(site_id or "")
        return text[len(SITE_PREFIX):] if text.startswith(SITE_PREFIX) else text

    def video_url(self, site_id: str) -> str:
        raw = self.raw_id(site_id)
        row = self.db.get_drama_by_site_id(str(site_id))
        stored = str(row["url"]) if row else ""
        match = VIEWKEY_RE.search(stored)
        if match and stored.startswith("http"):
            return stored
        return f"{self._require_base()}/view_video.php?viewkey={raw}"

    # ------------------------------------------------------------------ http
    def _get_text(self, url: str) -> str:
        self.http.base = self.site_base
        return self.http.get_text(url, headers=self._page_headers())

    def _get_bytes(self, url: str, referer: str = "") -> tuple[bytes, str]:
        self.http.base = self.site_base
        headers = {"Referer": (referer or self.site_base).rstrip("/") + "/",
                   "Accept": "image/*,*/*"}
        return self.http.get_bytes(url, headers=headers)

    def _page_headers(self) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    @staticmethod
    def _blocked(html: str) -> bool:
        head = html[:60000]
        return "Just a moment" in head or "cf-browser-verification" in head

    # --------------------------------------------------------------- creators
    def creator_from_url(self, url: str) -> dict:
        """把用户粘贴的博主页 URL 规范化为 {key, page_url}。"""
        text = str(url or "").strip()
        if not text:
            raise ScrapeError("博主主页地址不能为空")
        if not text.startswith(("http://", "https://")):
            base = self._require_base()
            text = urljoin(base + "/", text.lstrip("/"))
        parsed = urlparse(text)
        if not parsed.netloc:
            raise ScrapeError(f"无法解析博主地址: {url}")
        path = parsed.path or "/"
        # 去掉 /videos、/videolist 等列表后缀，保留博主路径本体
        core = re.sub(r"/(videos|videolist|video|uploads)(?:/[a-z\-]*)?/?$", "", path)
        if core in ("", "/"):
            raise ScrapeError("博主地址缺少路径：请粘贴 model / channels / users 页面链接")
        key = core.strip("/").lower()
        page_url = f"{parsed.scheme}://{parsed.netloc}{core.rstrip('/')}/videos"
        return {"key": key, "page_url": page_url, "origin": f"{parsed.scheme}://{parsed.netloc}"}

    def parse_creator_info(self, html: str) -> dict:
        """解析博主页：名称 + 头像（#getAvatar）。"""
        soup = BeautifulSoup(html, "lxml")
        name = ""
        for sel in ("h1", ".username h1", ".model-name", "[itemprop='name']"):
            node = soup.select_one(sel)
            if node:
                text = node.get_text(" ", strip=True)
                text = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", text).strip()
                if text:
                    name = text
                    break
        if not name:
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            match = re.match(r"([^|·\-–—]{2,60}?)(?:\s*(?:的)?\s*(?:videos|影片|主页))?\s*(?:[-|·]\s*.+)?$", title, re.I)
            if match:
                name = match.group(1).strip()
        if not name:
            node = soup.select_one("a[href*='/model/'], a[href*='/channels/'], a[href*='/users/']")
            if node:
                name = node.get_text(" ", strip=True)

        avatar = ""
        node = soup.select_one("#getAvatar") or soup.select_one(".getAvatar")
        if node is None:
            node = soup.select_one("[class*='avatar']")
        if node is not None:
            avatar = self._image_url_of(node)
        if not avatar:
            img = soup.select_one("img.getAvatar, .getAvatar img, img[class*='avatar']")
            if img is not None:
                avatar = self._image_url_of(img)
        if not avatar:
            og = soup.select_one("meta[property='og:image'], meta[name='twitter:image']")
            avatar = (og.get("content") or "") if og else ""
        return {"name": name.strip(), "avatar_url": avatar}

    def _image_url_of(self, node) -> str:
        """从节点（或其内部 img）取图片地址：src / data-src / style 背景。"""
        img = node if node.name == "img" else node.find("img")
        for target in (img, node):
            if target is None:
                continue
            for attr in ("data-thumb_url", "data-src", "data-original", "src"):
                value = target.get(attr)
                if value and not value.startswith(("data:", "blob:")):
                    return value
        for target in (img, node):
            if target is None:
                continue
            style = target.get("style") or ""
            match = re.search(r"url\(['\"]?([^'\")]+)", style)
            if match:
                return match.group(1)
        return ""

    # ----------------------------------------------------------------- listing
    def list_tabs(self, module: str) -> list[tuple[str, str]]:
        """演员订阅就是模块的列表：每个启用的博主一个 tab。"""
        subs = self.db.list_creator_subs(source=SOURCE_KEY, enabled_only=True)
        tabs: list[tuple[str, str]] = []
        for sub in subs:
            label = sub.get("name") or sub.get("creator_key") or "博主"
            tabs.append((f"sub:{int(sub['id'])}", str(label)))
        return tabs

    def creator_for_tab(self, tab: str) -> dict | None:
        if not tab.startswith("sub:"):
            return None
        try:
            creator_id = int(tab.split(":", 1)[1])
        except ValueError:
            return None
        now = time.time()
        with self._creator_cache_lock:
            cached = self._creator_cache.get(tab)
            if cached and cached[0] > now:
                return cached[1]
        sub = self.db.get_creator_sub(creator_id)
        with self._creator_cache_lock:
            self._creator_cache[tab] = (now + 5.0, sub)
        return sub

    def list_url(self, tab: str, page: int = 1) -> str:
        sub = self.creator_for_tab(tab)
        if not sub:
            raise ScrapeError(f"演员订阅不存在或已删除: {tab}")
        base_url = str(sub.get("page_url") or "")
        if not base_url:
            raise ScrapeError(f"演员订阅缺少主页地址: {tab}")
        if int(page) <= 1:
            return base_url
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}page={int(page)}"

    def _page_cap(self, sub: dict | None) -> int:
        caps = []
        if sub and int(sub.get("max_pages") or 0) > 0:
            caps.append(int(sub["max_pages"]))
        setting_cap = self.settings.get_int("ph_max_pages", 0, group=GROUP_KEY)
        if setting_cap > 0:
            caps.append(setting_cap)
        return min(caps) if caps else 0

    def page_count(self, module: str, tab: str = "latest") -> int:
        sub = self.creator_for_tab(tab)
        html = self._get_text(self.list_url(tab, 1))
        pages = self.parse_pages(html)
        cap = self._page_cap(sub)
        if cap > 0:
            pages = min(pages, cap)
        return max(1, pages)

    def fetch_listing_page(self, module: str, page: int, tab: str = "latest"):
        url = self.list_url(tab, page)
        html = self._get_text(url)
        if self._blocked(html):
            raise ScrapeError(f"博主页可能被 Cloudflare 拦截（第 {page} 页）")
        cards = self.parse_listing(html, module, page_url=url)
        sub = self.creator_for_tab(tab)
        if sub:
            creator_key = str(sub.get("creator_key") or "")
            creator_name = str(sub.get("name") or "")
            for card in cards:
                if creator_key:
                    # 列表阶段就绑定博主，详情解析失败也能归属
                    card["creator_key"] = creator_key
                if creator_name and not card.get("author"):
                    card["author"] = creator_name
        return cards, self.parse_pages(html)

    @staticmethod
    def parse_pages(html: str) -> int:
        soup = BeautifulSoup(html, "lxml")
        pages = 1
        # 1) 标准翻页容器
        for a in soup.select(
            ".pagination a[href], .pageList a[href], li.page_number a[href], "
            ".pageNav a[href], [class*='pagination'] a[href]"
        ):
            href = a.get("href") or ""
            match = PAGE_PARAM_RE.search(href) or re.search(r"/(\d{1,4})/?$", href)
            if match:
                pages = max(pages, int(match.group(1)))
        if pages == 1:
            # 2) 文本是数字且带 page 参数的链接（博主页常见）
            for a in soup.find_all("a", href=True):
                match = PAGE_PARAM_RE.search(a["href"])
                text = a.get_text(strip=True)
                if match and text.isdigit():
                    pages = max(pages, int(match.group(1)))
        return pages

    # 主视频列表容器：优先站点标准 id/class，找不到时用启发式定位。
    # 博主页底部常带「推荐 / 精选」区（其他博主的视频），必须排除，否则会多出
    # 不属于该博主的影片。
    MAIN_LIST_SELECTORS = (
        "#mostRecentVideosSection",
        "ul.mostRecentVideoSection",
        "#videosSection",
        ".videoUList",
        "ul.pcVideosList",
        ".sectionVideos",
    )
    PAGER_SELECTORS = (".pagination", ".pageList", "li.page_number", "[class*='pagination']")

    @staticmethod
    def _viewkey_count(node) -> int:
        return len({m.group(1) for a in node.find_all("a", href=True)
                    if (m := VIEWKEY_RE.search(a.get("href", "")))})

    def _scope_main_list(self, soup):
        """定位主视频列表节点：标准 id → 翻页前的最大列表 → 文档顺序第一个列表。"""
        for sel in self.MAIN_LIST_SELECTORS:
            node = soup.select_one(sel)
            if node is not None and node.find("a", href=VIEWKEY_RE):
                return node
        lists = [ul for ul in soup.find_all("ul") if ul.find("a", href=VIEWKEY_RE)]
        if not lists:
            return soup
        pager = None
        for sel in self.PAGER_SELECTORS:
            pager = soup.select_one(sel)
            if pager is not None:
                break
        if pager is not None:
            pager_line = getattr(pager, "sourceline", 0) or 0
            before = [ul for ul in lists
                      if (getattr(ul, "sourceline", 0) or 0) and ul.sourceline < pager_line]
            if before:
                # 翻页属于主列表：取翻页之前视频最多的那个 ul
                return max(before, key=lambda ul: (self._viewkey_count(ul), -ul.sourceline))
        # 没有翻页：主列表在页面顶部（推荐位在底部），取文档顺序第一个
        return lists[0]

    def parse_listing(self, html: str, module: str, page_url: str = "") -> list[dict]:
        """解析博主页视频卡：viewkey 去重，封面 / 标题 / 时长 / 播放量。

        只解析主视频列表（翻页的那个列表），排除底部推荐位其他博主的视频。
        相对链接优先按博主页实际域名补全（支持镜像站）。
        """
        base = (page_url or self.site_base or "").rstrip("/") or ""
        soup = BeautifulSoup(html, "lxml")
        scope = self._scope_main_list(soup)
        cards: list[dict] = []
        seen: set[str] = set()
        for a in scope.find_all("a", href=True):
            match = VIEWKEY_RE.search(a.get("href", ""))
            if not match:
                continue
            viewkey = match.group(1)
            if viewkey in seen:
                continue
            node = a.find_parent("li") or a.find_parent("article") or a.parent
            card = node if node is not None and node.name in ("li", "article", "div") else a
            img = a.find("img") or (card.find("img") if card else None)
            cover = self._image_url_of(img) if img is not None else ""
            cover = _clean_url(cover, base)
            title = self._card_title(card, a)
            duration_text = ""
            for sel in (".duration", "var.duration", ".videoDuration", ".length"):
                el = card.select_one(sel) if card else None
                if el is None and node is not None:
                    el = node.select_one(sel)
                if el is not None:
                    duration_text = el.get_text(" ", strip=True)
                    if duration_text:
                        break
            views = ""
            for sel in (".views", "var.views", ".videoViews", ".count"):
                el = card.select_one(sel) if card else None
                if el is not None:
                    views = el.get_text(" ", strip=True)
                    if views:
                        break
            badges = []
            for el in (card.select(".badges span, .ribbon, [class*='hd']") if card else []):
                text = el.get_text(" ", strip=True)
                if text and len(text) <= 8 and text.upper() not in badges:
                    badges.append(text.upper())
            seen.add(viewkey)
            cards.append(
                {
                    "site_id": self._sid(viewkey),
                    "module": module,
                    "title": title.strip(),
                    "url": _clean_url(a["href"], base),
                    "cover_url": cover,
                    "score": None,
                    "total_ep": 1,
                    "tags": [],
                    "views": views,
                    "badges": badges,
                    "duration_text": duration_text,
                    "duration": duration_to_seconds(duration_text),
                    "source": SOURCE_KEY,
                }
            )
        return cards

    def _card_title(self, card, thumb_link) -> str:
        if card is not None:
            for sel in (".title a", ".title", ".videoTitle", "span.title a", "a.title"):
                el = card.select_one(sel)
                if el is not None:
                    text = el.get_text(" ", strip=True)
                    if text:
                        return text
            # 用户站点结构：li/div/div[2]/div[2]/span/a 是标题
            el = card.select_one("span a[href]")
            if el is not None:
                text = el.get_text(" ", strip=True)
                if text and len(text) > 2:
                    return text
            # 兜底：卡片里文本最长的链接
            best = ""
            for link in card.find_all("a"):
                text = link.get_text(" ", strip=True)
                if len(text) > len(best):
                    best = text
            if best:
                return best
        img = thumb_link.find("img")
        if img is not None:
            for attr in ("alt", "title"):
                text = img.get(attr)
                if text:
                    return text
        return str(thumb_link.get("title") or thumb_link.get("aria-label") or "")

    # ------------------------------------------------------------------ detail
    def fetch_detail(self, site_id: str, url: str = "") -> dict:
        """抓取视频详情页。``url`` 可指定实际地址（单视频添加时用用户粘贴的链接）。"""
        target = str(url or "").strip() or self.video_url(site_id)
        html = self._get_text(target)
        if self._blocked(html):
            raise ScrapeError(f"视频页可能被 Cloudflare 拦截: {site_id}")
        return self.parse_detail(html, str(site_id))

    def parse_detail(self, html: str, site_id: str) -> dict:
        soup = BeautifulSoup(html, "lxml")
        base = self.site_base

        title = self._detail_title(soup)
        cover = ""
        for sel in ("meta[property='og:image']", "meta[itemprop='thumbnailUrl']",
                    "meta[name='twitter:image']", "link[rel='image_src']"):
            node = soup.select_one(sel)
            if node is not None:
                cover = node.get("content") or node.get("href") or ""
                if cover:
                    break
        cover = _clean_url(cover, base)

        tags = self._detail_tags(soup, html)
        views = self._detail_views(soup)
        score = self._detail_score(soup)
        duration, duration_text = self._detail_duration(soup, html, self.raw_id(site_id))
        premiered, year = self._detail_date(soup, html)

        creator = self._detail_creator(soup)
        creator_key = creator["key"]
        creator_name = creator["name"]
        avatar_url = creator["avatar_url"]
        avatar_local = ""
        # 演员订阅归属优先：列表卡片写入的博主是权威归属。视频页解析出来的
        # 上传者只做补充——转发/剪辑页的演职链接很容易抓错，两者不一致时
        # 一律按订阅博主归属。
        row = self.db.get_drama_by_site_id(str(site_id)) if site_id else None
        row_key = str(row["creator_key"] or "").strip() if row is not None else ""
        if row_key and creator_key != row_key:
            creator_key = row_key
            creator_name = ""
            avatar_url = ""
        if creator_key:
            sub = self.db.get_creator_sub_by_key(SOURCE_KEY, creator_key)
            if sub:
                if not creator_name:
                    creator_name = str(sub.get("name") or "")
                if not avatar_url:
                    local = str(sub.get("avatar_path") or "")
                    remote = str(sub.get("avatar_url") or "")
                    if local and Path(local).is_file():
                        avatar_local = local
                    elif remote:
                        avatar_url = remote

        episodes = [
            {
                "ep": 1,
                "site_video_id": self.raw_id(site_id),
                "title": "正片",
                "synopsis": "",
                "duration": duration,
                "cover_url": cover,
            }
        ]
        actors = []
        if creator_name:
            entry = {"name": creator_name, "role": "演员"}
            if avatar_local:
                entry["thumb"] = avatar_local
            else:
                entry["thumb_url"] = avatar_url or ""
            actors.append(entry)
        return {
            "site_id": str(site_id),
            "title": title,
            "description": "",  # 简介用标题兜底（Emby NFO plot 自动回退）
            "cover_url": cover,
            "score": score,
            "total_ep": 1,
            "author": creator_name,
            "status_text": "已完成",
            "views": views,
            "premiered": premiered,
            "duration": duration,
            "duration_text": duration_text,
            "category_text": ",".join(tags[:8]),
            "tags": tags,
            "year": year,
            "creator_key": creator_key,
            "episodes": episodes,
            "actors": actors,
        }

    def _detail_title(self, soup: BeautifulSoup) -> str:
        node = soup.select_one("h1.title, h1[itemprop='name'], h1")
        if node is not None:
            # 去掉站点塞进 h1 的小标签（免费/会员等）
            text = node.get_text(" ", strip=True)
            text = re.sub(r"\s*[-|·]\s*(?:Pornhub|pornhub\.com)\s*$", "", text)
            if text:
                return text.strip()
        og = soup.select_one("meta[property='og:title']")
        if og is not None and og.get("content"):
            text = re.sub(r"\s*[-|·]\s*(?:Pornhub|pornhub\.com)\s*$", "", og["content"])
            return text.strip()
        if soup.title is not None:
            text = soup.title.get_text(" ", strip=True)
            return re.sub(r"\s*[-|·]\s*(?:Pornhub|pornhub\.com)\s*$", "", text).strip()
        return ""

    def _detail_tags(self, soup: BeautifulSoup, html: str) -> list[str]:
        tags: list[str] = []
        for sel in TAG_SELECTORS:
            for a in soup.select(sel):
                text = a.get_text(" ", strip=True)
                if text and 1 < len(text) <= 32 and text not in tags:
                    tags.append(text)
            if len(tags) >= 3:
                return tags
        # 用户提供的标签块绝对 XPath 兜底
        if not tags:
            try:
                from lxml import html as lxml_html

                doc = lxml_html.fromstring(html)
                for a in doc.xpath(TAG_XPATH):
                    text = re.sub(r"\s+", " ", (a.text_content() or "")).strip()
                    if text and 1 < len(text) <= 32 and text not in tags:
                        tags.append(text)
            except Exception:
                pass
        if not tags:
            # 最后兜底：页面里成组出现的 search 链接（标签通常成组）
            candidates: list[str] = []
            for a in soup.select("a[href*='search=']"):
                text = a.get_text(" ", strip=True)
                if text and 1 < len(text) <= 24:
                    candidates.append(text)
            for text in candidates:
                if text not in tags and candidates.count(text) >= 1:
                    tags.append(text)
                if len(tags) >= 12:
                    break
        return tags

    def _detail_views(self, soup: BeautifulSoup) -> str:
        for sel in (".views .count", ".views", "var.views", "[itemprop='interactionCount']"):
            node = soup.select_one(sel)
            if node is not None:
                text = node.get("content") or node.get_text(" ", strip=True)
                text = re.sub(r"[^\d.,KMW万亿]", "", str(text), flags=re.I)
                if text:
                    return text
        return ""

    def _detail_score(self, soup: BeautifulSoup) -> float | None:
        for sel in ("#votePercentText", ".votePercentText", ".rating-percent", ".percent"):
            node = soup.select_one(sel)
            if node is not None:
                match = re.search(r"(\d{1,3})", node.get_text(" ", strip=True))
                if match:
                    value = min(100.0, float(match.group(1))) / 10.0
                    return round(value, 1)
        return None

    def _detail_duration(self, soup: BeautifulSoup, html: str, viewkey: str = ""):
        """视频真实时长：优先本视频 flashvars JSON 的 duration 字段。

        ⚠️ 页面里 ``class="duration"`` 与裸 ``"duration":`` 有几十个，
        全是推荐位/相关视频的时长——抓第一个会把 9 分钟的视频标成
        38 分钟，进而让完整下载被时长校验反复拒绝。
        """
        # 1) 本视频的 flashvars（含 mediaDefinitions，link 带 viewkey）
        for match in re.finditer(r"flashvars[_\w]*\s*=\s*\{", html):
            blob = self._balanced_json(html, match.end() - 1)
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            has_media = bool(data.get("mediaDefinitions") or data.get("media_definitions"))
            link = str(data.get("link") or "")
            if not has_media and (not viewkey or viewkey not in link):
                continue
            raw = data.get("duration") or data.get("video_duration")
            try:
                seconds = int(float(raw))
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0:
                return seconds, f"{seconds // 60}:{seconds % 60:02d}"
        # 2) 兜底：meta itemprop（ISO 8601，语义明确）
        node = soup.select_one("meta[itemprop='duration']")
        if node is not None and node.get("content"):
            seconds = duration_to_seconds(str(node["content"]))
            if seconds > 0:
                return seconds, str(node["content"])
        # 不再使用 .duration 选择器 / 裸 duration 正则（全是推荐位数据）
        return 0, ""

    def _detail_date(self, soup: BeautifulSoup, html: str):
        for sel in ("meta[itemprop='uploadDate']", "meta[property='og:video:release_date']",
                    "meta[property='video:release_date']", "time[datetime]"):
            node = soup.select_one(sel)
            if node is not None:
                text = str(node.get("content") or node.get("datetime") or "")
                match = DATE_TEXT_RE.search(text)
                if match:
                    return match.group(1), int(match.group(1)[:4])
        match = DATE_TEXT_RE.search(html)
        if match:
            year = int(match.group(1)[:4])
            if 1990 < year < 2100:
                return match.group(1), year
        return "", None

    # 视频页「上传者信息块」：演员/归属只能从这里取。
    # 页面里大量 /model/ /pornstar/ 链接属于演职人员、推荐位，拿第一个匹配
    # 会把别人的名字当成演员（798ds 的转发页就是这样错的）。
    UPLOADER_SCOPES = (
        ".userInfo",
        "[class*='userInfo']",
        ".username",
        "[class*='username']",
        ".userAvatar",
        "[class*='userAvatar']",
        "[class*='uploader']",
        "[class*='videoChannel']",
    )

    def _detail_creator(self, soup: BeautifulSoup) -> dict:
        """从视频页解析上传者（博主）：名称 + 主页 key + 头像。

        只在上传者信息块里找链接，找不到就返回空（由订阅归属兜底），
        绝不退回全页扫描。
        """
        for scope_sel in self.UPLOADER_SCOPES:
            for scope in soup.select(scope_sel):
                link = None
                for a in scope.find_all("a", href=True):
                    if self._creator_key_of(a.get("href", "")):
                        link = a
                        break
                if link is None:
                    continue
                key = self._creator_key_of(link.get("href", ""))
                name = link.get_text(" ", strip=True)
                if not name:
                    username = link.find(class_="username") if hasattr(link, "find") else None
                    name = username.get_text(" ", strip=True) if username else ""
                if not name:
                    name = key.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
                # 头像：上传者块的父级里找 img
                avatar = ""
                parent = scope.find_parent(["div", "section", "li"]) or scope
                img = None
                for sel in ("img[class*='avatar']", "img[src*='avatar']", "img"):
                    img = parent.select_one(sel)
                    if img is not None:
                        break
                if img is not None:
                    avatar = self._image_url_of(img)
                return {"key": key, "name": name.strip(), "avatar_url": avatar}
        return {"key": "", "name": "", "avatar_url": ""}

    @staticmethod
    def _creator_key_of(href: str) -> str:
        text = _clean_url(href, "http://x")
        if not text.startswith("http"):
            return ""
        path = urlparse(text).path or ""
        match = CREATOR_PATH_RE.match(path)
        if match:
            kind = match.group(1).lower()
            name = match.group(2)
            return f"{kind}/{name}".lower()
        return ""

    # ----------------------------------------------------------------- playback
    def get_playback(self, site_id: str, ep: int) -> dict:
        url = self.video_url(site_id)
        html = self._get_text(url)
        media = self.extract_media(html)
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if self.site_base:
            headers["Referer"] = self.site_base.rstrip("/") + "/"
        proxy = self.settings.get("proxy").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        # 页面会在不同 CDN 之间轮换，部分 token 已失效（HTTP 410/403）。
        # HLS 流做深度探测：master → 媒体播放列表 → 首尾分片全部可用才通过；
        # 找到第一个可用流即停（画质表其余候选不强探测，交给
        # download_stream 的 token 刷新兜底），避免启动时连发十几个探测请求。
        hls_list = list(media.get("hls_list") or [])
        working_hls: list[tuple[str, str]] = []
        for quality, candidate in hls_list:
            if self._probe_hls_candidate(candidate, headers, proxies):
                working_hls.append((quality, candidate))
                break
        if not working_hls and hls_list:
            # 探测全部失败（可能只是网络抖动）：退回页面第一个候选
            working_hls = [hls_list[0]]
        main = working_hls[0][1] if working_hls else ""

        variants: dict[str, str] = {}
        for key, candidate in (media.get("variants") or {}).items():
            if self._probe_media(candidate, headers, proxies):
                variants[key] = candidate
        best_mp4 = self._best_variant(variants)
        if not main:
            main = best_mp4
        if not main:
            raise ScrapeError(f"没有找到可用的视频流: {site_id}")

        # HLS 按画质进画质表：已探测可用的优先，未探测的候选也放进去
        # （download_stream 选用失败时会刷新 token 兜底）；mp4 直链优先覆盖
        hls_variants: dict[str, str] = {}
        for quality, candidate in hls_list:
            key = quality or self._guess_quality(candidate) or "1080"
            hls_variants.setdefault(key, candidate)
        for quality, candidate in working_hls:
            key = quality or self._guess_quality(candidate) or "1080"
            hls_variants[key] = candidate
        all_variants = dict(hls_variants)
        for key, candidate in variants.items():
            all_variants[key] = candidate

        streams: dict[str, str] = {"main": main, "hevc": ""}
        if best_mp4:
            streams["mp4"] = best_mp4
        hosts = [u for _, u in working_hls] + list(variants.values())
        self._remember_hosts(hosts)
        cdn: dict[str, str] = {}
        if self.site_base:
            cdn["Referer"] = self.site_base.rstrip("/") + "/"
        return {
            "id": str(site_id),
            "streams": streams,
            "variants": all_variants,
            "hls": main,
            "cdn": cdn,
        }

    def _probe_media(self, url: str, headers: dict, proxies: dict | None = None,
                     timeout: float = 6.0) -> bool:
        """GET 探测候选流是否真正可用（HTTP 200 且内容像媒体）。"""
        try:
            response = requests.get(url, headers=headers, proxies=proxies,
                                    stream=True, timeout=timeout)
        except requests.RequestException:
            return False
        with response:
            if response.status_code != 200:
                return False
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "mpegurl" in ctype or "video/" in ctype or "mp4" in ctype:
                return True
            if ctype in ("", "text/plain", "application/octet-stream"):
                try:
                    chunk = next(response.iter_content(16), b"")
                except Exception:
                    return False
                return chunk.startswith(b"#EXTM3U") or chunk[4:8] == b"ftyp"
            return False

    def _probe_hls_candidate(self, master_url: str, headers: dict,
                             proxies: dict | None = None) -> bool:
        """深度探测 HLS：master → 媒体播放列表 → 首尾分片，全部可用才通过。

        只探 master 会漏掉「分片 token 已失效」的死流：那种流 ffmpeg
        下到一半开始跳分片，最后正常退出但文件是截断的。
        """
        try:
            resp = requests.get(master_url, headers=headers, proxies=proxies,
                                timeout=6)
            if resp.status_code != 200:
                return False
            body = resp.text or ""
            if "#EXTM3U" not in body:
                return False
            media_lines = [l.strip() for l in body.splitlines()
                           if l.strip() and not l.strip().startswith("#")]
            if not media_lines:
                return False
            media_url = urljoin(master_url, media_lines[0])
            resp2 = requests.get(media_url, headers=headers, proxies=proxies,
                                 timeout=6)
            if resp2.status_code != 200:
                return False
            body2 = resp2.text or ""
            if "#EXTM3U" not in body2:
                return False
            segments = [l.strip() for l in body2.splitlines()
                        if l.strip() and not l.strip().startswith("#")]
            if not segments:
                return False
            for seg_rel in (segments[0], segments[-1]):
                seg_url = urljoin(media_url, seg_rel)
                if not self._probe_media(seg_url, headers, proxies):
                    return False
            return True
        except requests.RequestException:
            return False

    @staticmethod
    def _guess_quality(url: str) -> str:
        match = re.search(r"(\d{3,4})[pP][_\-]", str(url or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _best_variant(variants: dict[str, str]) -> str:
        if not variants:
            return ""
        keys = sorted(variants, key=lambda k: int(k) if str(k).isdigit() else 0, reverse=True)
        return variants[keys[0]]

    @staticmethod
    def stream_url(payload: dict, quality: str) -> str:
        """画质选择：720p 优先 720 直链；hevc/h264 取最高画质直链。"""
        variants = payload.get("variants") or {}
        streams = payload.get("streams") or {}
        if not variants:
            return streams.get("mp4") or streams.get("main") or ""
        keys = sorted(variants, key=lambda k: int(k) if str(k).isdigit() else 0, reverse=True)
        if quality == "720p":
            for key in keys:
                if str(key).isdigit() and int(key) <= 720:
                    return variants[key]
        return variants[keys[0]]

    @staticmethod
    def cdn_headers(payload: dict) -> dict[str, str]:
        """ffmpeg / 播放代理请求媒体时带的头；Referer 由 get_playback 写入 payload。"""
        cdn = payload.get("cdn") or {}
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        referer = str(cdn.get("Referer") or cdn.get("referer") or "").rstrip("/")
        if referer:
            headers["Referer"] = referer + "/"
        auth_key = cdn.get("authKey")
        auth_value = cdn.get("authValue")
        if auth_key and auth_value:
            headers[str(auth_key)] = str(auth_value)
        return headers

    def _remember_hosts(self, urls: list[str]) -> None:
        for url in urls or []:
            host = (urlparse(str(url or "")).hostname or "").lower()
            if not host:
                continue
            self._seen_hosts.add(host)
            labels = host.split(".")
            if len(labels) >= 2:
                self._seen_hosts.add(".".join(labels[-2:]))

    # ------------------------------------------------------------------- media
    def extract_media(self, html: str) -> dict:
        """从视频区域提取下载链接：flashvars / mediaDefinitions / 直链正则。

        返回 ``{"variants": {画质: mp4直链}, "hls": 第一条hls,
        "hls_list": [(画质, hls地址), ...]}``。HLS 每个画质一条
        （1080/720/480/240…），交给 get_playback 逐条探测后按画质选用。
        """
        variants: dict[str, str] = {}
        hls_list: list[tuple[str, str]] = []
        # 1) flashvars_{id} = {...};（Pornhub 系标准写法）
        for match in re.finditer(r"flashvars[_\w]*\s*=\s*\{", html):
            blob = self._balanced_json(html, match.end() - 1)
            if not blob:
                continue
            try:
                data = json.loads(blob)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            found = self._collect_definitions(data)
            variants.update(found["variants"])
            seen = {u for _, u in hls_list}
            for quality, candidate in found["hls_list"]:
                if candidate not in seen:
                    hls_list.append((quality, candidate))
                    seen.add(candidate)
            if variants or hls_list:
                break
        # 2) playerQualityLevels / qualityItems 数组（{text, file} 结构）
        if not variants and not hls_list:
            for match in re.finditer(r"(?:playerQualityLevels|qualityItems)[_\w]*\s*=\s*\[", html):
                blob = self._balanced_json(html, match.end() - 1)
                if not blob:
                    continue
                try:
                    items = json.loads(blob)
                except Exception:
                    continue
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("file") or item.get("url") or "")
                    label = str(item.get("text") or item.get("label") or item.get("quality") or "")
                    if not url:
                        continue
                    url = url.replace("\\/", "/")
                    if ".m3u8" in url and looks_like_media(url):
                        hls_list.append((_quality_key(label), url))
                    elif looks_like_media(url):
                        key = _quality_key(label) or str(100 + len(variants))
                        variants.setdefault(key, url)
                if variants or hls_list:
                    break
        # 3) 直接扫页面里的 mp4/m3u8 直链
        if not variants and not hls_list:
            for url in M3U8_URL_RE.findall(html):
                hls_list.append(("", url.replace("\\/", "/")))
            for url in MP4_URL_RE.findall(html):
                clean = url.replace("\\/", "/")
                match = re.search(r"(\d{3,4})p?\.mp4", clean)
                key = match.group(1) if match else str(100 + len(variants))
                variants.setdefault(key, clean)
        # 过滤广告/占位/JSON API 链接
        variants = {
            key: url for key, url in variants.items()
            if looks_like_media(url) and "google" not in url and "ads" not in url
        }
        hls_list = [
            (quality, url) for quality, url in hls_list
            if looks_like_media(url) and "google" not in url and "ads" not in url
        ]
        # 画质降序（无画质标签的排最后）
        hls_list.sort(key=lambda pair: int(pair[0]) if str(pair[0]).isdigit() else 0, reverse=True)
        hls = hls_list[0][1] if hls_list else ""
        return {"variants": variants, "hls": hls, "hls_list": hls_list}

    @staticmethod
    def _collect_definitions(data: dict) -> dict:
        variants: dict[str, str] = {}
        hls_list: list[tuple[str, str]] = []
        definitions = []
        for key in ("mediaDefinitions", "media_definitions", "qualityLevels", "sources"):
            value = data.get(key)
            if isinstance(value, list):
                definitions = value
                break
        for item in definitions:
            if not isinstance(item, dict):
                continue
            url = str(item.get("videoUrl") or item.get("url") or item.get("file") or "")
            if not url:
                continue
            url = url.replace("\\/", "/")
            fmt = str(item.get("format") or item.get("type") or "").lower()
            label = str(item.get("quality") or item.get("label") or item.get("text") or "")
            if not looks_like_media(url):
                # get_media 之类 JSON 接口不是媒体文件，跳过
                continue
            if fmt == "hls" or ".m3u8" in url:
                hls_list.append((_quality_key(label), url))
            else:
                key = _quality_key(label) or str(100 + len(variants))
                variants.setdefault(key, url)
        return {"variants": variants, "hls_list": hls_list}

    @staticmethod
    def _balanced_json(text: str, start: int) -> str:
        """从 start（指向 ``{`` 或 ``[``）截取配平的 JSON 片段。"""
        depth = 0
        in_str = False
        escape = False
        end = min(len(text), start + 2_000_000)
        for index in range(start, end):
            ch = text[index]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in "{[":
                    depth += 1
                elif ch in "}]":
                    depth -= 1
                    if depth == 0:
                        return text[start:index + 1]
        return ""

    # -------------------------------------------------------- segment download
    # 并行下载线程数：4 路并发分片，速度约为串行的 4 倍；过高容易触发 CDN 限流
    SEGMENT_WORKERS = 4

    def download_stream(self, payload: dict, site_id: str, ep: int, quality: str,
                        workdir: Path, progress=None, should_cancel=None) -> dict:
        """分段下载 HLS（并行 + 断点续传 + token 失效自动刷新）。

        站点 CDN 的分片 token 极不稳定，ffmpeg 直连远程 HLS 会在 token
        失效时静默跳分片产出截断文件。这里自己并行分片下载：

        - 每个分片写到 ``workdir/seg_NNNN.ts``（先写 .part 再改名，保证
          落盘的分片一定是完整的）；
        - 4 线程并行下载（``SEGMENT_WORKERS``）；
        - 重试时已存在的分片直接跳过 → **流量零浪费**；
        - 一批分片失败时重新 ``get_playback`` 拿新 token 刷新播放列表，
          只重试失败的分片，已完成的保留；
        - 全部就绪后生成 ``local.m3u8``，调用方用 ffmpeg 本地合并。

        返回 ``{"playlist": 路径, "segments": 总数, "duration": 总秒数,
        "bytes": 已下载字节}``；所选流不是 HLS 时返回
        ``{"playlist": "", "direct_url": 直链}`` 由调用方走直连。
        """
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if self.site_base:
            headers["Referer"] = self.site_base.rstrip("/") + "/"
        proxy = self.settings.get("proxy").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None

        url = self.stream_url(payload, quality)
        if ".m3u8" not in url:
            return {"playlist": "", "direct_url": url}

        segments = self._load_segments(url, headers, proxies)
        refreshes = 0
        while segments is None:
            refreshes += 1
            if refreshes > 6:
                raise ScrapeError("播放列表反复拉取失败，稍后重试")
            time.sleep(1.5)
            payload = self.get_playback(site_id, ep)
            url = self.stream_url(payload, quality)
            if ".m3u8" not in url:
                return {"playlist": "", "direct_url": url}
            segments = self._load_segments(url, headers, proxies)

        total = len(segments)
        if total == 0:
            raise ScrapeError("播放列表没有分片")

        # 统计已有缓存（断点续传：已完成的分片直接跳过）
        def cached_indices() -> list[int]:
            done = []
            for position in range(total):
                target = workdir / f"seg_{position:04d}.ts"
                if target.exists() and target.stat().st_size > 0:
                    done.append(position)
            return done

        cached = cached_indices()
        done_files = len(cached)
        bytes_done = sum((workdir / f"seg_{i:04d}.ts").stat().st_size for i in cached)
        if callable(progress) and done_files:
            progress(done_files, total, bytes_done)

        state = {"done": done_files, "bytes": bytes_done}

        def report(index: int) -> None:
            state["done"] += 1
            state["bytes"] += (workdir / f"seg_{index:04d}.ts").stat().st_size
            if callable(progress):
                progress(state["done"], total, state["bytes"])

        def run_wave(indices: list[int]) -> list[int]:
            """并行下载一波分片，返回失败的索引列表。"""
            failed: list[int] = []
            with ThreadPoolExecutor(max_workers=self.SEGMENT_WORKERS) as pool:
                futures = {}
                for position in indices:
                    if callable(should_cancel) and should_cancel():
                        break
                    target = workdir / f"seg_{position:04d}.ts"
                    futures[pool.submit(
                        self._fetch_segment, segments[position]["url"], target,
                        headers, proxies,
                    )] = position
                for future in as_completed(futures):
                    position = futures[future]
                    try:
                        ok = future.result()
                    except Exception:
                        ok = False
                    if ok:
                        report(position)
                    else:
                        failed.append(position)
            return failed

        missing = [i for i in range(total) if i not in set(cached)]
        failure_rounds = 0
        while missing:
            if callable(should_cancel) and should_cancel():
                raise ScrapeError("任务已取消")
            failed = run_wave(missing)
            if not failed:
                break
            # 有分片失败：大概率 token 失效，刷新播放列表（新 token）后只重试失败的
            failure_rounds += 1
            refreshes += 1
            if failure_rounds > 10 or refreshes > 12:
                raise ScrapeError(
                    f"分片下载反复失败（已刷新 token {refreshes} 次）；"
                    f"已完成 {state['done']}/{total} 片，下次重试将从断点继续"
                )
            time.sleep(1.0)
            payload = self.get_playback(site_id, ep)
            url = self.stream_url(payload, quality)
            if ".m3u8" in url:
                fresh = self._load_segments(url, headers, proxies)
                if fresh is not None and len(fresh) == total:
                    segments = fresh
            missing = failed

        playlist = workdir / "local.m3u8"
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-TARGETDURATION:10",
            "#EXT-X-PLAYLIST-TYPE:VOD",
        ]
        total_duration = 0.0
        for position, seg in enumerate(segments):
            duration = float(seg.get("duration") or 0)
            total_duration += duration
            lines.append(f"#EXTINF:{duration:.3f},")
            lines.append(f"seg_{position:04d}.ts")
        lines.append("#EXT-X-ENDLIST")
        playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "playlist": str(playlist),
            "segments": total,
            "duration": total_duration,
            "bytes": bytes_done,
        }

    def _load_segments(self, master_url: str, headers: dict, proxies: dict | None):
        """master → 媒体播放列表 → 分片列表；失败返回 None。"""
        try:
            resp = requests.get(master_url, headers=headers, proxies=proxies, timeout=10)
            if resp.status_code != 200 or "#EXTM3U" not in (resp.text or ""):
                return None
            media_lines = [l.strip() for l in resp.text.splitlines()
                           if l.strip() and not l.strip().startswith("#")]
            if not media_lines:
                return None
            media_url = urljoin(master_url, media_lines[0])
            resp2 = requests.get(media_url, headers=headers, proxies=proxies, timeout=10)
            if resp2.status_code != 200 or "#EXTM3U" not in (resp2.text or ""):
                return None
            segments: list[dict] = []
            current_duration = 0.0
            for line in resp2.text.splitlines():
                line = line.strip()
                if line.startswith("#EXTINF:"):
                    try:
                        current_duration = float(line.split(":", 1)[1].split(",")[0])
                    except ValueError:
                        current_duration = 0.0
                elif line and not line.startswith("#"):
                    segments.append(
                        {"url": urljoin(media_url, line), "duration": current_duration}
                    )
                    current_duration = 0.0
            return segments or None
        except requests.RequestException:
            return None

    def _fetch_segment(self, url: str, target: Path, headers: dict,
                       proxies: dict | None) -> bool:
        """下载单个分片；先写 .part 再原子改名，落盘即完整。"""
        part = target.parent / (target.name + ".part")
        try:
            response = requests.get(url, headers=headers, proxies=proxies,
                                    stream=True, timeout=(10, 60))
            if response.status_code != 200:
                response.close()
                return False
            with open(part, "wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)
            if part.stat().st_size <= 0:
                part.unlink(missing_ok=True)
                return False
            part.replace(target)
            return True
        except (requests.RequestException, OSError):
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    # ------------------------------------------------------------------- cover
    def save_cover(self, site_id: str, cover_url: str) -> str:
        if not cover_url:
            return ""
        if cover_url.startswith(("http://", "https://", "//")):
            candidates = [cover_url]
            if "?" in cover_url:
                candidates.append(cover_url.split("?", 1)[0])
            content = b""
            for candidate in candidates:
                try:
                    content, _ = self._get_bytes(candidate, referer=self.site_base)
                    if content:
                        break
                except ScrapeError:
                    continue
        else:
            try:
                content, _ = self._get_bytes(cover_url, referer=self.site_base)
            except ScrapeError:
                content = b""
        if not content:
            return ""
        try:
            image = Image.open(io.BytesIO(content))
            image.load()
        except Exception:
            return ""
        target = COVERS_DIR / f"{site_id}.jpg"
        try:
            image = image.convert("RGB")
            image.save(target, "JPEG", quality=86, optimize=True)
        except Exception:
            return ""
        self.db.execute(
            "UPDATE dramas SET cover_path=? WHERE site_id=?", (str(target), str(site_id))
        )
        return str(target)
