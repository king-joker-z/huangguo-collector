"""全局路径与默认配置。"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
WEB_DIR = ROOT_DIR / "web"
DATA_DIR = Path(os.environ.get("HUANGGUO_DATA_DIR") or (ROOT_DIR / "data")).expanduser()
COVERS_DIR = DATA_DIR / "covers"
EPISODE_COVERS_DIR = COVERS_DIR / "episodes"
TMP_DIR = DATA_DIR / "tmp"
LOG_DIR = DATA_DIR / "logs"

for _d in (DATA_DIR, COVERS_DIR, EPISODE_COVERS_DIR, TMP_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "huangguo.db"

SITE_BASE = "https://huangguoai.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MODULES = {
    "original": {
        "label": "原创",
        "base": "/original/",
        "desc": "黄果原创",
    },
    "duanju": {
        "label": "短剧",
        "base": "/ai-duanju/",
        "desc": "AI 短剧",
    },
    "manju": {
        "label": "漫剧",
        "base": "/ai-manju/",
        "desc": "AI 漫剧",
    },
}

DEFAULT_DOWNLOAD_ROOT = os.environ.get("HUANGGUO_DOWNLOAD_ROOT") or str(
    ROOT_DIR / "huangguo-downloads"
)

DEFAULT_SETTINGS = {
    "download_root": DEFAULT_DOWNLOAD_ROOT,
    "quality": "hevc",            # hevc | h264 | 720p
    "concurrency": "2",           # 同时下载集数
    "scan_workers": "6",          # 扫描详情并发
    "request_delay": "0.15",      # 每次请求间隔(秒)
    "proxy": "",                  # http://127.0.0.1:7890 可选
    "cookie": "",                 # 可选，浏览器 Cookie
    "write_nfo": "1",
    "write_fanart": "1",
    "auto_tag_done": "1",
    "folder_template": "{module}/{title} ({year})",
    "hls_proxy": "1",
}


def ensure_dirs() -> None:
    for d in (DATA_DIR, COVERS_DIR, EPISODE_COVERS_DIR, TMP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}
