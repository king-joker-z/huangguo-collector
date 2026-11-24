"""进程内单例运行时。"""
from __future__ import annotations

from .covers import CoverBackfillManager
from .db import Database
from .downloader import DownloadManager
from .hlsproxy import HlsProxy
from .scan import ScanManager
from .scheduler import Scheduler
from .scraper import Scraper
from .settings import Settings
from .yeguo import YeguoScraper

db = Database()
settings = Settings(db)
scrapers = {
    "huangguo": Scraper(db, settings),
    "yeguo": YeguoScraper(db, settings),
}
scraper = scrapers["huangguo"]
downloads = DownloadManager(db, settings, scrapers)
scan = ScanManager(db, settings, scrapers, downloads)
scheduler = Scheduler(db, settings, scan)
covers = CoverBackfillManager(db, settings)
hls = HlsProxy(db, settings, scrapers)
