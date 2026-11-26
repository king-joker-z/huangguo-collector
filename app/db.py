"""SQLite 数据层：剧集、分集、标签、下载任务。"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime
from typing import Any, Iterable

from .config import DB_PATH, MODULES, SOURCES

SCHEMA = """
CREATE TABLE IF NOT EXISTS dramas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL UNIQUE,
    module TEXT NOT NULL,
    modules_json TEXT DEFAULT '[]',
    source TEXT DEFAULT 'huangguo',
    title_key TEXT DEFAULT '',
    duplicate_of INTEGER DEFAULT 0,
    is_duplicate INTEGER DEFAULT 0,
    manual_preferred INTEGER DEFAULT 0,
    keep_separate INTEGER DEFAULT 0,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    cover_url TEXT DEFAULT '',
    cover_path TEXT DEFAULT '',
    score REAL,
    total_ep INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    author TEXT DEFAULT '',
    status_text TEXT DEFAULT '',
    views TEXT DEFAULT '',
    premiered TEXT DEFAULT '',
    duration_text TEXT DEFAULT '',
    category_text TEXT DEFAULT '',
    tags_json TEXT DEFAULT '[]',
    badges_json TEXT DEFAULT '[]',
    year INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_scraped_at TEXT,
    detail_scraped_at TEXT,
    prev_total_ep INTEGER DEFAULT 0,
    has_update INTEGER DEFAULT 0,
    favorite INTEGER DEFAULT 0,
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dramas_module ON dramas(module);
CREATE INDEX IF NOT EXISTS idx_dramas_update ON dramas(has_update);
CREATE INDEX IF NOT EXISTS idx_dramas_score ON dramas(score);
CREATE INDEX IF NOT EXISTS idx_dramas_last_scraped ON dramas(last_scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_dramas_updated ON dramas(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_dramas_update_updated ON dramas(has_update, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_dramas_favorite ON dramas(favorite);
CREATE INDEX IF NOT EXISTS idx_dramas_year ON dramas(year);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
    ep INTEGER NOT NULL,
    site_video_id TEXT DEFAULT '',
    title TEXT DEFAULT '',
    synopsis TEXT DEFAULT '',
    duration INTEGER DEFAULT 0,
    state TEXT DEFAULT 'pending',
    file_path TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0,
    cover_url TEXT DEFAULT '',
    cover_path TEXT DEFAULT '',
    downloaded_at TEXT,
    UNIQUE(drama_id, ep)
);
CREATE INDEX IF NOT EXISTS idx_episodes_drama ON episodes(drama_id);
CREATE INDEX IF NOT EXISTS idx_episodes_state ON episodes(state);
CREATE INDEX IF NOT EXISTS idx_episodes_drama_state ON episodes(drama_id, state);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '#e5484d',
    kind TEXT DEFAULT 'custom',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drama_tags (
    drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (drama_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_drama_tags_tag ON drama_tags(tag_id);

CREATE TABLE IF NOT EXISTS excluded_tags (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
    ep INTEGER NOT NULL,
    quality TEXT DEFAULT 'hevc',
    state TEXT DEFAULT 'queued',
    progress REAL DEFAULT 0,
    speed REAL DEFAULT 0,
    size INTEGER DEFAULT 0,
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    UNIQUE(drama_id, ep)
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_drama_state ON tasks(drama_id, state);
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC);

CREATE TABLE IF NOT EXISTS subscriptions (
    drama_id INTEGER PRIMARY KEY REFERENCES dramas(id) ON DELETE CASCADE,
    enabled INTEGER DEFAULT 1,
    quality TEXT DEFAULT 'hevc',
    auto_download INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_auto_at TEXT,
    last_auto_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_enabled ON subscriptions(enabled);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    time TEXT NOT NULL,
    weekdays TEXT DEFAULT '0,1,2,3,4,5,6',
    modules_json TEXT DEFAULT '["original","duanju","manju","yeguo"]',
    details INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT,
    next_run_at TEXT,
    last_status TEXT DEFAULT '',
    last_message TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module TEXT,
    state TEXT,
    found INTEGER DEFAULT 0,
    new_count INTEGER DEFAULT 0,
    updated_count INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    options_json TEXT DEFAULT '{}',
    resumed_from INTEGER DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);
"""

VISIBLE_VIEW = """
CREATE VIEW IF NOT EXISTS visible_dramas AS
SELECT d.* FROM dramas d
WHERE d.is_duplicate=0
  AND NOT EXISTS (
    SELECT 1 FROM json_each(
      CASE WHEN d.tags_json IS NULL OR d.tags_json='' THEN '[]' ELSE d.tags_json END
    ) je
    JOIN excluded_tags et ON et.name=je.value
  )
  AND NOT EXISTS (
    SELECT 1 FROM drama_tags dt
    JOIN tags t ON t.id=dt.tag_id
    JOIN excluded_tags et ON et.name=t.name
    WHERE dt.drama_id=d.id
  )
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def title_key(title: str) -> str:
    """跨站点同名判断：全角转半角、去空白和标点、忽略大小写。"""
    text = unicodedata.normalize("NFKC", str(title or "")).lower()
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def normalize_tag(name: str) -> str:
    return unicodedata.normalize("NFKC", str(name or "")).strip()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


MODULE_ORDER = ("original", "duanju", "manju", "yeguo")


def ordered_modules(modules: Any) -> list[str]:
    values = {str(m) for m in (modules or []) if str(m)}
    ordered = [m for m in MODULE_ORDER if m in values]
    ordered.extend(sorted(values - set(ordered)))
    return ordered


class Database:
    def __init__(self, path=DB_PATH) -> None:
        self.lock = threading.RLock()
        self._stats_lock = threading.Lock()
        self._stats_cache: tuple[float, dict] | None = None
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=60)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        with self.lock:
            self.conn.executescript(SCHEMA)
            columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(dramas)")}
            if "modules_json" not in columns:
                self.conn.execute("ALTER TABLE dramas ADD COLUMN modules_json TEXT DEFAULT '[]'")
            if "source" not in columns:
                self.conn.execute("ALTER TABLE dramas ADD COLUMN source TEXT DEFAULT 'huangguo'")
            if "title_key" not in columns:
                self.conn.execute("ALTER TABLE dramas ADD COLUMN title_key TEXT DEFAULT ''")
            if "duplicate_of" not in columns:
                self.conn.execute("ALTER TABLE dramas ADD COLUMN duplicate_of INTEGER DEFAULT 0")
            if "is_duplicate" not in columns:
                self.conn.execute("ALTER TABLE dramas ADD COLUMN is_duplicate INTEGER DEFAULT 0")
            if "manual_preferred" not in columns:
                self.conn.execute("ALTER TABLE dramas ADD COLUMN manual_preferred INTEGER DEFAULT 0")
            if "keep_separate" not in columns:
                self.conn.execute("ALTER TABLE dramas ADD COLUMN keep_separate INTEGER DEFAULT 0")
            episode_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(episodes)")}
            if "cover_url" not in episode_columns:
                self.conn.execute("ALTER TABLE episodes ADD COLUMN cover_url TEXT DEFAULT ''")
            if "cover_path" not in episode_columns:
                self.conn.execute("ALTER TABLE episodes ADD COLUMN cover_path TEXT DEFAULT ''")
            scan_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(scan_runs)")}
            if "options_json" not in scan_columns:
                self.conn.execute("ALTER TABLE scan_runs ADD COLUMN options_json TEXT DEFAULT '{}'")
            if "resumed_from" not in scan_columns:
                self.conn.execute("ALTER TABLE scan_runs ADD COLUMN resumed_from INTEGER DEFAULT 0")
            self.conn.execute(
                "UPDATE dramas SET modules_json=json_array(module) "
                "WHERE modules_json IS NULL OR modules_json='' OR modules_json='[]'"
            )
            self.conn.execute(
                "UPDATE dramas SET source='huangguo' WHERE source IS NULL OR source=''"
            )
            rows = self.conn.execute(
                "SELECT id,title FROM dramas WHERE title_key IS NULL OR title_key=''"
            ).fetchall()
            for row in rows:
                self.conn.execute(
                    "UPDATE dramas SET title_key=? WHERE id=?",
                    (title_key(row["title"]), row["id"]),
                )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dramas_source ON dramas(source)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dramas_title_key ON dramas(title_key)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dramas_duplicate ON dramas(is_duplicate)"
            )
            self.conn.execute(VISIBLE_VIEW)
            self.conn.commit()
            self.resolve_all_duplicates()

    # ------------------------------------------------------------------ basic
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return list(self.conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    # --------------------------------------------------------------- settings
    def get_settings(self) -> dict[str, str]:
        rows = self.query("SELECT key,value FROM settings")
        return {r["key"]: r["value"] for r in rows}

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ----------------------------------------------------------------- dramas
    def get_drama_by_site_id(self, site_id: str) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM dramas WHERE site_id=?", (site_id,))

    def get_drama(self, drama_id: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM dramas WHERE id=?", (drama_id,))

    def upsert_card(self, card: dict) -> tuple[int, bool, bool]:
        """写入列表卡片。返回 (drama_id, is_new, has_update)。"""
        site_id = str(card["site_id"])
        source = str(card.get("source") or "huangguo")
        key = title_key(card.get("title") or "")
        total = int(card.get("total_ep") or 0)
        ts = now_str()
        with self.lock:
            row = self.conn.execute(
                "SELECT id,total_ep,detail_scraped_at,module,modules_json,source,title_key "
                "FROM dramas WHERE site_id=?",
                (site_id,),
            ).fetchone()
            if row is None:
                cur = self.conn.execute(
                    """INSERT INTO dramas(site_id,module,modules_json,source,title_key,title,url,cover_url,score,total_ep,
                       views,tags_json,badges_json,created_at,updated_at,last_scraped_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        site_id,
                        card["module"],
                        json_dumps([card["module"]]),
                        source,
                        key,
                        card["title"],
                        card["url"],
                        card.get("cover_url", ""),
                        card.get("score"),
                        total,
                        card.get("views", ""),
                        json_dumps(card.get("tags", [])),
                        json_dumps(card.get("badges", [])),
                        ts,
                        ts,
                        ts,
                    ),
                )
                self.conn.commit()
                drama_id = int(cur.lastrowid)
                self.resolve_title_group(key)
                return drama_id, True, False

            try:
                modules = set(json.loads(row["modules_json"] or "[]"))
            except Exception:
                modules = set()
            modules.add(card["module"])
            ordered = ordered_modules(modules)
            primary = ordered[0]
            old_total = int(row["total_ep"] or 0)
            new_total = max(old_total, total)
            has_update = 1 if old_total > 0 and total > old_total else 0
            old_key = str(row["title_key"] or "")
            self.conn.execute(
                """UPDATE dramas SET module=?,modules_json=?,source=?,title_key=?,title=?,url=?,cover_url=?,score=?,
                   total_ep=?,views=?,tags_json=?,badges_json=?,
                   last_scraped_at=?,
                   prev_total_ep=CASE WHEN ?=1 THEN ? ELSE prev_total_ep END,
                   has_update=CASE WHEN ?=1 THEN 1 ELSE has_update END
                   WHERE id=?""",
                (
                    primary,
                    json_dumps(ordered),
                    source,
                    key,
                    card["title"],
                    card["url"],
                    card.get("cover_url", ""),
                    card.get("score"),
                    new_total,
                    card.get("views", ""),
                    json_dumps(card.get("tags", [])),
                    json_dumps(card.get("badges", [])),
                    ts,
                    1 if has_update else 0,
                    old_total,
                    1 if has_update else 0,
                    row["id"],
                ),
            )
            self.conn.commit()
            self.resolve_title_group(key)
            if old_key and old_key != key:
                self.resolve_title_group(old_key)
            return int(row["id"]), False, bool(has_update)

    def apply_detail(self, site_id: str, detail: dict) -> tuple[int, bool]:
        """写入详情页数据与分集。返回 (drama_id, has_update)。"""
        ts = now_str()
        with self.lock:
            row = self.conn.execute(
                "SELECT id,total_ep,detail_scraped_at,title_key,title,views FROM dramas WHERE site_id=?", (site_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"drama {site_id} not found")
            old_total = int(row["total_ep"] or 0)
            old_key = str(row["title_key"] or "")
            new_total = max(old_total, int(detail.get("total_ep") or 0))
            incoming_title = str(detail.get("title") or row["title"] or "")
            key = title_key(incoming_title)
            has_update = old_total > 0 and int(detail.get("total_ep") or 0) > old_total
            self.conn.execute(
                """UPDATE dramas SET title=?,title_key=?,description=?,cover_url=?,score=?,total_ep=?,
                   author=?,status_text=?,views=?,premiered=?,duration_text=?,category_text=?,
                   tags_json=?,year=?,updated_at=?,detail_scraped_at=?,
                   prev_total_ep=CASE WHEN ?=1 THEN ? ELSE prev_total_ep END,
                   has_update=CASE WHEN ?=1 THEN 1 ELSE has_update END
                   WHERE id=?""",
                (
                    incoming_title,
                    key,                    detail.get("description") or "",
                    detail.get("cover_url") or "",
                    detail.get("score"),
                    new_total,
                    detail.get("author") or "",
                    detail.get("status_text") or "",
                    detail.get("views") or row["views"] or "",
                    detail.get("premiered") or "",
                    detail.get("duration_text") or "",
                    detail.get("category_text") or "",
                    json_dumps(detail.get("tags") or []),
                    detail.get("year"),
                    ts,
                    ts,
                    1 if has_update else 0,
                    old_total,
                    1 if has_update else 0,
                    row["id"],
                ),
            )
            episodes = detail.get("episodes") or []
            for ep in episodes:
                self.conn.execute(
                    """INSERT INTO episodes(drama_id,ep,site_video_id,title,synopsis,duration,cover_url)
                       VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(drama_id,ep) DO UPDATE SET
                         site_video_id=CASE WHEN excluded.site_video_id<>'' THEN excluded.site_video_id ELSE episodes.site_video_id END,
                         title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE episodes.title END,
                         synopsis=CASE WHEN excluded.synopsis<>'' THEN excluded.synopsis ELSE episodes.synopsis END,
                         duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE episodes.duration END,
                         cover_url=CASE WHEN excluded.cover_url<>'' THEN excluded.cover_url ELSE episodes.cover_url END""",
                    (
                        row["id"],
                        int(ep["ep"]),
                        ep.get("site_video_id") or "",
                        ep.get("title") or "",
                        ep.get("synopsis") or "",
                        int(ep.get("duration") or 0),
                        ep.get("cover_url") or "",
                    ),
                )
            self.conn.commit()
            self.resolve_title_group(key)
            if old_key and old_key != key:
                self.resolve_title_group(old_key)
            return int(row["id"]), bool(has_update)

    def resolve_title_group(self, key: str) -> None:
        """同名跨站点去重：集数最多者为主版本，其他来源标记为重复。"""
        if not key:
            return
        rows = self.query(
            "SELECT id,source,total_ep,manual_preferred,keep_separate,is_duplicate FROM dramas "
            "WHERE title_key=? ORDER BY keep_separate DESC, manual_preferred DESC, total_ep DESC, id ASC",
            (key,),
        )
        if not rows:
            return
        kept = [row for row in rows if int(row["keep_separate"] or 0)]
        non_kept = [row for row in rows if not int(row["keep_separate"] or 0)]
        manual = next((row for row in rows if int(row["manual_preferred"] or 0)), None)
        # 手动保留的版本可以同时显示；如果保留的是当前主版本，不要再把其他版本
        # 提升为可见；如果保留的是隐藏版本，则原主版本继续显示。
        if manual:
            preferred = manual
        elif any(int(row["is_duplicate"] or 0) == 0 for row in kept):
            preferred = next(row for row in kept if int(row["is_duplicate"] or 0) == 0)
        elif non_kept:
            preferred = non_kept[0]
        elif kept:
            preferred = kept[0]
        else:
            preferred = rows[0]
        visible_ids = {int(row["id"]) for row in kept}
        visible_ids.add(int(preferred["id"]))
        for row in rows:
            is_dup = 0 if int(row["id"]) in visible_ids else 1
            dup_of = 0 if is_dup == 0 else int(preferred["id"])
            self.execute(
                "UPDATE dramas SET duplicate_of=?,is_duplicate=? "
                "WHERE id=? AND (duplicate_of<>? OR is_duplicate<>?)",
                (dup_of, is_dup, row["id"], dup_of, is_dup),
            )

    def resolve_all_duplicates(self) -> None:
        rows = self.query(
            "SELECT title_key FROM dramas WHERE title_key<>'' "
            "GROUP BY title_key HAVING COUNT(*)>1"
        )
        for row in rows:
            self.resolve_title_group(str(row["title_key"]))

    def list_duplicate_groups(self) -> list[dict]:
        rows = self.query(
            """SELECT d.*,
                 (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id AND e.state='done') AS done_ep,
                 (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id) AS ep_rows,
                 (SELECT COUNT(*) FROM subscriptions s WHERE s.drama_id=d.id AND s.enabled=1) AS subscribed_flag,
                 (SELECT s.quality FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_quality,
                 (SELECT s.auto_download FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_auto
               FROM dramas d
               WHERE d.title_key<>'' AND d.title_key IN (
                 SELECT title_key FROM dramas WHERE title_key<>''
                 GROUP BY title_key HAVING COUNT(*)>1
               )
               ORDER BY d.title_key, d.is_duplicate, d.manual_preferred DESC, d.total_ep DESC, d.id"""
        )
        groups: dict[str, dict] = {}
        for row in rows:
            item = self._drama_dict(row)
            item["manual_preferred"] = bool(row["manual_preferred"])
            key = str(row["title_key"])
            group = groups.setdefault(key, {"key": key, "title": item["title"], "items": []})
            group["items"].append(item)
        out = []
        for group in groups.values():
            visible = [i for i in group["items"] if not i["is_duplicate"]]
            hidden = [i for i in group["items"] if i["is_duplicate"]]
            preferred = visible[0] if visible else (group["items"][0] if group["items"] else None)
            if not preferred:
                continue
            group["preferred"] = preferred
            group["duplicates"] = hidden
            group["manual"] = bool(preferred.get("manual_preferred"))
            out.append(group)
        out.sort(key=lambda g: (not g["manual"], -len(g["items"]), g["title"]))
        return out

    def prefer_duplicate(self, drama_id: int, merge_metadata: bool = True) -> str:
        row = self.get_drama(drama_id)
        if not row:
            raise KeyError("剧集不存在")
        key = str(row["title_key"] or "")
        if not key:
            raise ValueError("该剧没有可合并的同名分组")
        group = [dict(r) for r in self.query(
            "SELECT id,title FROM dramas WHERE title_key=?", (key,)
        )]
        if len(group) < 2:
            raise ValueError("该剧没有其他同名版本")
        self.execute(
            "UPDATE dramas SET manual_preferred=CASE WHEN id=? THEN 1 ELSE 0 END "
            "WHERE title_key=?",
            (drama_id, key),
        )
        if merge_metadata:
            other_ids = [int(r["id"]) for r in group if int(r["id"]) != int(drama_id)]
            if other_ids:
                placeholders = ",".join("?" for _ in other_ids)
                self.execute(
                    f"""INSERT OR IGNORE INTO drama_tags(drama_id,tag_id)
                        SELECT ?, tag_id FROM drama_tags WHERE drama_id IN ({placeholders})""",
                    (drama_id, *other_ids),
                )
                self.execute(
                    f"UPDATE dramas SET favorite=1 WHERE id=? AND EXISTS "
                    f"(SELECT 1 FROM dramas WHERE id IN ({placeholders}) AND favorite=1)",
                    (drama_id, *other_ids),
                )
        self.resolve_title_group(key)
        return key

    def set_duplicate_keep(self, drama_id: int, keep: bool = True) -> str:
        row = self.get_drama(drama_id)
        if not row:
            raise KeyError("剧集不存在")
        key = str(row["title_key"] or "")
        if not key:
            raise ValueError("该剧没有同名分组")
        self.execute(
            "UPDATE dramas SET keep_separate=? WHERE id=?",
            (1 if keep else 0, int(drama_id)),
        )
        self.resolve_title_group(key)
        return key

    def reset_duplicate_preference(self, drama_id: int) -> str:
        row = self.get_drama(drama_id)
        if not row:
            raise KeyError("剧集不存在")
        key = str(row["title_key"] or "")
        if not key:
            raise ValueError("该剧没有同名分组")
        self.execute("UPDATE dramas SET manual_preferred=0 WHERE title_key=?", (key,))
        self.resolve_title_group(key)
        return key

    def mark_detail_error(self, site_id: str) -> None:
        self.execute("UPDATE dramas SET detail_scraped_at=? WHERE site_id=?", (now_str(), site_id))

    def set_drama_fields(self, drama_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"favorite", "notes", "has_update", "prev_total_ep", "cover_path", "cover_url"}
        parts, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            parts.append(f"{key}=?")
            params.append(value)
        if not parts:
            return
        params.append(drama_id)
        self.execute(f"UPDATE dramas SET {', '.join(parts)} WHERE id=?", params)

    def clear_update(self, drama_id: int) -> None:
        self.execute("UPDATE dramas SET has_update=0, prev_total_ep=total_ep WHERE id=?", (drama_id,))

    def need_detail(self, site_id: str) -> bool:
        row = self.get_drama_by_site_id(site_id)
        if not row:
            return True
        return not row["detail_scraped_at"] or not row["description"]

    # --------------------------------------------------------------- episodes
    def get_episodes(self, drama_id: int) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM episodes WHERE drama_id=? ORDER BY ep", (drama_id,))

    def get_episode(self, drama_id: int, ep: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM episodes WHERE drama_id=? AND ep=?", (drama_id, ep))

    def set_episode_state(self, drama_id: int, ep: int, state: str,
                          file_path: str = "", file_size: int = 0) -> None:
        if state == "done":
            self.execute(
                "UPDATE episodes SET state=?,file_path=?,file_size=?,downloaded_at=? WHERE drama_id=? AND ep=?",
                (state, file_path, file_size, now_str(), drama_id, ep),
            )
        else:
            self.execute(
                "UPDATE episodes SET state=? WHERE drama_id=? AND ep=?",
                (state, drama_id, ep),
            )

    def set_episode_cover(self, drama_id: int, ep: int, cover_path: str, cover_url: str = "") -> None:
        self.execute(
            "UPDATE episodes SET cover_path=?,cover_url=? WHERE drama_id=? AND ep=?",
            (cover_path, cover_url, drama_id, ep),
        )

    def refresh_drama_progress(self, drama_id: int) -> dict:
        row = self.query_one(
            """SELECT d.total_ep,
                      (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id AND e.state='done') AS done_ep
               FROM dramas d WHERE d.id=?""",
            (drama_id,),
        )
        total = int(row["total_ep"] or 0) if row else 0
        done = int(row["done_ep"] or 0) if row else 0
        if total > 0 and done >= total:
            self.clear_update(drama_id)
        return {"total_ep": total, "done_ep": done}

    # ------------------------------------------------------------------- tags
    def excluded_tag_names(self) -> list[str]:
        return [str(r["name"]) for r in self.query("SELECT name FROM excluded_tags ORDER BY name")]

    def list_excluded_tags(self) -> list[dict]:
        return [dict(r) for r in self.query(
            "SELECT name,created_at FROM excluded_tags ORDER BY created_at DESC"
        )]

    def add_excluded_tag(self, name: str) -> None:
        name = normalize_tag(name)
        if not name:
            raise ValueError("标签名不能为空")
        self.execute(
            "INSERT OR IGNORE INTO excluded_tags(name,created_at) VALUES(?,?)",
            (name, now_str()),
        )
        self.invalidate_stats()

    def remove_excluded_tag(self, name: str) -> None:
        self.execute("DELETE FROM excluded_tags WHERE name=?", (normalize_tag(name),))
        self.invalidate_stats()

    def invalidate_stats(self) -> None:
        with self._stats_lock:
            self._stats_cache = None

    def _excluded_clause(self, alias: str = "d") -> tuple[str, list[str]]:
        names = self.excluded_tag_names()
        if not names:
            return "", []
        placeholders = ",".join("?" for _ in names)
        clause = f"""NOT EXISTS (
            SELECT 1 FROM json_each(
              CASE WHEN {alias}.tags_json IS NULL OR {alias}.tags_json='' THEN '[]' ELSE {alias}.tags_json END
            ) je WHERE je.value IN ({placeholders})
          ) AND NOT EXISTS (
            SELECT 1 FROM drama_tags dt JOIN tags t ON t.id=dt.tag_id
            WHERE dt.drama_id={alias}.id AND t.name IN ({placeholders})
          )"""
        return clause, [*names, *names]

    def is_tag_excluded(self, drama_id: int) -> bool:
        names = self.excluded_tag_names()
        if not names:
            return False
        placeholders = ",".join("?" for _ in names)
        row = self.query_one(
            f"""SELECT 1 AS hit FROM dramas d WHERE d.id=? AND (
              EXISTS (
                SELECT 1 FROM json_each(
                  CASE WHEN d.tags_json IS NULL OR d.tags_json='' THEN '[]' ELSE d.tags_json END
                ) je WHERE je.value IN ({placeholders})
              ) OR EXISTS (
                SELECT 1 FROM drama_tags dt JOIN tags t ON t.id=dt.tag_id
                WHERE dt.drama_id=d.id AND t.name IN ({placeholders})
              )
            )""",
            [drama_id, *names, *names],
        )
        return bool(row)

    def ensure_tag(self, name: str, color: str = "#e5484d", kind: str = "custom") -> int:
        row = self.query_one("SELECT id FROM tags WHERE name=?", (name,))
        if row:
            return int(row["id"])
        cur = self.execute(
            "INSERT INTO tags(name,color,kind,created_at) VALUES(?,?,?,?)",
            (name, color, kind, now_str()),
        )
        return int(cur.lastrowid)

    def list_tags(self) -> list[dict]:
        rows = self.query(
            """SELECT t.*, (SELECT COUNT(*) FROM drama_tags dt WHERE dt.tag_id=t.id) AS count
               FROM tags t ORDER BY t.kind='system' DESC, t.id"""
        )
        excluded = set(self.excluded_tag_names())
        return [{**dict(r), "excluded": str(r["name"]) in excluded} for r in rows]

    def tags_for_drama(self, drama_id: int) -> list[dict]:
        rows = self.query(
            """SELECT t.* FROM tags t JOIN drama_tags dt ON dt.tag_id=t.id
               WHERE dt.drama_id=? ORDER BY t.id""",
            (drama_id,),
        )
        return [dict(r) for r in rows]

    def tags_for_dramas(self, ids: list[int]) -> dict[int, list[dict]]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.query(
            f"""SELECT dt.drama_id, t.id, t.name, t.color, t.kind
                FROM drama_tags dt JOIN tags t ON t.id=dt.tag_id
                WHERE dt.drama_id IN ({placeholders}) ORDER BY t.id""",
            ids,
        )
        out: dict[int, list[dict]] = {i: [] for i in ids}
        for r in rows:
            out[int(r["drama_id"])].append(
                {"id": r["id"], "name": r["name"], "color": r["color"], "kind": r["kind"]}
            )
        return out

    def add_tag_to_drama(self, drama_id: int, tag_id: int) -> None:
        self.execute(
            "INSERT OR IGNORE INTO drama_tags(drama_id,tag_id) VALUES(?,?)", (drama_id, tag_id)
        )

    def remove_tag_from_drama(self, drama_id: int, tag_id: int) -> None:
        self.execute("DELETE FROM drama_tags WHERE drama_id=? AND tag_id=?", (drama_id, tag_id))

    def delete_tag(self, tag_id: int) -> None:
        self.execute("DELETE FROM tags WHERE id=?", (tag_id,))

    def set_drama_tags(self, drama_id: int, add: list[str], remove: list[str]) -> None:
        for name in add:
            name = name.strip()
            if not name:
                continue
            tag_id = self.ensure_tag(name)
            self.add_tag_to_drama(drama_id, tag_id)
        for name in remove:
            row = self.query_one("SELECT id FROM tags WHERE name=?", (name.strip(),))
            if row:
                self.remove_tag_from_drama(drama_id, int(row["id"]))

    # ----------------------------------------------------------- subscriptions
    def get_subscription(self, drama_id: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM subscriptions WHERE drama_id=?", (drama_id,))

    def set_subscription(self, drama_id: int, enabled: bool = True, quality: str = "hevc",
                         auto_download: bool = True) -> None:
        ts = now_str()
        self.execute(
            """INSERT INTO subscriptions(drama_id,enabled,quality,auto_download,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(drama_id) DO UPDATE SET
                 enabled=excluded.enabled,
                 quality=excluded.quality,
                 auto_download=excluded.auto_download,
                 updated_at=excluded.updated_at""",
            (drama_id, 1 if enabled else 0, quality, 1 if auto_download else 0, ts, ts),
        )

    def delete_subscription(self, drama_id: int) -> None:
        self.execute("DELETE FROM subscriptions WHERE drama_id=?", (drama_id,))

    def mark_subscription_auto(self, drama_id: int, count: int) -> None:
        self.execute(
            "UPDATE subscriptions SET last_auto_at=?,last_auto_count=? WHERE drama_id=?",
            (now_str(), int(count), drama_id),
        )

    def list_subscriptions(self) -> list[dict]:
        rows = self.query(
            """SELECT d.*,
                 s.quality AS sub_quality,
                 s.auto_download AS sub_auto,
                 s.enabled AS sub_enabled,
                 s.created_at AS sub_created_at,
                 s.updated_at AS sub_updated_at,
                 s.last_auto_at,
                 s.last_auto_count,
                 1 AS subscribed_flag,
                 (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id AND e.state='done') AS done_ep,
                 (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id) AS ep_rows,
                 (SELECT COUNT(*) FROM tasks t WHERE t.drama_id=d.id AND t.state IN ('queued','downloading')) AS active_tasks
               FROM subscriptions s JOIN visible_dramas d ON d.id=s.drama_id
               ORDER BY d.has_update DESC, s.created_at DESC"""
        )
        items = [self._drama_dict(r) for r in rows]
        tag_map = self.tags_for_dramas([int(r["id"]) for r in rows])
        for item in items:
            item["tags"] = tag_map.get(item["id"], [])
        return items

    # --------------------------------------------------------------- schedules
    def list_schedules(self) -> list[dict]:
        rows = self.query("SELECT * FROM schedules ORDER BY id")
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["modules"] = json.loads(item.pop("modules_json", "[]") or "[]")
            except Exception:
                item["modules"] = []
            try:
                item["weekdays"] = [int(x) for x in str(item.get("weekdays") or "").split(",") if x != ""]
            except Exception:
                item["weekdays"] = []
            out.append(item)
        return out

    def get_schedule(self, schedule_id: int) -> dict | None:
        for item in self.list_schedules():
            if int(item["id"]) == int(schedule_id):
                return item
        return None

    def create_schedule(self, name: str, time_text: str, weekdays: list[int], modules: list[str],
                        details: bool, enabled: bool, next_run_at: str) -> int:
        ts = now_str()
        cur = self.execute(
            """INSERT INTO schedules(name,enabled,time,weekdays,modules_json,details,
               created_at,updated_at,next_run_at,last_status)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                name, 1 if enabled else 0, time_text,
                ",".join(str(int(d)) for d in weekdays),
                json_dumps(modules), 1 if details else 0,
                ts, ts, next_run_at, "等待执行",
            ),
        )
        return int(cur.lastrowid)

    def update_schedule(self, schedule_id: int, **fields: Any) -> None:
        allowed = {
            "name", "enabled", "time", "weekdays", "details", "next_run_at",
            "last_run_at", "last_status", "last_message",
        }
        parts, params = [], []
        for key, value in fields.items():
            if key == "modules":
                parts.append("modules_json=?")
                params.append(json_dumps(value))
            elif key == "weekdays":
                parts.append("weekdays=?")
                params.append(",".join(str(int(d)) for d in value))
            elif key in allowed:
                parts.append(f"{key}=?")
                params.append(1 if key == "enabled" and value else (0 if key == "enabled" else value))
        if not parts:
            return
        parts.append("updated_at=?")
        params.append(now_str())
        params.append(schedule_id)
        self.execute(f"UPDATE schedules SET {', '.join(parts)} WHERE id=?", params)

    def delete_schedule(self, schedule_id: int) -> None:
        self.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))

    # -------------------------------------------------------------------- tasks
    def upsert_task(self, drama_id: int, ep: int, quality: str, force: bool = False) -> int | None:
        ts = now_str()
        with self.lock:
            row = self.conn.execute(
                "SELECT id,state FROM tasks WHERE drama_id=? AND ep=?", (drama_id, ep)
            ).fetchone()
            if row is None:
                cur = self.conn.execute(
                    """INSERT INTO tasks(drama_id,ep,quality,state,created_at,updated_at)
                       VALUES(?,?,?,'queued',?,?)""",
                    (drama_id, ep, quality, ts, ts),
                )
                self.conn.commit()
                return int(cur.lastrowid)
            if row["state"] in ("queued", "downloading"):
                return None
            if row["state"] == "done" and not force:
                return None
            self.conn.execute(
                """UPDATE tasks SET quality=?,state='queued',progress=0,speed=0,size=0,error='',
                   updated_at=?,started_at=NULL,finished_at=NULL WHERE id=?""",
                (quality, ts, row["id"]),
            )
            self.conn.commit()
            return int(row["id"])

    def get_task(self, task_id: int) -> sqlite3.Row | None:
        return self.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def update_task(self, task_id: int, **fields: Any) -> None:
        allowed = {"state", "progress", "speed", "size", "error", "started_at", "finished_at", "quality"}
        parts, params = [], []
        for key, value in fields.items():
            if key in allowed:
                parts.append(f"{key}=?")
                params.append(value)
        if not parts:
            return
        parts.append("updated_at=?")
        params.append(now_str())
        params.append(task_id)
        self.execute(f"UPDATE tasks SET {', '.join(parts)} WHERE id=?", params)

    def list_tasks(self, states: list[str] | None = None, limit: int = 300) -> list[dict]:
        sql = """SELECT t.*, d.title AS drama_title, d.site_id, d.module
                 FROM tasks t JOIN dramas d ON d.id=t.drama_id"""
        params: list[Any] = []
        if states:
            sql += f" WHERE t.state IN ({','.join('?' for _ in states)})"
            params.extend(states)
        sql += " ORDER BY CASE t.state WHEN 'downloading' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END, t.id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.query(sql, params)]

    def active_tasks(self) -> list[dict]:
        return self.list_tasks(["queued", "downloading"], limit=500)

    def clear_finished_tasks(self) -> None:
        self.execute("DELETE FROM tasks WHERE state IN ('done','canceled')")

    def clear_tasks(self, states: list[str]) -> None:
        allowed = {"done", "canceled", "failed"}
        states = [s for s in states if s in allowed]
        if not states:
            return
        self.execute(f"DELETE FROM tasks WHERE state IN ({','.join('?' for _ in states)})", states)

    def reset_running_tasks(self) -> None:
        self.execute("UPDATE tasks SET state='queued' WHERE state='downloading'")

    # ------------------------------------------------------------------ lists
    def list_dramas(
        self,
        module: str = "all",
        q: str = "",
        status: str = "all",
        tag: str = "",
        sort: str = "updated",
        page: int = 1,
        per_page: int = 40,
        score_min: float = 0,
        year: int = 0,
        site_tag: str = "",
        source: str = "",
        include_duplicates: bool = False,
    ) -> dict:
        where: list[str] = []
        params: list[Any] = []
        if not include_duplicates:
            where.append("x.is_duplicate=0")
        else:
            excluded_clause, excluded_params = self._excluded_clause("x")
            if excluded_clause:
                where.append(excluded_clause)
                params.extend(excluded_params)
        if source in SOURCES:
            where.append("x.source=?")
            params.append(source)
        if module in MODULES:
            where.append("EXISTS (SELECT 1 FROM json_each(x.modules_json) jm WHERE jm.value=?)")
            params.append(module)
        if q:
            where.append(
                "(x.title LIKE ? OR x.tags_json LIKE ? OR EXISTS ("
                "SELECT 1 FROM drama_tags dt JOIN tags t ON t.id=dt.tag_id "
                "WHERE dt.drama_id=x.id AND t.name LIKE ?))"
            )
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if status == "done":
            where.append("x.total_ep>0 AND x.done_ep>=x.total_ep")
        elif status == "partial":
            where.append("x.done_ep>0 AND x.done_ep<x.total_ep")
        elif status == "none":
            where.append("x.done_ep=0")
        elif status == "update":
            where.append("x.has_update=1")
        elif status == "downloading":
            where.append("x.active_tasks>0")
        elif status == "favorite":
            where.append("x.favorite=1")
        elif status == "subscribed":
            where.append("x.subscribed_flag>0")
        if tag:
            where.append(
                "EXISTS (SELECT 1 FROM drama_tags dt JOIN tags t ON t.id=dt.tag_id "
                "WHERE dt.drama_id=x.id AND t.name=?)"
            )
            params.append(tag)
        if site_tag:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(x.tags_json) je WHERE je.value=?)"
            )
            params.append(site_tag)
        if score_min:
            where.append("COALESCE(x.score,0)>=?")
            params.append(float(score_min))
        if year:
            where.append("x.year=?")
            params.append(int(year))

        sort_sql = {
            "updated": "x.last_scraped_at DESC, x.id DESC",
            "added": "x.id DESC",
            "score": "COALESCE(x.score,0) DESC, x.id DESC",
            "episodes": "x.total_ep DESC, x.id DESC",
            "title": "x.title COLLATE NOCASE ASC",
            "progress": "CASE WHEN x.total_ep>0 THEN x.done_ep*1.0/x.total_ep ELSE 0 END DESC, x.id DESC",
        }.get(sort, "x.last_scraped_at DESC, x.id DESC")

        base_table = "dramas" if include_duplicates else "visible_dramas"
        base = f"""
            SELECT d.*,
                (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id AND e.state='done') AS done_ep,
                (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id) AS ep_rows,
                (SELECT COUNT(*) FROM tasks t WHERE t.drama_id=d.id AND t.state IN ('queued','downloading')) AS active_tasks,
                (SELECT COUNT(*) FROM subscriptions s WHERE s.drama_id=d.id AND s.enabled=1) AS subscribed_flag,
                (SELECT s.quality FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_quality,
                (SELECT s.auto_download FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_auto
            FROM {base_table} d
        """
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        count_row = self.query_one(f"SELECT COUNT(*) AS n FROM ({base}) x{where_sql}", params)
        total = int(count_row["n"]) if count_row else 0
        per_page = max(1, min(200, per_page))
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        offset = (page - 1) * per_page
        rows = self.query(
            f"SELECT * FROM ({base}) x{where_sql} ORDER BY {sort_sql} LIMIT ? OFFSET ?",
            [*params, per_page, offset],
        )
        items = [self._drama_dict(r) for r in rows]
        tag_map = self.tags_for_dramas([int(r["id"]) for r in rows])
        for item in items:
            item["tags"] = tag_map.get(item["id"], [])
        return {"items": items, "total": total, "page": page, "pages": pages, "per_page": per_page}

    def list_drama_ids(
        self,
        module: str = "all",
        q: str = "",
        status: str = "all",
        tag: str = "",
        sort: str = "updated",
        score_min: float = 0,
        year: int = 0,
        site_tag: str = "",
        source: str = "",
        include_duplicates: bool = False,
    ) -> list[int]:
        """返回当前筛选条件下的全部剧集 ID，供“全选筛选结果”使用。"""
        ids: list[int] = []
        page = 1
        while True:
            data = self.list_dramas(
                module=module, q=q, status=status, tag=tag, sort=sort,
                page=page, per_page=200, score_min=score_min, year=year,
                site_tag=site_tag, source=source,
                include_duplicates=include_duplicates,
            )
            ids.extend(int(item["id"]) for item in data.get("items", []))
            if page >= int(data.get("pages") or 1):
                break
            page += 1
        return ids

    def get_drama_detail(self, drama_id: int) -> dict | None:
        row = self.query_one(
            """SELECT d.*,
                (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id AND e.state='done') AS done_ep,
                (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id) AS ep_rows,
                (SELECT COUNT(*) FROM subscriptions s WHERE s.drama_id=d.id AND s.enabled=1) AS subscribed_flag,
                (SELECT s.quality FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_quality,
                (SELECT s.auto_download FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_auto
               FROM dramas d WHERE d.id=?""",
            (drama_id,),
        )
        if not row:
            return None
        data = self._drama_dict(row)
        data["tag_excluded"] = self.is_tag_excluded(drama_id)
        eps = [dict(e) for e in self.get_episodes(drama_id)]
        for e in eps:
            e["file_name"] = e["file_path"].replace("\\", "/").rsplit("/", 1)[-1] if e["file_path"] else ""
            if e.get("cover_path"):
                e["thumb"] = f"/covers/episodes/{data.get('site_id')}_{e['ep']}.jpg"
            else:
                e["thumb"] = ""
        data["episodes"] = eps
        data["tags"] = self.tags_for_drama(drama_id)
        if data.get("is_duplicate") and data.get("duplicate_of"):
            preferred = self.get_drama(int(data["duplicate_of"]))
            if preferred:
                data["duplicate_preferred"] = {
                    "id": int(preferred["id"]),
                    "title": preferred["title"],
                    "total_ep": int(preferred["total_ep"] or 0),
                    "source": preferred["source"] or "huangguo",
                }
        data["tasks"] = [
            dict(t) for t in self.query(
                "SELECT * FROM tasks WHERE drama_id=? AND state IN ('queued','downloading')", (drama_id,)
            )
        ]
        return data

    def _drama_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["tags_site"] = json.loads(d.pop("tags_json", "[]") or "[]")
        except Exception:
            d["tags_site"] = []
        try:
            d["badges"] = json.loads(d.pop("badges_json", "[]") or "[]")
        except Exception:
            d["badges"] = []
        try:
            d["modules"] = json.loads(d.pop("modules_json", "[]") or "[]")
        except Exception:
            d["modules"] = []
        if not d["modules"]:
            d["modules"] = [d.get("module", "")]
        d["module"] = d.get("module") or d["modules"][0]
        d["module_label"] = MODULES.get(d.get("module", ""), {}).get("label", d.get("module", ""))
        d["module_labels"] = [MODULES.get(m, {}).get("label", m) for m in d["modules"]]
        d["source"] = d.get("source") or "huangguo"
        d["source_label"] = SOURCES.get(d["source"], {}).get("label", d["source"])
        raw_site_id = str(d.get("site_id") or "")
        if d["source"] == "yeguo" and raw_site_id.startswith("yeguo_"):
            raw_site_id = raw_site_id[len("yeguo_"):]
        d["raw_site_id"] = raw_site_id
        d["is_duplicate"] = bool(d.get("is_duplicate"))
        d["duplicate_of"] = int(d.get("duplicate_of") or 0)
        d["manual_preferred"] = bool(d.get("manual_preferred"))
        d["keep_separate"] = bool(d.get("keep_separate"))
        d["subscribed"] = bool(d.pop("subscribed_flag", 0))
        d["sub_quality"] = d.pop("sub_quality", "") or ""
        d["sub_auto"] = bool(d.pop("sub_auto", 1))
        total = int(d.get("total_ep") or 0)
        done = int(d.get("done_ep") or 0)
        d["done_ep"] = done
        if total > 0 and done >= total:
            d["download_state"] = "done"
        elif done > 0:
            d["download_state"] = "partial"
        elif d.get("active_tasks"):
            d["download_state"] = "downloading"
        else:
            d["download_state"] = "none"
        d["cover"] = f"/covers/{d['site_id']}.jpg" if d.get("cover_path") else (d.get("cover_url") or "")
        return d

    def updates(self, limit: int = 100) -> list[dict]:
        rows = self.query(
            """SELECT d.*,
                (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id AND e.state='done') AS done_ep,
                (SELECT COUNT(*) FROM subscriptions s WHERE s.drama_id=d.id AND s.enabled=1) AS subscribed_flag,
                (SELECT s.quality FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_quality,
                (SELECT s.auto_download FROM subscriptions s WHERE s.drama_id=d.id LIMIT 1) AS sub_auto
               FROM visible_dramas d WHERE d.has_update=1
               ORDER BY d.updated_at DESC LIMIT ?""",
            (limit,),
        )
        items = [self._drama_dict(r) for r in rows]
        tag_map = self.tags_for_dramas([int(r["id"]) for r in rows])
        for item in items:
            item["tags"] = tag_map.get(item["id"], [])
        return items

    def stats(self, ttl: float = 2.0) -> dict:
        now_mono = time.monotonic()
        if ttl > 0:
            with self._stats_lock:
                cached = self._stats_cache
                if cached and now_mono - cached[0] < ttl:
                    return copy.deepcopy(cached[1])
        total = int(self.query_one("SELECT COUNT(*) n FROM visible_dramas")["n"])
        by_module = {
            r["module"]: int(r["n"])
            for r in self.query(
                "SELECT jm.value AS module, COUNT(*) AS n "
                "FROM visible_dramas d, json_each(d.modules_json) jm GROUP BY jm.value"
            )
        }
        by_source = {
            r["source"]: int(r["n"])
            for r in self.query(
                "SELECT source, COUNT(*) AS n FROM visible_dramas GROUP BY source"
            )
        }
        status_row = self.query_one(
            """SELECT
                 COALESCE(SUM(CASE WHEN active_tasks>0 THEN 1 ELSE 0 END),0) AS downloading,
                 COALESCE(SUM(CASE WHEN active_tasks=0 AND total_ep>0 AND done_ep>=total_ep THEN 1 ELSE 0 END),0) AS done,
                 COALESCE(SUM(CASE WHEN active_tasks=0 AND done_ep>0 AND done_ep<total_ep THEN 1 ELSE 0 END),0) AS partial,
                 COALESCE(SUM(CASE WHEN active_tasks=0 AND done_ep=0 THEN 1 ELSE 0 END),0) AS none,
                 COALESCE(SUM(CASE WHEN has_update=1 THEN 1 ELSE 0 END),0) AS upd,
                 COALESCE(SUM(CASE WHEN favorite=1 THEN 1 ELSE 0 END),0) AS favorite,
                 COALESCE(SUM(total_ep),0) AS total_ep_sum
               FROM (
                 SELECT d.id,d.total_ep,d.has_update,d.favorite,
                   (SELECT COUNT(*) FROM episodes e WHERE e.drama_id=d.id AND e.state='done') AS done_ep,
                   (SELECT COUNT(*) FROM tasks t WHERE t.drama_id=d.id AND t.state IN ('queued','downloading')) AS active_tasks
                 FROM visible_dramas d
               ) x"""
        )
        status = {
            "all": total,
            "downloading": int(status_row["downloading"] or 0),
            "done": int(status_row["done"] or 0),
            "partial": int(status_row["partial"] or 0),
            "none": int(status_row["none"] or 0),
            "update": int(status_row["upd"] or 0),
            "favorite": int(status_row["favorite"] or 0),
        }
        done = status["done"]
        partial = status["partial"]
        updates = status["update"]
        episodes_done = int(self.query_one(
            "SELECT COUNT(*) n FROM episodes e JOIN visible_dramas d ON d.id=e.drama_id "
            "WHERE e.state='done'"
        )["n"])
        episodes_total = int(self.query_one(
            "SELECT COUNT(*) n FROM episodes e JOIN visible_dramas d ON d.id=e.drama_id"
        )["n"])
        storage = int(self.query_one("SELECT COALESCE(SUM(file_size),0) n FROM episodes WHERE state='done'")["n"])
        active = int(self.query_one("SELECT COUNT(*) n FROM tasks WHERE state IN ('queued','downloading')")["n"])
        subscribed = int(self.query_one(
            "SELECT COUNT(*) n FROM subscriptions s JOIN visible_dramas d ON d.id=s.drama_id "
            "WHERE s.enabled=1"
        )["n"])
        sub_updates = int(self.query_one(
            "SELECT COUNT(*) n FROM visible_dramas d WHERE d.has_update=1 AND EXISTS "
            "(SELECT 1 FROM subscriptions s WHERE s.drama_id=d.id AND s.enabled=1)"
        )["n"])
        duplicates = int(self.query_one(
            "SELECT COUNT(*) n FROM dramas WHERE is_duplicate=1"
        )["n"])
        status["subscribed"] = subscribed
        last_row = self.query_one("SELECT MAX(last_scraped_at) AS t FROM dramas")
        years = [
            {"year": int(r["year"]), "count": int(r["n"])}
            for r in self.query(
                "SELECT year, COUNT(*) n FROM visible_dramas WHERE year IS NOT NULL "
                "GROUP BY year ORDER BY year DESC LIMIT 12"
            )
        ]
        site_tags = [
            {"name": r["name"], "count": int(r["count"])}
            for r in self.query(
                """SELECT je.value AS name, COUNT(*) AS count
                   FROM visible_dramas d, json_each(d.tags_json) je
                   WHERE d.tags_json IS NOT NULL AND d.tags_json <> ''
                   GROUP BY je.value ORDER BY count DESC, je.value LIMIT 48"""
            )
        ]
        result = {
            "total": total,
            "by_module": by_module,
            "by_source": by_source,
            "duplicates": duplicates,
            "cross_listed": max(0, sum(by_module.values()) - total),
            "status": status,
            "done": done,
            "partial": partial,
            "updates": updates,
            "episodes_done": episodes_done,
            "episodes_total": episodes_total,
            "total_ep_sum": int(status_row["total_ep_sum"] or 0),
            "storage_bytes": storage,
            "active_tasks": active,
            "subscriptions": subscribed,
            "subscription_updates": sub_updates,
            "last_scraped_at": (last_row["t"] if last_row and last_row["t"] else ""),
            "years": years,
            "site_tags": site_tags,
        }
        with self._stats_lock:
            self._stats_cache = (now_mono, copy.deepcopy(result))
        return result

    def create_scan_run(self, modules: list[str], details: bool, max_pages: int | None,
                        refresh: bool, resumed_from: int = 0) -> int:
        options = {
            "modules": modules,
            "details": bool(details),
            "max_pages": max_pages,
            "refresh": bool(refresh),
        }
        cur = self.execute(
            """INSERT INTO scan_runs(module,state,message,options_json,resumed_from,started_at)
               VALUES(?,?,?,?,?,?)""",
            (",".join(modules), "running", "准备开始", json_dumps(options), int(resumed_from), now_str()),
        )
        return int(cur.lastrowid)

    def finish_scan_run(self, run_id: int, state: str, found: int, new_count: int,
                        updated_count: int, message: str) -> None:
        self.execute(
            """UPDATE scan_runs SET state=?,found=?,new_count=?,updated_count=?,message=?,finished_at=?
               WHERE id=?""",
            (state, int(found), int(new_count), int(updated_count), message, now_str(), int(run_id)),
        )

    def mark_interrupted_scan_runs(self) -> None:
        self.execute(
            "UPDATE scan_runs SET state='interrupted',message='程序重启前扫描未完成',finished_at=? "
            "WHERE state='running'",
            (now_str(),),
        )

    def list_scan_runs(self, limit: int = 50) -> list[dict]:
        rows = self.query("SELECT * FROM scan_runs ORDER BY id DESC LIMIT ?", (int(limit),))
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["options"] = json.loads(item.pop("options_json", "{}") or "{}")
            except Exception:
                item["options"] = {}
            out.append(item)
        return out

    def get_scan_run(self, run_id: int) -> dict | None:
        for item in self.list_scan_runs(500):
            if int(item["id"]) == int(run_id):
                return item
        return None

    def recent_activity(self, limit: int = 20) -> list[dict]:
        rows = self.query(
            """SELECT e.downloaded_at, e.ep, e.file_size, d.id AS drama_id, d.title, d.site_id, d.module
               FROM episodes e JOIN dramas d ON d.id=e.drama_id
               WHERE e.state='done' AND e.downloaded_at IS NOT NULL
               ORDER BY e.downloaded_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

