"""演员头像：把适配器返回的 thumb_url 缓存到本地。

适配器只需在 detail["actors"] 里给出：
    {"name": "姓名", "role": "演员", "thumb_url": "https://...jpg"}
上层会下载到 data/avatars/，NFO 生成时再复制进剧集目录的 actors/ 下。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import AVATARS_DIR


def _suffix(content: bytes, content_type: str) -> str:
    kind = (content_type or "").lower()
    if "png" in kind or content.startswith(b"\x89PNG"):
        return ".png"
    if "webp" in kind or (content[:4] == b"RIFF" and content[8:12] == b"WEBP"):
        return ".webp"
    return ".jpg"


def download_avatar(scraper, name: str, url: str) -> str:
    """下载头像到 data/avatars/，返回本地路径（失败返回空串）。"""
    headers = {
        "Referer": f"{getattr(scraper, 'site_base', '') or ''}/",
        "Accept": "image/*,*/*",
    }
    try:
        content, content_type = scraper.http.get_bytes(url, headers=headers)
    except Exception:
        return ""
    if not content or len(content) < 512:
        return ""
    digest = hashlib.sha1(f"{name}\x00{url}".encode("utf-8")).hexdigest()[:16]
    target = AVATARS_DIR / f"{digest}{_suffix(content, content_type)}"
    if not target.exists():
        try:
            target.write_bytes(content)
        except OSError:
            return ""
    return str(target)


_download = download_avatar


def ensure_avatars(scraper, detail: dict) -> list[dict]:
    """就地补全 detail["actors"]，返回规范化后的演员列表。"""
    actors: list[dict] = []
    for actor in detail.get("actors") or []:
        if not isinstance(actor, dict):
            continue
        name = str(actor.get("name") or "").strip()
        if not name:
            continue
        entry = {"name": name, "role": str(actor.get("role") or "").strip()}
        thumb = str(actor.get("thumb") or "").strip()
        if thumb and Path(thumb).is_file():
            entry["thumb"] = thumb
        else:
            url = str(actor.get("thumb_url") or "").strip()
            if url:
                entry["thumb"] = download_avatar(scraper, name, url)
        actors.append(entry)
    detail["actors"] = actors
    return actors
