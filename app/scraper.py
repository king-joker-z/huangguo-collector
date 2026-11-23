"""黄果短剧站点采集器：列表、详情、播放地址、封面。"""
from __future__ import annotations

import io
import json
import re
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import COVERS_DIR, MODULES, SITE_BASE, USER_AGENT
from .settings import Settings

# 图床公开解码协议（AES-128-CBC）。运行时优先从站点脚本读取，失败时用此回退。
FALLBACK_IMAGE_KEY = b"f5d965df75336270"
FALLBACK_IMAGE_IV = b"97b60394abc2fbe1"


class ScrapeError(RuntimeError):
    pass


class HttpClient:
    """线程内复用 requests.Session，带重试、代理和 Cookie 支持。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.local = threading.local()

    def _session(self) -> requests.Session:
        if not hasattr(self.local, "session"):
            session = requests.Session()
            retry = Retry(
                total=3,
                connect=3,
                read=3,
                backoff_factor=0.7,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "POST"}),
            )
            adapter = HTTPAdapter(max_retries=retry, pool_connections=24, pool_maxsize=24)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
                    "Referer": SITE_BASE + "/",
                }
            )
            self.local.session = session
        session = self.local.session
        proxy = self.settings.get("proxy").strip()
        session.proxies = {"http": proxy, "https": proxy} if proxy else {}
        cookie = self.settings.get("cookie").strip()
        if cookie:
            session.headers["Cookie"] = cookie
        else:
            session.headers.pop("Cookie", None)
        return session

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        delay = self.settings.get_float("request_delay", 0.15)
        if delay > 0:
            time.sleep(delay)
        timeout = kwargs.pop("timeout", 30)
        try:
            response = self._session().request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            raise ScrapeError(f"请求失败: {exc}") from exc
        if response.status_code >= 400:
            raise ScrapeError(f"HTTP {response.status_code}: {url}")
        return response

    def get_text(self, url: str, **kwargs) -> str:
        response = self.request("GET", url, **kwargs)
        if not response.encoding or response.encoding.lower() in ("iso-8859-1", "ascii"):
            response.encoding = "utf-8"
        return response.text

    def get_bytes(self, url: str, **kwargs) -> tuple[bytes, str]:
        response = self.request("GET", url, **kwargs)
        return response.content, response.headers.get("Content-Type", "")


class Scraper:
    def __init__(self, db, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.http = HttpClient(settings)
        self._image_crypto: tuple[bytes, bytes] | None = None
        self._image_crypto_lock = threading.Lock()

    # ------------------------------------------------------------- listing
    def module_url(self, module: str, page: int = 1, tab: str = "latest") -> str:
        base = MODULES[module]["base"].rstrip("/")
        if tab and tab != "latest":
            base += f"/{tab}"
        if page <= 1:
            return urljoin(SITE_BASE, base + "/")
        return urljoin(SITE_BASE, f"{base}/{page}/")

    def page_count(self, module: str, tab: str = "latest") -> int:
        html = self.http.get_text(self.module_url(module, 1, tab))
        _, pages = self.parse_listing(html, module, tab)
        return pages

    def fetch_listing_page(self, module: str, page: int, tab: str = "latest") -> tuple[list[dict], int]:
        html = self.http.get_text(self.module_url(module, page, tab))
        return self.parse_listing(html, module, tab)

    def parse_listing(self, html: str, module: str, panel: str | None = None) -> tuple[list[dict], int]:
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(".hg-card-grid.is-active .hg-drama-card")
        if not cards:
            cards = soup.select("section.hg-list-page .hg-drama-card")
        items: list[dict] = []
        for card in cards:
            parsed = self._parse_card(card, module)
            if parsed:
                items.append(parsed)
        pages = 1
        pager = None
        if panel:
            pager = soup.select_one(f'.hg-channel-pager[data-channel-panel="{panel}"]')
        if pager is None:
            pager = soup.select_one('.hg-channel-pager[data-channel-panel="latest"]')
        if pager is None:
            pager = soup.select_one(".hg-channel-pager")
        jumps = pager.select(".hg-pager__jump[data-pages]") if pager else []
        for jump in jumps:
            try:
                pages = max(pages, int(jump.get("data-pages") or 1))
            except (TypeError, ValueError):
                pass
        if pages == 1 and pager:
            for link in pager.select(".hg-pager a[href]"):
                match = re.search(r"/(\d+)/?$", link.get("href", ""))
                if match:
                    pages = max(pages, int(match.group(1)))
        return items, pages

    def _parse_card(self, card, module: str) -> dict | None:
        link = card.select_one("a.hg-drama-card__cover-link") or card.select_one("a[href*='/video/']")
        if not link:
            return None
        href = link.get("href", "")
        match = re.search(r"/video/(\d+)", href)
        if not match:
            return None
        site_id = match.group(1)
        title_el = card.select_one(".hg-drama-card__title")
        img = card.select_one("img")
        title = (title_el.get_text(" ", strip=True) if title_el else "") or (img.get("alt") if img else "")
        cover = ""
        if img:
            cover = img.get("data-src") or img.get("src") or ""
            if "/static/web/" in cover or cover.startswith("data:"):
                cover = img.get("data-src") or ""
        if cover.startswith("//"):
            cover = "https:" + cover
        score = None
        score_el = card.select_one(".hg-drama-card__score")
        if score_el:
            sm = re.search(r"([\d.]+)", score_el.get_text(" ", strip=True))
            if sm:
                score = float(sm.group(1))
        total_ep = 0
        ep_el = card.select_one(".hg-drama-card__episode")
        if ep_el:
            em = re.search(r"(\d+)", ep_el.get_text(" ", strip=True))
            if em:
                total_ep = int(em.group(1))
        tags = [a.get_text(" ", strip=True) for a in card.select(".hg-drama-card__tags .hg-tag")]
        views_el = card.select_one(".hg-drama-card__views")
        views = views_el.get_text(" ", strip=True) if views_el else ""
        views = re.sub(r"\s+", "", views)
        badges = [b.get_text(" ", strip=True) for b in card.select(".hg-drama-card__badge")]
        return {
            "site_id": site_id,
            "module": module,
            "title": title.strip(),
            "url": urljoin(SITE_BASE, f"/video/{site_id}/"),
            "cover_url": cover.strip(),
            "score": score,
            "total_ep": total_ep,
            "tags": tags,
            "views": views,
            "badges": badges,
        }

    # -------------------------------------------------------------- detail
    def fetch_detail(self, site_id: str) -> dict:
        url = f"{SITE_BASE}/video/{site_id}/"
        html = self.http.get_text(url, headers={"Referer": SITE_BASE + "/"})
        soup = BeautifulSoup(html, "lxml")
        data_el = soup.find("script", id="videoInitialData")
        if not data_el:
            raise ScrapeError(f"详情页缺少 videoInitialData: {site_id}")
        try:
            payload = json.loads(data_el.string or data_el.get_text() or "{}")
        except json.JSONDecodeError as exc:
            raise ScrapeError(f"详情 JSON 解析失败: {site_id}") from exc

        h1 = soup.select_one("h1")
        title = (h1.get_text(" ", strip=True) if h1 else "") or payload.get("title") or ""

        ep_links = soup.select("aside.hg-web-play__side a.hg-web-play__ep")
        max_range = 0
        for button in soup.select("button[data-hg-ep-range]"):
            try:
                max_range = max(max_range, int(button.get("data-end") or 0))
            except (TypeError, ValueError):
                pass
        tracking = payload.get("trackingEpisodes") or {}
        if not isinstance(tracking, dict):
            tracking = {}
        total_ep = max(
            len(ep_links),
            max_range,
            len(tracking),
            int(payload.get("ep") or 0),
        )

        synopses = payload.get("episodeSynopses") or {}
        if not isinstance(synopses, dict):
            synopses = {}
        episodes: list[dict] = []
        for ep in range(1, total_ep + 1):
            info = tracking.get(str(ep)) or {}
            episodes.append(
                {
                    "ep": ep,
                    "site_video_id": str(info.get("video_id") or ""),
                    "title": f"第{ep}集",
                    "synopsis": (synopses.get(str(ep)) or "").strip(),
                    "duration": int(info.get("drama_duration") or 0),
                }
            )

        intro = soup.select_one(".hg-web-play__introcard")
        intro_text = intro.get_text("\n", strip=True) if intro else ""
        premiered = ""
        match = re.search(r"上线日期\s*(\d{4}-\d{2}-\d{2})", intro_text)
        if match:
            premiered = match.group(1)
        elif payload.get("time"):
            premiered = str(payload["time"])[:10]

        views = ""
        match = re.search(r"播放次数\s*([\d.]+\s*[wW万]?)", intro_text)
        if match:
            views = re.sub(r"\s+", "", match.group(1))
        elif payload.get("views"):
            views = str(payload["views"])

        duration_text = ""
        match = re.search(r"单集片长\s*(\d+:\d+)", intro_text)
        if match:
            duration_text = match.group(1)

        category_text = ""
        match = re.search(r"题材类型\s*(.+?)(?:\n推荐专题|\n|$)", intro_text, re.S)
        if match:
            category_text = re.sub(r"\s+", " ", match.group(1)).strip()[:120]

        score = None
        scorebox = soup.select_one(".hg-web-play__scorebox")
        if scorebox:
            match = re.search(r"([\d.]+)\s*分", scorebox.get_text(" ", strip=True))
            if match:
                score = float(match.group(1))
        if score is None:
            meta = soup.select_one("meta[name='description']")
            if meta:
                match = re.search(r"评分\s*([\d.]+)", meta.get("content", ""))
                if match:
                    score = float(match.group(1))

        description = (payload.get("description") or "").strip()
        if not description:
            syn = soup.select_one(".hg-web-play__synopsis")
            if syn:
                description = syn.get_text("\n", strip=True)
                description = re.sub(r"^剧情简介\s*(展开)?\s*", "", description).strip()

        tags = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]
        if not tags:
            tags = [a.get_text(" ", strip=True) for a in soup.select(".hg-web-play__introcard a[href*='/tag/']")]

        cover = (payload.get("coverSrc") or payload.get("posterSrc") or "").strip()
        if not cover:
            og = soup.select_one("meta[property='og:image']")
            if og:
                cover = og.get("content", "")
        if cover.startswith("//"):
            cover = "https:" + cover

        status_text = ""
        match = re.search(r"更新状态\s*([^\n]+)", intro_text)
        if match:
            status_text = match.group(1).strip()
        if not status_text and total_ep:
            status_text = f"更新至{total_ep}集"

        year = None
        if premiered:
            try:
                year = int(premiered[:4])
            except ValueError:
                year = None

        return {
            "site_id": site_id,
            "title": title.strip(),
            "description": description,
            "cover_url": cover,
            "score": score,
            "total_ep": total_ep,
            "author": str(payload.get("author") or "").strip(),
            "status_text": status_text,
            "views": views,
            "premiered": premiered,
            "duration_text": duration_text,
            "category_text": category_text,
            "tags": tags,
            "year": year,
            "episodes": episodes,
        }

    # --------------------------------------------------------------- cover
    def _get_image_crypto(self) -> tuple[bytes, bytes]:
        with self._image_crypto_lock:
            if self._image_crypto:
                return self._image_crypto
            key, iv = FALLBACK_IMAGE_KEY, FALLBACK_IMAGE_IV
            try:
                text = self.http.get_text(
                    f"{SITE_BASE}/static/web/js/plugins/crypto-image-protocol.js"
                )
                match = re.search(r"CryptoImageProtocol\s*=\s*Object\.freeze\((\{.*?\})\)", text, re.S)
                if match:
                    data = json.loads(match.group(1))
                    candidate_key = str(data.get("decryptKey") or "").encode()
                    candidate_iv = str(data.get("decryptIV") or "").encode()
                    if len(candidate_key) in (16, 24, 32) and len(candidate_iv) == 16:
                        key, iv = candidate_key, candidate_iv
            except Exception:
                pass
            self._image_crypto = (key, iv)
            return self._image_crypto

    def _decode_image(self, content: bytes):
        try:
            image = Image.open(io.BytesIO(content))
            image.load()
            return image
        except Exception:
            pass
        if not content or len(content) % 16:
            return None
        try:
            key, iv = self._get_image_crypto()
            decrypted = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor().update(content)
            padding = decrypted[-1] if decrypted else 0
            if 0 < padding <= 16 and decrypted.endswith(bytes([padding]) * padding):
                decrypted = decrypted[:-padding]
            image = Image.open(io.BytesIO(decrypted))
            image.load()
            return image
        except Exception:
            return None

    def save_cover(self, site_id: str, cover_url: str) -> str:
        if not cover_url:
            return ""
        candidates = [cover_url]
        if "?" in cover_url:
            candidates.append(cover_url.split("?", 1)[0])
        content = b""
        for candidate in candidates:
            try:
                content, _ = self.http.get_bytes(
                    candidate, headers={"Referer": SITE_BASE + "/", "Accept": "image/*,*/*"}
                )
                if content:
                    break
            except ScrapeError:
                continue
        image = self._decode_image(content) if content else None
        if image is None:
            return ""
        target = COVERS_DIR / f"{site_id}.jpg"
        try:
            image = image.convert("RGB")
            image.thumbnail((600, 900), Image.LANCZOS)
            image.save(target, "JPEG", quality=82, optimize=True)
        except Exception:
            return ""
        self.set_cover_by_site_id(site_id, str(target))
        return str(target)

    def set_cover_by_site_id(self, site_id: str, path: str) -> None:
        self.db.execute("UPDATE dramas SET cover_path=? WHERE site_id=?", (path, str(site_id)))

    # ------------------------------------------------------------ playback
    def get_playback(self, site_id: str, ep: int) -> dict:
        url = f"{SITE_BASE}/api/videos/{site_id}/episodes/{ep}/playback"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{SITE_BASE}/video/{site_id}/",
            "Origin": SITE_BASE,
        }
        response = self.http.request("POST", url, data="{}", headers=headers)
        try:
            body = response.json()
        except ValueError as exc:
            raise ScrapeError(f"播放接口返回非 JSON: {site_id} ep{ep}") from exc
        if body.get("status") != 1 or not isinstance(body.get("data"), dict):
            raise ScrapeError(f"播放接口失败: {body.get('msg') or body.get('status')}")
        data = body["data"]
        streams = data.get("streams") or {}
        if not streams.get("main"):
            raise ScrapeError(f"播放地址为空: {site_id} ep{ep}")
        if str(data.get("id") or "") != str(site_id):
            raise ScrapeError(f"播放身份不匹配: {site_id} ep{ep}")
        return data

    @staticmethod
    def stream_url(payload: dict, quality: str) -> str:
        streams = payload.get("streams") or {}
        if quality == "hevc" and streams.get("hevc"):
            return streams["hevc"]
        return streams.get("main") or ""

    @staticmethod
    def cdn_headers(payload: dict) -> dict[str, str]:
        cdn = payload.get("cdn") or {}
        headers = {"Referer": SITE_BASE + "/", "User-Agent": USER_AGENT}
        if cdn.get("authKey") and cdn.get("authValue"):
            headers[cdn["authKey"]] = cdn["authValue"]
        if cdn.get("app"):
            headers["x-cdn-app"] = str(cdn["app"])
        return headers
