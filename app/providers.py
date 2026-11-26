"""站点 Provider 注册表。

Scraper 和 YeguoScraper 都实现同一组方法；运行时只依赖 source -> provider，
新增站点时只需要在这里注册并补一个 MODULES 项，不再改 scan/download/hls。
"""
from __future__ import annotations

from typing import Protocol

from .config import MODULES, SOURCES
from .db import Database
from .settings import Settings


class SiteProvider(Protocol):
    site_base: str
    check_url: str

    def page_count(self, module: str, tab: str = "latest") -> int: ...
    def fetch_listing_page(self, module: str, page: int, tab: str = "latest") -> tuple[list[dict], int]: ...
    def fetch_detail(self, site_id: str) -> dict: ...
    def get_playback(self, site_id: str, ep: int) -> dict: ...
    def save_cover(self, site_id: str, cover_url: str) -> str: ...


def build_providers(db: Database, settings: Settings) -> dict[str, SiteProvider]:
    from .scraper import Scraper
    from .yeguo import YeguoScraper

    return {
        "huangguo": Scraper(db, settings),
        "yeguo": YeguoScraper(db, settings),
    }


def provider_summary(providers: dict) -> list[dict]:
    out = []
    for key, source in SOURCES.items():
        provider = providers.get(key)
        modules = [m for m, meta in MODULES.items() if meta.get("source", "huangguo") == key]
        out.append(
            {
                "key": key,
                "label": source["label"],
                "base": source["base"],
                "available": provider is not None,
                "modules": modules,
            }
        )
    return out
