"""全局路径与默认配置。"""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
WEB_DIR = ROOT_DIR / "web"
VERSION = "1.11.3"
DATA_DIR = Path(os.environ.get("HUANGGUO_DATA_DIR") or (ROOT_DIR / "data")).expanduser()
COVERS_DIR = DATA_DIR / "covers"
EPISODE_COVERS_DIR = COVERS_DIR / "episodes"
AVATARS_DIR = DATA_DIR / "avatars"
TMP_DIR = DATA_DIR / "tmp"
LOG_DIR = DATA_DIR / "logs"

for _d in (DATA_DIR, COVERS_DIR, EPISODE_COVERS_DIR, AVATARS_DIR, TMP_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "huangguo.db"

SITE_BASE = "https://huangguoai.com"
YEGUO_BASE = "https://yeguodj.com"
# 欧美影视分区使用可配置的博主页站点（Pornhub 风格：#getAvatar 头像 +
# ul/li 视频卡 + viewkey 详情页 + mediaDefinitions 下载链接）。
# 真实地址在设置页填写（film/ph_base），默认为空表示「未配置」。
PH_DEFAULT_BASE = ""
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

SOURCES = {
    "huangguo": {"label": "黄果", "base": SITE_BASE},
    "yeguo": {"label": "野果", "base": YEGUO_BASE},
    "ph": {"label": "欧美站", "base": PH_DEFAULT_BASE},
}

# 一级模块分区：导航首页按 group 分成不同入口，互不影响。
# 新增站点时只需要给 MODULES 加一项并指定 group，前端导航会自动出现。
MODULE_GROUPS = {
    "duanju": {
        "label": "短剧",
        "icon": "▶",
        "desc": "AI 短剧 / 漫剧 / 原创",
    },
    "film": {
        "label": "欧美影视",
        "icon": "▷",
        "desc": "博主 / 创作者影片，按演员订阅采集",
    },
}

GROUP_ORDER = ("duanju", "film")

MODULES = {
    "original": {
        "label": "原创",
        "icon": "◆",
        "base": "/original/",
        "desc": "黄果原创",
        "source": "huangguo",
        "group": "duanju",
        "media_type": "tv",  # tv | movie
    },
    "duanju": {
        "label": "短剧",
        "icon": "▶",
        "base": "/ai-duanju/",
        "desc": "AI 短剧",
        "source": "huangguo",
        "group": "duanju",
        "media_type": "tv",  # tv | movie
    },
    "manju": {
        "label": "漫剧",
        "icon": "✦",
        "base": "/ai-manju/",
        "desc": "AI 漫剧",
        "source": "huangguo",
        "group": "duanju",
        "media_type": "tv",  # tv | movie
    },
    "yeguo": {
        "label": "野果短剧",
        "icon": "🍑",
        "base": "/explore/drama/",
        "desc": "野果短剧",
        "source": "yeguo",
        "group": "duanju",
        "media_type": "tv",  # tv | movie
    },
    "ph_creator": {
        "label": "博主影片",
        "icon": "▷",
        "base": "/",
        "desc": "按演员订阅采集博主的所有影片",
        "source": "ph",
        "group": "film",
        "media_type": "movie",  # 每部影片独立成电影目录
    },
}


def module_group(module: str) -> str:
    return str(MODULES.get(module, {}).get("group") or "duanju")


def modules_in_group(group: str) -> list[str]:
    if not group:
        return list(MODULES.keys())
    return [key for key, meta in MODULES.items() if meta.get("group") == group]


def module_media_type(module: str) -> str:
    """模块的媒体类型：tv（剧集）或 movie（电影）。"""
    value = str(MODULES.get(module, {}).get("media_type") or "tv").strip().lower()
    return "movie" if value == "movie" else "tv"


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
    "translate_titles": "0",      # 英文标题自动翻译开关
    "translate_target": "zh-CN",  # 目标语言
    "ph_base": "",                # 欧美影视站点根地址，如 https://www.pornhub.com
    "ph_max_pages": "0",          # 每个博主单次采集的翻页上限，0 = 全部
}


def ensure_dirs() -> None:
    for d in (DATA_DIR, COVERS_DIR, EPISODE_COVERS_DIR, AVATARS_DIR, TMP_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}




