"""进程内单例运行时。"""
from __future__ import annotations

from .covers import CoverBackfillManager
from .creators import CreatorManager
from .db import Database
from .downloader import DownloadManager
from .hlsproxy import HlsProxy
from .providers import build_providers
from .scan import ScanManager
from .scheduler import Scheduler
from .settings import Settings
from .translate import TitleTranslateManager, Translator

db = Database()
settings = Settings(db)
scrapers = build_providers(db, settings)
scraper = scrapers["huangguo"]
translator = Translator(db, settings)
downloads = DownloadManager(db, settings, scrapers)
scan = ScanManager(db, settings, scrapers, downloads, translator)
scheduler = Scheduler(db, settings, scan)
covers = CoverBackfillManager(db, settings)
title_translate = TitleTranslateManager(db, settings, translator)
hls = HlsProxy(db, settings, scrapers)
creators = CreatorManager(db, settings, scrapers)
