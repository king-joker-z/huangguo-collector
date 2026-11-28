"""全量/增量扫描：遍历模块分页，写入列表并同步详情与分集。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .actors import ensure_avatars
from .config import MODULES, module_group
from .creators import skip_short_video
from .db import Database, now_str
from .groupctx import reset_group, set_group
from .scraper import Scraper, ScrapeError
from .settings import Settings


def _chunks(items: list, size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


TAB_VARIANTS = {
    "original": [("latest", "最近更新")],
    "duanju": [("latest", "最近更新"), ("hot", "当前热播"), ("random", "精选推荐")],
    "manju": [("latest", "最近更新"), ("hot", "当前热播"), ("random", "精选推荐")],
    "yeguo": [("latest", "全部剧集")],
}


class ScanManager:
    def __init__(self, db: Database, settings: Settings, scrapers, downloads=None,
                 translator=None) -> None:
        self.db = db
        self.settings = settings
        if isinstance(scrapers, dict):
            self.scrapers = scrapers
        else:
            self.scrapers = {"huangguo": scrapers}
        self.scraper = self.scrapers.get("huangguo")
        self.downloads = downloads
        self.translator = translator
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.job = self._idle()
        self.db.mark_interrupted_scan_runs()

    @staticmethod
    def _idle() -> dict:
        return {
            "state": "idle",
            "modules": [],
            "module": "",
            "module_label": "",
            "pages": 0,
            "done_pages": 0,
            "found": 0,
            "new_count": 0,
            "updated_count": 0,
            "detail_total": 0,
            "detail_done": 0,
            "message": "",
            "started_at": "",
            "finished_at": "",
            "errors": [],
            "run_id": 0,
        }

    def status(self) -> dict:
        with self.lock:
            data = dict(self.job)
            data["errors"] = list(self.job.get("errors") or [])
            return data

    def start(self, modules: list[str], details: bool = True, max_pages: int | None = None,
              refresh: bool = False, resumed_from: int = 0, tab: str = "") -> tuple[bool, str]:
        modules = [m for m in modules if m in MODULES]
        if not modules:
            return False, "没有有效的模块"
        with self.lock:
            if self.job.get("state") == "running":
                return False, "已有扫描任务在进行中"
            self.cancel_event.clear()
            run_id = self.db.create_scan_run(modules, details, max_pages, refresh, resumed_from)
            self.job = self._idle()
            self.job.update(
                {
                    "state": "running",
                    "modules": modules,
                    "module": modules[0],
                    "module_label": MODULES[modules[0]]["label"],
                    "message": "继续上次扫描（已完成详情会跳过）" if resumed_from else "准备开始",
                    "started_at": now_str(),
                    "run_id": run_id,
                }
            )
            self.thread = threading.Thread(
                target=self._run, args=(modules, details, max_pages, refresh, tab), name="scan", daemon=True
            )
            self.thread.start()
        return True, "扫描已开始"

    def resume(self, run_id: int) -> tuple[bool, str]:
        run = self.db.get_scan_run(run_id)
        if not run:
            return False, "扫描记录不存在"
        options = run.get("options") or {}
        modules = options.get("modules") or ["original", "duanju", "manju"]
        return self.start(
            modules,
            details=bool(options.get("details", True)),
            max_pages=options.get("max_pages"),
            refresh=False,
            resumed_from=int(run_id),
            tab=str(options.get("tab") or ""),
        )

    def cancel(self) -> None:
        self.cancel_event.set()
        with self.lock:
            if self.job.get("state") == "running":
                self.job["message"] = "正在停止…"

    def _update(self, **fields) -> None:
        with self.lock:
            self.job.update(fields)

    def _error(self, message: str) -> None:
        with self.lock:
            errors = list(self.job.get("errors") or [])
            errors.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
            self.job["errors"] = errors[-30:]

    def _finish(self, state: str, message: str) -> None:
        with self.lock:
            self.job.update(state=state, message=message, finished_at=now_str())
            run_id = int(self.job.get("run_id") or 0)
            found = int(self.job.get("found") or 0)
            new_count = int(self.job.get("new_count") or 0)
            updated = int(self.job.get("updated_count") or 0)
        if run_id:
            self.db.finish_scan_run(run_id, state, found, new_count, updated, message)

    def _run(self, modules: list[str], details: bool, max_pages: int | None,
             refresh: bool, scan_tab: str = "") -> None:
        seen_detail_ids: set[int] = set()
        token = None
        try:
            for module in modules:
                if self.cancel_event.is_set():
                    break
                token = set_group(module_group(module))
                label = MODULES[module]["label"]
                source = MODULES[module].get("source", "huangguo")
                scraper = self.scrapers.get(source)
                if scraper is None:
                    self._error(f"{label} 未配置采集器")
                    continue
                tabs = TAB_VARIANTS.get(module, [("latest", "最近更新")])
                # 适配器可以动态提供列表（欧美影视 = 演员订阅）
                list_tabs = getattr(scraper, "list_tabs", None)
                if callable(list_tabs):
                    tabs = list_tabs(module) or []
                if scan_tab:
                    tabs = [(key, name) for key, name in tabs if key == scan_tab]
                if not tabs:
                    hint = "，先到「演员订阅」添加博主" if source == "ph" else ""
                    self._error(f"{label} 没有可采集的列表{hint}")
                    continue
                self._update(module=module, module_label=label, message=f"读取「{label}」分页…")
                tab_pages: list[tuple[str, str, int]] = []
                try:
                    for tab, tab_label in tabs:
                        pages = scraper.page_count(module, tab)
                        if max_pages:
                            pages = min(pages, max_pages)
                        tab_pages.append((tab, tab_label, pages))
                except ScrapeError as exc:
                    self._error(f"{label} 分页失败: {exc}")
                    continue
                total_pages = sum(p for _, _, p in tab_pages)
                self._update(
                    pages=total_pages,
                    done_pages=0,
                    detail_total=0,
                    detail_done=0,
                    message=f"采集「{label}」共 {total_pages} 页（{len(tab_pages)} 个列表）",
                )

                detail_ids: list[int] = []
                done_pages = 0
                for tab, tab_label, pages in tab_pages:
                    tab_new = 0
                    for page in range(1, pages + 1):
                        if self.cancel_event.is_set():
                            break
                        self._update(
                            message=f"「{label} · {tab_label}」{page}/{pages} 页"
                        )
                        try:
                            cards, _ = scraper.fetch_listing_page(module, page, tab)
                        except ScrapeError as exc:
                            self._error(f"{label} {tab_label} 第{page}页: {exc}")
                            done_pages += 1
                            self._update(done_pages=done_pages)
                            continue
                        for card in cards:
                            try:
                                drama_id, is_new, has_update = self.db.upsert_card(card)
                            except Exception as exc:
                                self._error(f"写入卡片失败 {card.get('site_id')}: {exc}")
                                continue
                            with self.lock:
                                self.job["found"] += 1
                                if is_new:
                                    self.job["new_count"] += 1
                                    tab_new += 1
                                if has_update:
                                    self.job["updated_count"] += 1
                            if details and (
                                is_new
                                or has_update
                                or refresh
                                or self.db.need_detail(card["site_id"])
                            ) and (drama_id not in seen_detail_ids or has_update):
                                seen_detail_ids.add(drama_id)
                                detail_ids.append(drama_id)
                        done_pages += 1
                        self._update(done_pages=done_pages)
                    if not self.cancel_event.is_set():
                        self._mark_tab_scanned(scraper, module, tab, tab_new)
                    if self.cancel_event.is_set():
                        break

                if details and detail_ids:
                    detail_ids = list(dict.fromkeys(detail_ids))
                    self._update(
                        detail_total=len(detail_ids),
                        detail_done=0,
                        message=f"「{label}」同步详情 0/{len(detail_ids)}",
                    )
                    workers = max(1, min(12, self.settings.get_int(
                        "scan_workers", 6, group=module_group(module)
                    )))
                    for chunk in _chunks(detail_ids, 24):
                        if self.cancel_event.is_set():
                            break
                        with ThreadPoolExecutor(max_workers=workers) as executor:
                            futures = {
                                executor.submit(self._detail_one, drama_id, refresh): drama_id
                                for drama_id in chunk
                            }
                            for future in as_completed(futures):
                                if self.cancel_event.is_set():
                                    continue
                                try:
                                    future.result()
                                except Exception as exc:
                                    self._error(f"详情失败 #{futures[future]}: {exc}")
                                with self.lock:
                                    self.job["detail_done"] += 1
                                    self.job["message"] = (
                                        f"「{label}」同步详情 "
                                        f"{self.job['detail_done']}/{self.job['detail_total']}"
                                    )

            if self.cancel_event.is_set():
                self._finish("canceled", "扫描已停止，可到扫描历史继续")
            else:
                self._finish(
                    "done",
                    f"完成：发现 {self.job['found']} 部，新增 {self.job['new_count']} 部，"
                    f"更新 {self.job['updated_count']} 部",
                )
        except Exception as exc:
            self._error(f"扫描异常: {exc}")
            self._finish("error", f"扫描异常: {exc}")
        finally:
            if token is not None:
                reset_group(token)

    def _detail_one(self, drama_id: int, refresh: bool) -> None:
        row = self.db.get_drama(drama_id)
        if not row:
            return
        source = row["source"] if "source" in row.keys() else "huangguo"
        scraper = self.scrapers.get(source) or self.scraper
        if scraper is None:
            raise RuntimeError(f"未配置站点采集器: {source}")
        token = set_group(module_group(str(row["module"] or "")))
        try:
            detail = scraper.fetch_detail(str(row["site_id"]))
            ensure_avatars(scraper, detail)
            _, has_update = self.db.apply_detail(str(row["site_id"]), detail)
            if has_update:
                with self.lock:
                    self.job["updated_count"] += 1
            if refresh or not row["cover_path"]:
                try:
                    scraper.save_cover(str(row["site_id"]), detail.get("cover_url") or "")
                except Exception:
                    pass
            if self.translator is not None:
                self.translator.maybe_translate_drama(drama_id)
            self._auto_download(drama_id)
        finally:
            reset_group(token)

    def _mark_tab_scanned(self, scraper, module: str, tab: str, new_count: int) -> None:
        """演员订阅的列表扫完后回写统计（影片数 / 上次扫描）。"""
        creator_for_tab = getattr(scraper, "creator_for_tab", None)
        if not callable(creator_for_tab) or not str(tab).startswith("sub:"):
            return
        try:
            sub = creator_for_tab(tab)
            if not sub:
                return
            stats = self.db.creator_video_stats(
                str(sub.get("source") or "ph"), str(sub.get("creator_key") or "")
            )
            self.db.mark_creator_scanned(
                int(sub["id"]), int(new_count), int(stats.get("videos") or 0)
            )
        except Exception as exc:
            self._error(f"回写演员订阅统计失败 {tab}: {exc}")

    def _auto_download(self, drama_id: int) -> None:
        if self.downloads is None or self.cancel_event.is_set():
            return
        row = self.db.get_drama(drama_id)
        if not row:
            return
        if int(row["is_duplicate"] or 0) or self.db.is_tag_excluded(drama_id):
            return
        drama_group = module_group(str(row["module"] or ""))
        subscription = self.db.get_subscription(drama_id)
        if subscription and subscription["enabled"] and subscription["auto_download"]:
            quality = subscription["quality"] or self.settings.get("quality", group=drama_group)
            result = self.downloads.enqueue_new(drama_id, quality)
            if result.get("queued"):
                self.db.mark_subscription_auto(drama_id, result["queued"])
                self._update(message=f"订阅自动下载：新增 {result['queued']} 集")
            return
        # 欧美影视：演员订阅（博主）驱动自动下载
        creator = self._creator_sub_for(row)
        if not creator or not creator.get("enabled") or not creator.get("auto_download"):
            return
        # 低于「自动下载最短时长」的影片跳过；手动下载不受影响
        if skip_short_video(self.settings, int(row["duration"] or 0)):
            return
        quality = str(creator.get("quality") or "") or self.settings.get("quality", group=drama_group)
        result = self.downloads.enqueue_new(drama_id, quality)
        if result.get("queued"):
            self.db.mark_creator_auto(int(creator["id"]), result["queued"])
            self._update(message=f"演员订阅自动下载：新增 {result['queued']} 部")

    def _creator_sub_for(self, row):
        source = str(row["source"] or "")
        creator_key = str(row["creator_key"] or "") if "creator_key" in row.keys() else ""
        if not source or not creator_key:
            return None
        return self.db.get_creator_sub_by_key(source, creator_key)

    def auto_download(self, drama_id: int) -> None:
        self._auto_download(drama_id)
