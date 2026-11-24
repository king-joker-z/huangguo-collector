"""定时全量扫库调度器。"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from .db import Database, now_str
from .scan import ScanManager
from .settings import Settings


def parse_hhmm(text: str) -> tuple[int, int] | None:
    try:
        hour, minute = str(text).strip().split(":", 1)
        hour_i, minute_i = int(hour), int(minute)
        if 0 <= hour_i <= 23 and 0 <= minute_i <= 59:
            return hour_i, minute_i
    except (ValueError, AttributeError):
        pass
    return None


def next_run_at(time_text: str, weekdays: list[int], after: datetime | None = None) -> datetime | None:
    parsed = parse_hhmm(time_text)
    if not parsed:
        return None
    hour, minute = parsed
    base = after or datetime.now()
    days = {int(d) for d in (weekdays or []) if 0 <= int(d) <= 6}
    if not days:
        days = set(range(7))
    for offset in range(0, 9):
        day = base.date() + timedelta(days=offset)
        if day.weekday() not in days:
            continue
        candidate = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
        if candidate > base + timedelta(seconds=5):
            return candidate
    return None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


class Scheduler:
    def __init__(self, db: Database, settings: Settings, scan: ScanManager) -> None:
        self.db = db
        self.settings = settings
        self.scan = scan
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.active_schedule_id: int | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self.thread:
            return
        self._bootstrap()
        self.thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def _bootstrap(self) -> None:
        now = datetime.now()
        for schedule in self.db.list_schedules():
            if schedule.get("last_status") == "running":
                self.db.update_schedule(
                    schedule["id"],
                    last_status="已中断",
                    last_message="程序重启前任务未完成",
                )
            if not schedule.get("enabled"):
                continue
            current = parse_datetime(schedule.get("next_run_at"))
            if current is None or current <= now:
                target = next_run_at(schedule["time"], schedule.get("weekdays") or [])
                self.db.update_schedule(schedule["id"], next_run_at=format_datetime(target))

    # --------------------------------------------------------------------- refresh
    def refresh(self, schedule_id: int) -> None:
        schedule = self.db.get_schedule(schedule_id)
        if not schedule:
            return
        target = next_run_at(schedule["time"], schedule.get("weekdays") or [])
        self.db.update_schedule(schedule_id, next_run_at=format_datetime(target))

    # ------------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self.stop_event.wait(15)

    def tick(self) -> None:
        with self.lock:
            if self.active_schedule_id is not None:
                status = self.scan.status()
                if status.get("state") not in ("running", "idle"):
                    self.db.update_schedule(
                        self.active_schedule_id,
                        last_status=status.get("state") or "",
                        last_message=status.get("message") or "",
                    )
                    self.active_schedule_id = None
                else:
                    return

        if self.scan.status().get("state") == "running":
            now = datetime.now()
            for schedule in self.db.list_schedules():
                if not schedule.get("enabled"):
                    continue
                due = parse_datetime(schedule.get("next_run_at"))
                if due is None or due > now:
                    continue
                target = next_run_at(schedule["time"], schedule.get("weekdays") or [], now + timedelta(minutes=1))
                self.db.update_schedule(
                    schedule["id"],
                    next_run_at=format_datetime(target),
                    last_status="跳过",
                    last_message="已有扫描任务在执行，顺延到下一次",
                )
            return
        now = datetime.now()
        for schedule in self.db.list_schedules():
            if not schedule.get("enabled"):
                continue
            due = parse_datetime(schedule.get("next_run_at"))
            if due is None or due > now:
                continue
            modules = schedule.get("modules") or ["original", "duanju", "manju", "yeguo"]
            started, message = self.scan.start(modules, details=bool(schedule.get("details", 1)), refresh=False)
            if not started:
                return
            target = next_run_at(schedule["time"], schedule.get("weekdays") or [], datetime.now() + timedelta(minutes=1))
            self.db.update_schedule(
                schedule["id"],
                last_run_at=now_str(),
                next_run_at=format_datetime(target),
                last_status="running",
                last_message="定时任务已启动全量扫库",
            )
            with self.lock:
                self.active_schedule_id = int(schedule["id"])
            return

    # ---------------------------------------------------------------------- manual
    def run_now(self, schedule_id: int) -> tuple[bool, str]:
        schedule = self.db.get_schedule(schedule_id)
        if not schedule:
            return False, "定时任务不存在"
        modules = schedule.get("modules") or ["original", "duanju", "manju", "yeguo"]
        started, message = self.scan.start(modules, details=bool(schedule.get("details", 1)), refresh=False)
        if not started:
            return False, message
        target = next_run_at(schedule["time"], schedule.get("weekdays") or [], datetime.now() + timedelta(minutes=1))
        self.db.update_schedule(
            schedule_id,
            last_run_at=now_str(),
            next_run_at=format_datetime(target),
            last_status="running",
            last_message="手动执行定时任务",
        )
        with self.lock:
            self.active_schedule_id = int(schedule_id)
        return True, "已开始执行"

