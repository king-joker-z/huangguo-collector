"""已下载分集的封面补全（从视频抽帧，写 Jellyfin -thumb.jpg）。"""
from __future__ import annotations

import threading

from . import nfo
from .config import module_group
from .db import Database, now_str
from .groupctx import reset_group, set_group
from .settings import Settings


class CoverBackfillManager:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.job = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {
            "state": "idle",
            "total": 0,
            "done": 0,
            "filled": 0,
            "failed": 0,
            "message": "",
            "started_at": "",
            "finished_at": "",
        }

    def status(self) -> dict:
        with self.lock:
            return dict(self.job)

    def start(self, drama_ids: list[int] | None = None, force: bool = False,
              group: str = "") -> tuple[bool, str]:
        with self.lock:
            if self.job.get("state") == "running":
                return False, "封面补全任务正在进行中"
            rows = self._collect(drama_ids, group)
            self.job = self._idle()
            self.job.update(
                {
                    "state": "running",
                    "total": len(rows),
                    "message": f"准备补全 {len(rows)} 集封面",
                    "started_at": now_str(),
                }
            )
            self.thread = threading.Thread(
                target=self._run, args=(rows, force), name="cover-backfill", daemon=True
            )
            self.thread.start()
        return True, f"开始补全 {len(rows)} 集封面"

    def _collect(self, drama_ids: list[int] | None, group: str = "") -> list[dict]:
        params: list = []
        table = self.db.scope_table(group)
        sql = f"""SELECT e.drama_id,e.ep,e.file_path,e.duration,e.cover_path
                 FROM episodes e JOIN {table} d ON d.id=e.drama_id WHERE e.state='done'"""
        if drama_ids:
            sql += f" AND e.drama_id IN ({','.join('?' for _ in drama_ids)})"
            params.extend(drama_ids)
        sql += " ORDER BY e.drama_id, e.ep"
        return [dict(row) for row in self.db.query(sql, params)]

    def _run(self, rows: list[dict], force: bool) -> None:
        filled = failed = 0
        try:
            for index, row in enumerate(rows, start=1):
                drama = self.db.get_drama(int(row["drama_id"]))
                token = set_group(module_group(str(drama["module"] or ""))) if drama else None
                try:
                    episode = self.db.get_episode(int(row["drama_id"]), int(row["ep"]))
                    if not drama or not episode:
                        failed += 1
                    else:
                        try:
                            target = nfo.generate_episode_thumb(
                                self.settings, dict(drama), dict(episode), row.get("file_path")
                            )
                            if target:
                                self.db.set_episode_cover(int(row["drama_id"]), int(row["ep"]), target)
                                nfo.write_episode_nfo(self.settings, dict(drama), dict(episode))
                                filled += 1
                            else:
                                failed += 1
                        except Exception:
                            failed += 1
                finally:
                    if token is not None:
                        reset_group(token)
                with self.lock:
                    self.job.update(
                        done=index,
                        filled=filled,
                        failed=failed,
                        message=f"补全集封面 {index}/{len(rows)}",
                    )
            with self.lock:
                self.job.update(
                    state="done",
                    message=f"完成：补全 {filled} 集，失败 {failed} 集",
                    finished_at=now_str(),
                )
        except Exception as exc:
            with self.lock:
                self.job.update(
                    state="error",
                    message=f"补全异常：{exc}",
                    finished_at=now_str(),
                )
