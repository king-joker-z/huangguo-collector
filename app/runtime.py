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

db = Database()
settings = Settings(db)
scraper = Scraper(db, settings)
downloads = DownloadManager(db, settings, scraper)
scan = ScanManager(db, settings, scraper, downloads)
scheduler = Scheduler(db, settings, scan)
covers = CoverBackfillManager(db, settings)
hls = HlsProxy(db, settings, scraper)
