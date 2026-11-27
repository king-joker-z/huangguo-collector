"""可持久化的运行时设置（支持按分区隔离）。"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .config import DEFAULT_SETTINGS
from .db import Database
from .groupctx import get_group


class Settings:
    """设置按分区存储。

    存储键为 ``"{group}/{key}"``：分区没有单独配置时回退到全局 ``key``，
    再回退到 ``DEFAULT_SETTINGS``。未显式传 group 时使用当前线程的分区上下文。
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.lock = threading.RLock()
        self._data: dict[str, str] = dict(DEFAULT_SETTINGS)
        self._data.update(db.get_settings())
        env_root = os.environ.get("HUANGGUO_DOWNLOAD_ROOT")
        if env_root:
            self._data["download_root"] = env_root

    def reload(self) -> None:
        with self.lock:
            self._data = dict(DEFAULT_SETTINGS)
            self._data.update(self.db.get_settings())
            env_root = os.environ.get("HUANGGUO_DOWNLOAD_ROOT")
            if env_root:
                self._data["download_root"] = env_root

    @staticmethod
    def _resolve_group(group: str | None) -> str:
        return get_group() if group is None else (group or "")

    def get(self, key: str, default: str | None = None, group: str | None = None) -> str:
        group_key = self._resolve_group(group)
        fallback = DEFAULT_SETTINGS.get(key, default if default is not None else "")
        with self.lock:
            if group_key:
                value = self._data.get(f"{group_key}/{key}")
                if value is not None:
                    return value
            value = self._data.get(key)
            if value is not None:
                return value
            return fallback

    def get_int(self, key: str, default: int = 0, group: str | None = None) -> int:
        try:
            return int(float(self.get(key, str(default), group=group)))
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0, group: str | None = None) -> float:
        try:
            return float(self.get(key, str(default), group=group))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False, group: str | None = None) -> bool:
        value = self.get(key, "1" if default else "0", group=group).strip().lower()
        return value in {"1", "true", "yes", "on"}

    def update(self, values: dict, group: str | None = None) -> None:
        allowed = set(DEFAULT_SETTINGS.keys())
        group_key = self._resolve_group(group)
        with self.lock:
            for key, value in values.items():
                if key not in allowed:
                    continue
                text = "" if value is None else str(value)
                storage_key = f"{group_key}/{key}" if group_key else key
                self._data[storage_key] = text
                self.db.set_setting(storage_key, text)

    def all(self, group: str | None = None) -> dict[str, str]:
        return {key: self.get(key, group=group) for key in DEFAULT_SETTINGS}

    def root_for(self, group: str | None = None) -> Path:
        raw = self.get("download_root", "", group=group) or DEFAULT_SETTINGS["download_root"]
        path = Path(raw).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def download_root(self) -> Path:
        return self.root_for()
