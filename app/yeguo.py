"""野果短剧（yeguodj.com）采集器。

站点是 Nuxt SSR + Cloudflare，普通 requests 会被 TLS 指纹拦截；这里用
curl_cffi 模拟 Chrome。播放接口返回 AES-CBC 加密 JSON，解密后包含整部剧的
全部集、m3u8 地址和每集封面，不需要逐集打开浏览器。
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import threading
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from curl_cffi import requests as curl_requests

from .config import USER_AGENT, YEGUO_BASE
from .scraper import FALLBACK_IMAGE_IV, FALLBACK_IMAGE_KEY, ScrapeError, Scraper

PLAY_URL = f"{YEGUO_BASE}/api.php/api/playlet/play"
LIST_URL = f"{YEGUO_BASE}/explore/drama/"
DETAIL_RE = re.compile(r"/drama/detail/(\d+)")
PAGE_RE = re.compile(r"/explore/drama/page/(\d+)/")
TOTAL_RE = re.compile(r"共\s*(\d+)\s*条")
PLAY_KEY = b"2acf7e91e9864673"
PLAY_IV = b"1c29882d3ddfcfd6"


class YeguoHttp:
    """curl_cffi 会话，按线程隔离，带重试和代理/Cookie。"""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.local = threading.local()

    def _session(self):
        if not hasattr(self.local, "session"):
            session = curl_requests.Session(impersonate="chrome")
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
                    "Referer": YEGUO_BASE + "/",
                    "Origin": YEGUO_BASE,
                }
            )
            self.local.session = session
        session = self.local.session
        cookie = self.settings.get("cookie").strip()
        if cookie:
            session.headers["Cookie"] = cookie
        else:
            session.headers.pop("Cookie", None)
        return session

    def request(self, method: str, url: str, **kwargs):
        delay = self.settings.get_float("request_delay", 0.15)
        if delay > 0:
            time.sleep(delay)
        timeout = kwargs.pop("timeout", 30)
        retries = max(1, int(kwargs.pop("retries", 3)))
        proxy = self.settings.get("proxy").strip()
        if proxy:
            kwargs.setdefault("proxies", {"http": proxy, "https": proxy})
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                response = self._session().request(
                    method, url, timeout=timeout, allow_redirects=True, **kwargs
                )
                if response.status_code >= 500 and attempt < retries - 1:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                if response.status_code >= 400:
                    raise ScrapeError(f"HTTP {response.status_code}: {url}")
                return response
            except ScrapeError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < retries - 1:
                    time.sleep(0.8 * (attempt + 1))
        raise ScrapeError(f"请求失败: {last_error}") from last_error

    def get_text(self, url: str, **kwargs) -> str:
        response = self.request("GET", url, **kwargs)
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "ascii"):
            response.encoding = "utf-8"
        return response.text

    def get_bytes(self, url: str, **kwargs) -> tuple[bytes, str]:
        response = self.request("GET", url, **kwargs)
        return response.content, response.headers.get("Content-Type", "")


def _unpad(data: bytes) -> bytes:
    if not data:
        return data
    pad = data[-1]
    if 0 < pad <= 16 and data.endswith(bytes([pad]) * pad):
        return data[:-pad]
    return data


def decrypt_play_payload(data: str) -> dict:
    try:
        ciphertext = base64.b64decode(str(data).replace(" ", "+"))
        decryptor = Cipher(algorithms.AES(PLAY_KEY), modes.CBC(PLAY_IV)).decryptor()
        plain = _unpad(decryptor.update(ciphertext) + decryptor.finalize())
        return json.loads(plain.decode("utf-8"))
    except Exception as exc:
        raise ScrapeError(f"野果播放数据解密失败: {exc}") from exc


class YeguoScraper(Scraper):
    def __init__(self, db, settings) -> None:
        super().__init__(db, settings)
        self.http = YeguoHttp(settings)
        self.site_base = YEGUO_BASE
        self.check_url = YEGUO_BASE + "/favicon.ico"
        self._image_crypto = (FALLBACK_IMAGE_KEY, FALLBACK_IMAGE_IV)
        self._play_cache: dict[str, tuple[float, dict]] = {}
        self._play_lock = threading.RLock()

    # ------------------------------------------------------------------ ids
    @staticmethod
    def raw_id(site_id: str) -> str:
        text = str(site_id or "")
        return text[len("yeguo_"):] if text.startswith("yeguo_") else text

    def module_url(self, module: str, page: int = 1, tab: str = "latest") -> str:
        if page <= 1:
            return LIST_URL
        return f"{YEGUO_BASE}/explore/drama/page/{int(page)}/"

    # --------------------------------------------------------------- listing
    @staticmethod
    def _parse_pages(soup: BeautifulSoup) -> int:
        text = soup.get_text(" ", strip=True)
        match = TOTAL_RE.search(text)
        if match:
            return max(1, (int(match.group(1)) + 19) // 20)
        pages = 1
        for anchor in soup.select('a[href*="/explore/drama/page/"]'):
            found = PAGE_RE.search(anchor.get("href", ""))
            if found:
                pages = max(pages, int(found.group(1)))
        return pages

    def parse_listing(self, html: str, module: str, panel: str | None = None) -> tuple[list[dict], int]:
        soup = BeautifulSoup(html, "lxml")
        articles = soup.select("article")
        if not articles:
            raise ScrapeError("野果列表为空，可能被 Cloudflare 拦截")
        pages = self._parse_pages(soup)
        items = []
        for article in articles:
            link = article.select_one('a[href*="/drama/detail/"]')
            if not link:
                continue
            found = DETAIL_RE.search(link.get("href", ""))
            if not found:
                continue
            raw = found.group(1)
            title_el = article.select_one("h3")
            img = article.select_one("img[data-src]") or article.select_one("img")
            title = (title_el.get_text(" ", strip=True) if title_el else "")
            if not title and img:
                title = img.get("alt") or ""
            cover = ""
            if img:
                cover = img.get("data-src") or img.get("src") or ""
                if cover.startswith("blob:") or cover.startswith("data:"):
                    cover = img.get("data-src") or ""
            if cover.startswith("//"):
                cover = "https:" + cover
            tags = []
            for tag in article.select('a[href^="/tag/"]'):
                name = tag.get_text(" ", strip=True)
                if name and name not in tags:
                    tags.append(name)
            span_texts = [s.get_text(" ", strip=True) for s in article.select("span")]
            status_text = "完结" if "完结" in span_texts else ""
            badges = [t for t in span_texts if t in ("完结", "热门", "新", "推荐")]
            views = ""
            for text in span_texts:
                if text.endswith("播放"):
                    views = text[:-2]
                    break
            items.append(
                {
                    "site_id": f"yeguo_{raw}",
                    "source": "yeguo",
                    "module": "yeguo",
                    "title": title.strip(),
                    "url": f"{YEGUO_BASE}/drama/detail/{raw}/",
                    "cover_url": cover.strip(),
                    "score": None,
                    "total_ep": 0,
                    "tags": tags,
                    "views": views,
                    "badges": badges,
                    "status_text": status_text,
                }
            )
        return items, pages

    def page_count(self, module: str, tab: str = "latest") -> int:
        html = self.http.get_text(self.module_url(module, 1, tab))
        _, pages = self.parse_listing(html, module, tab)
        return pages

    def fetch_listing_page(self, module: str, page: int, tab: str = "latest") -> tuple[list[dict], int]:
        html = self.http.get_text(self.module_url(module, page, tab))
        return self.parse_listing(html, module, tab)

    # ----------------------------------------------------------------- play
    def _play(self, site_id: str) -> dict:
        raw = self.raw_id(site_id)
        now = time.time()
        with self._play_lock:
            cached = self._play_cache.get(raw)
            if cached and cached[0] > now:
                return cached[1]
        oid = secrets.token_hex(16)
        form = {
            "bundleId": "com.pwa.mater",
            "version": "1.3.2",
            "oauth_type": "web",
            "language": "zh",
            "via": "pwa",
            "oauth_id": oid,
            "token": "",
            "trace_id": oid,
            "playlet_id": raw,
            "video_id": raw,
            "episode_id": "0",
        }
        try:
            response = self.http.request("POST", PLAY_URL, data=form, timeout=30)
            body = response.json()
        except ScrapeError:
            raise
        except Exception as exc:
            raise ScrapeError(f"野果播放接口失败: {exc}") from exc
        if int(body.get("errcode") or 0) != 0:
            raise ScrapeError(f"野果播放接口: {body.get('errmsg') or body.get('msg') or body}")
        payload = decrypt_play_payload(body.get("data") or "")
        with self._play_lock:
            if len(self._play_cache) >= 256:
                oldest = min(self._play_cache, key=lambda k: self._play_cache[k][0])
                self._play_cache.pop(oldest, None)
            self._play_cache[raw] = (now + 300, payload)
        return payload

    def fetch_detail(self, site_id: str) -> dict:
        raw = self.raw_id(site_id)
        payload = self._play(raw)
        data = payload.get("data") or {}
        cover = data.get("cover_img") or data.get("social_cover") or ""
        episode_items = data.get("episodeAll") or []
        episodes = []
        seen = set()
        for item in episode_items:
            if item.get("is_adv"):
                continue
            try:
                sort = int(item.get("sort") or item.get("index") or 0)
            except (TypeError, ValueError):
                continue
            if sort <= 0 or sort in seen:
                continue
            seen.add(sort)
            episodes.append(
                {
                    "ep": sort,
                    "site_video_id": str(item.get("id") or ""),
                    "title": item.get("episode_title") or item.get("title") or f"第{sort}集",
                    "synopsis": "",
                    "duration": int(item.get("duration") or item.get("episode_duration") or 0),
                    "cover_url": item.get("episode_cover") or cover,
                }
            )
        episodes.sort(key=lambda e: e["ep"])
        total = max(
            int(data.get("total_serial") or 0),
            len(episodes),
            max(seen) if seen else 0,
        )
        for ep in range(1, total + 1):
            if ep not in seen:
                episodes.append(
                    {
                        "ep": ep,
                        "site_video_id": "",
                        "title": f"第{ep}集",
                        "synopsis": "",
                        "duration": 0,
                        "cover_url": cover,
                    }
                )
        episodes.sort(key=lambda e: e["ep"])
        premiered = ""
        for item in episode_items:
            value = str(item.get("episode_published_at") or "")
            if value:
                premiered = value[:10]
                break
        if not premiered:
            value = str(data.get("created_at") or "")
            if value[:4].isdigit() and int(value[:4]) > 2000:
                premiered = value[:10]
        tags = [str(t) for t in (data.get("tags") or []) if str(t)]
        return {
            "title": data.get("video_title") or data.get("drama_name") or "",
            "description": data.get("description") or "",
            "cover_url": cover,
            "score": None,
            "total_ep": total,
            "author": "",
            "status_text": data.get("serialize_status_text") or data.get("update_status") or "",
            "views": "",
            "premiered": premiered,
            "duration_text": "",
            "category_text": ",".join(tags),
            "tags": tags,
            "year": int(premiered[:4]) if premiered[:4].isdigit() else None,
            "episodes": episodes,
        }

    def get_playback(self, site_id: str, ep: int) -> dict:
        raw = self.raw_id(site_id)
        data = (self._play(raw).get("data") or {})
        current = None
        for item in data.get("episodeAll") or []:
            if item.get("is_adv"):
                continue
            try:
                sort = int(item.get("sort") or item.get("index") or 0)
            except (TypeError, ValueError):
                continue
            if sort == int(ep):
                current = item
                break
        url = (current or {}).get("video_url") or (data.get("video_url") if int(ep) <= 1 else "")
        if not url:
            raise ScrapeError(f"野果第 {ep} 集没有播放地址")
        h265 = (current or {}).get("video_url_h265") or data.get("video_url_h265") or ""
        return {
            "streams": {"main": url, "hevc": h265},
            "cdn": {},
            "episode_cover": (current or {}).get("episode_cover") or data.get("episode_cover") or data.get("cover_img") or "",
        }

    @staticmethod
    def cdn_headers(payload: dict) -> dict[str, str]:
        return {
            "Referer": YEGUO_BASE + "/",
            "Origin": YEGUO_BASE,
            "User-Agent": USER_AGENT,
        }

