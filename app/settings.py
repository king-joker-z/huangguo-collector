"""可持久化的运行时设置。"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .config import DEFAULT_SETTINGS
from .db import Database


class Settings:
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

    def get(self, key: str, default: str | None = None) -> str:
        with self.lock:
            return self._data.get(key, DEFAULT_SETTINGS.get(key, default if default is not None else ""))

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get(key, str(default))))
        except (TypeError, ValueError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, "1" if default else "0").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def update(self, values: dict) -> None:
        allowed = set(DEFAULT_SETTINGS.keys())
        with self.lock:
            for key, value in values.items():
                if key not in allowed:
                    continue
                text = "" if value is None else str(value)
                self._data[key] = text
                self.db.set_setting(key, text)

    def all(self) -> dict[str, str]:
        with self.lock:
            return dict(self._data)

    @property
    def download_root(self) -> Path:
        path = Path(self.get("download_root") or DEFAULT_SETTINGS["download_root"]).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
