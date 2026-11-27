"""演员订阅（博主 / 创作者页面）管理。

欧美影视分区的采集入口：用户粘贴博主页 URL，系统抓取博主名称与头像
（``#getAvatar``），存入 ``creator_subs`` 表；扫描时每个启用的订阅都是
``ph_creator`` 模块的一个列表，翻页全部采集，新影片自动入库。

订阅开启了 auto_download 时，扫描发现的新影片会自动加入下载队列
（scan.ScanManager._auto_download 联动）。
"""
from __future__ import annotations

from .actors import download_avatar
from .db import Database
from .scraper import ScrapeError
from .settings import Settings

SOURCE_KEY = "ph"
QUALITIES = ("", "hevc", "h264", "720p")


class CreatorManager:
    def __init__(self, db: Database, settings: Settings, scrapers: dict) -> None:
        self.db = db
        self.settings = settings
        self.scrapers = scrapers if isinstance(scrapers, dict) else {}

    # ------------------------------------------------------------------ helpers
    def _scraper(self):
        scraper = self.scrapers.get(SOURCE_KEY)
        if scraper is None:
            raise ScrapeError("欧美影视采集器未注册")
        if not scraper.site_base:
            raise ScrapeError("欧美站点地址未配置：请到「欧美影视 → 设置」填写站点根地址")
        return scraper

    def list(self, source: str = SOURCE_KEY) -> list[dict]:
        items = self.db.list_creator_subs(source=source)
        for item in items:
            item.update(self.db.creator_video_stats(source, str(item.get("creator_key") or "")))
            item["avatar"] = (
                f"/avatars/{str(item.get('avatar_path') or '').rsplit('/', 1)[-1]}"
                if item.get("avatar_path") else ""
            )
        return items

    # ---------------------------------------------------------------------- add
    def add(self, url: str, auto_download: bool = True, quality: str = "",
            max_pages: int = 0, enabled: bool = True) -> tuple[dict, str]:
        """添加演员订阅：抓博主页取名称/头像，探测可用的视频列表地址。"""
        scraper = self._scraper()
        info = scraper.creator_from_url(url)
        page_url = info["page_url"]
        profile_url = page_url.rsplit("/videos", 1)[0] or page_url

        name = ""
        avatar_url = ""
        warning = ""
        found_cards = 0
        for candidate in (page_url, profile_url):
            try:
                html = scraper._get_text(candidate)
            except ScrapeError:
                continue
            if scraper._blocked(html):
                raise ScrapeError("博主主页可能被 Cloudflare 拦截，请配置代理或稍后再试")
            if not name:
                parsed_info = scraper.parse_creator_info(html)
                name = parsed_info["name"]
                avatar_url = parsed_info["avatar_url"]
            cards = scraper.parse_listing(html, "ph_creator")
            if cards:
                found_cards = len(cards)
                page_url = candidate
                break
        if not found_cards:
            warning = "未在页面中找到影片列表，请确认地址是否为博主主页/视频列表页"

        avatar_path = ""
        if avatar_url:
            avatar_path = download_avatar(scraper, name or info["key"], avatar_url)
        if not name:
            name = info["key"].rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")

        creator_id = self.db.insert_creator_sub(
            source=SOURCE_KEY,
            creator_key=info["key"],
            name=name,
            page_url=page_url,
            avatar_url=avatar_url,
            avatar_path=avatar_path,
            auto_download=auto_download,
            quality=self._clean_quality(quality),
            max_pages=max(0, int(max_pages or 0)),
            enabled=enabled,
        )
        item = self.db.get_creator_sub(creator_id) or {}
        item.update(self.db.creator_video_stats(SOURCE_KEY, info["key"]))
        return item, warning

    # ------------------------------------------------------------------ update
    def update(self, creator_id: int, **fields) -> dict:
        item = self.db.get_creator_sub(creator_id)
        if not item:
            raise KeyError("演员订阅不存在")
        payload = {}
        for key in ("name",):
            if key in fields and fields[key] is not None:
                value = str(fields[key]).strip()
                if value:
                    payload[key] = value
        if "page_url" in fields and fields["page_url"]:
            scraper = self._scraper()
            info = scraper.creator_from_url(str(fields["page_url"]))
            payload["page_url"] = info["page_url"]
            payload["creator_key"] = info["key"]
        if "enabled" in fields and fields["enabled"] is not None:
            payload["enabled"] = 1 if fields["enabled"] else 0
        if "auto_download" in fields and fields["auto_download"] is not None:
            payload["auto_download"] = 1 if fields["auto_download"] else 0
        if "quality" in fields and fields["quality"] is not None:
            payload["quality"] = self._clean_quality(str(fields["quality"]))
        if "max_pages" in fields and fields["max_pages"] is not None:
            try:
                payload["max_pages"] = max(0, int(fields["max_pages"]))
            except (TypeError, ValueError):
                payload["max_pages"] = 0
        if payload:
            self.db.update_creator_sub(creator_id, **payload)
        return self.db.get_creator_sub(creator_id) or {}

    def delete(self, creator_id: int) -> None:
        if not self.db.get_creator_sub(creator_id):
            raise KeyError("演员订阅不存在")
        self.db.delete_creator_sub(creator_id)

    def refresh(self, creator_id: int) -> tuple[dict, str]:
        """重新抓取博主名称与头像（页面改版后使用）。"""
        item = self.db.get_creator_sub(creator_id)
        if not item:
            raise KeyError("演员订阅不存在")
        scraper = self._scraper()
        url = str(item.get("page_url") or "")
        html = scraper._get_text(url)
        if scraper._blocked(html):
            raise ScrapeError("博主主页可能被 Cloudflare 拦截")
        info = scraper.parse_creator_info(html)
        name = info["name"] or str(item.get("name") or "")
        avatar_url = info["avatar_url"] or str(item.get("avatar_url") or "")
        avatar_path = str(item.get("avatar_path") or "")
        if avatar_url:
            avatar_path = download_avatar(scraper, name, avatar_url) or avatar_path
        self.db.update_creator_sub(
            creator_id, name=name, avatar_url=avatar_url, avatar_path=avatar_path
        )
        fresh = self.db.get_creator_sub(creator_id) or {}
        fresh.update(self.db.creator_video_stats(SOURCE_KEY, str(fresh.get("creator_key") or "")))
        return fresh, "已刷新博主信息"

    # ------------------------------------------------------------------ quality
    @staticmethod
    def _clean_quality(quality: str) -> str:
        text = str(quality or "").strip()
        return text if text in QUALITIES else ""
