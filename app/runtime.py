"""进程内单例运行时。"""
from __future__ import annotations

from .covers import CoverBackfillManager
from .db import Database
from .downloader import DownloadManager
from .hlsproxy import HlsProxy
from .providers import build_providers
from .scan import ScanManager
from .scheduler import Scheduler
from .settings import Settings

db = Database()
settings = Settings(db)
scrapers = build_providers(db, settings)
scraper = scrapers["huangguo"]
downloads = DownloadManager(db, settings, scrapers)
scan = ScanManager(db, settings, scrapers, downloads)
scheduler = Scheduler(db, settings, scan)
covers = CoverBackfillManager(db, settings)
hls = HlsProxy(db, settings, scrapers)
