"""站内 HLS 观看代理：把加密签名的 m3u8/分片改写到本地，避免 CORS 与防盗链。"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from urllib.parse import quote, urljoin, urlparse

import requests
from fastapi.responses import Response, StreamingResponse

from .config import SITE_BASE, USER_AGENT
from .db import Database
from .scraper import Scraper, ScrapeError
from .settings import Settings

ALLOWED_SUFFIXES = (".qnlycc.cn", ".tjyyjk.cn", ".huangguoai.com")
URI_RE = re.compile(r'URI="([^"]+)"')


class HlsProxy:
    def __init__(self, db: Database, settings: Settings, scraper: Scraper) -> None:
        self.db = db
        self.settings = settings
        self.scraper = scraper
        self.secret = secrets.token_bytes(24)
        self.cache: dict[tuple[str, int], tuple[float, str, dict]] = {}
        # 每个分片 token 对应自己的 CDN 鉴权头，避免并发播放时串用其他集的 auth。
        self.stream_headers: dict[str, tuple[float, dict[str, str]]] = {}
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ signing
    def sign(self, url: str) -> str:
        expires = int(time.time()) + 7200
        digest = hmac.new(self.secret, f"{url}|{expires}".encode(), hashlib.sha256).hexdigest()[:24]
        return f"{expires}.{digest}"

    def verify(self, url: str, token: str) -> bool:
        try:
            expires_text, digest = token.split(".", 1)
            expires = int(expires_text)
        except (ValueError, AttributeError):
            return False
        if expires < int(time.time()):
            return False
        expected = hmac.new(self.secret, f"{url}|{expires}".encode(), hashlib.sha256).hexdigest()[:24]
        return hmac.compare_digest(expected, digest)

    def proxy_url(self, absolute: str, headers: dict[str, str] | None = None) -> str:
        token = self.sign(absolute)
        if headers:
            with self.lock:
                now = time.time()
                self.stream_headers[token] = (now + 7200, dict(headers))
                if len(self.stream_headers) > 4096:
                    self.stream_headers = {
                        key: value for key, value in self.stream_headers.items() if value[0] > now
                    }
        return f"/api/stream?u={quote(absolute, safe='')}&t={token}"

    # -------------------------------------------------------------------- build
    def manifest(self, drama_id: int, ep: int) -> str:
        drama = self.db.get_drama(drama_id)
        if not drama:
            raise ScrapeError("剧集不存在")
        site_id = str(drama["site_id"])
        key = (site_id, int(ep))
        with self.lock:
            cached = self.cache.get(key)
            if cached and cached[0] > time.time():
                return cached[1]

        payload = self.scraper.get_playback(site_id, int(ep))
        url = (payload.get("streams") or {}).get("main") or ""
        if not url:
            raise ScrapeError("没有视频流")
        headers = Scraper.cdn_headers(payload)
        text = self.scraper.http.get_text(url, headers=headers)

        base = url
        out_lines: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                out_lines.append("")
                continue
            if line.startswith("#"):
                if "URI=" in line:
                    def replace(match: re.Match) -> str:
                        absolute = urljoin(base, match.group(1))
                        return f'URI="{self.proxy_url(absolute, headers)}"'

                    line = URI_RE.sub(replace, line)
                out_lines.append(line)
                continue
            absolute = urljoin(base, line)
            out_lines.append(self.proxy_url(absolute, headers))

        manifest = "\n".join(out_lines) + "\n"
        with self.lock:
            self.cache[key] = (time.time() + 45, manifest, headers)
        return manifest

    def manifest_response(self, drama_id: int, ep: int) -> Response:
        text = self.manifest(drama_id, ep)
        return Response(
            content=text,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"},
        )

    # ------------------------------------------------------------------- stream
    def _headers_for(self, url: str) -> dict[str, str]:
        return {"User-Agent": USER_AGENT, "Referer": SITE_BASE + "/", "Accept": "*/*"}

    @staticmethod
    def _allowed(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "huangguoai.com" or host.endswith(ALLOWED_SUFFIXES)

    def stream(self, url: str, token: str, range_header: str = "") -> StreamingResponse:
        if not self.verify(url, token) or not self._allowed(url):
            return Response(status_code=403, content="forbidden")
        with self.lock:
            entry = self.stream_headers.get(token)
        headers = dict(entry[1]) if entry and entry[0] > time.time() else self._headers_for(url)
        if range_header:
            headers["Range"] = range_header
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=60)
        except requests.RequestException as exc:
            return Response(status_code=502, content=str(exc))
        if response.status_code >= 400:
            response.close()
            return Response(status_code=response.status_code, content="upstream error")

        def generate():
            try:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                response.close()

        response_headers = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}
        content_length = response.headers.get("Content-Length")
        if content_length:
            response_headers["Content-Length"] = content_length
        return StreamingResponse(
            generate(),
            status_code=response.status_code,
            media_type=response.headers.get("Content-Type", "application/octet-stream"),
            headers=response_headers,
        )
