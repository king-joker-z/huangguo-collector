"""HTTP API。"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from . import nfo
from .config import MODULES, VERSION
from .db import now_str
from .runtime import covers, db, downloads, hls, scan, scheduler, scraper, scrapers, settings
from .scheduler import format_datetime, next_run_at
from .scraper import ScrapeError

router = APIRouter(prefix="/api")

# 粗略码率（bps），用于下载前估算空间
QUALITY_MBPS = {"hevc": 2.36, "h264": 8.30, "720p": 2.20}


def ok(**kwargs):
    return {"ok": True, **kwargs}


def fail(message: str, status: int = 400):
    raise HTTPException(status_code=status, detail=message)


def _drama_or_404(drama_id: int) -> dict:
    row = db.get_drama(drama_id)
    if not row:
        fail("剧集不存在", 404)
    return dict(row)


def _ensure_episodes(drama_id: int) -> dict:
    row = _drama_or_404(drama_id)
    episodes = db.get_episodes(drama_id)
    total = int(row.get("total_ep") or 0)
    if episodes and len(episodes) >= total and row.get("detail_scraped_at"):
        return row
    source = str(row.get("source") or "huangguo")
    site_scraper = scrapers.get(source) or scraper
    try:
        detail = site_scraper.fetch_detail(str(row["site_id"]))
        db.apply_detail(str(row["site_id"]), detail)
        if not row.get("cover_path"):
            site_scraper.save_cover(str(row["site_id"]), detail.get("cover_url") or "")
    except ScrapeError as exc:
        fail(f"同步详情失败: {exc}", 502)
    fresh = db.get_drama(drama_id)
    return dict(fresh) if fresh else row


def _episode_numbers(drama_id: int, scope: str, eps: list[int]) -> list[int]:
    rows = db.get_episodes(drama_id)
    if scope == "new":
        return [int(r["ep"]) for r in rows if r["state"] != "done"]
    if scope == "eps":
        valid = {int(r["ep"]) for r in rows}
        return [int(e) for e in eps if int(e) in valid]
    return [int(r["ep"]) for r in rows]


def _estimate(episodes: list[dict], total_ep: int) -> dict:
    duration = sum(int(e.get("duration") or 0) for e in episodes)
    if duration <= 0:
        duration = max(1, total_ep) * 480
    return {
        key: int(duration * mbps * 1_000_000 / 8)
        for key, mbps in QUALITY_MBPS.items()
    }


def _open_folder(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        return ok(
            folder=str(folder),
            message=f"服务器/容器内无法打开资源管理器，请在宿主机查看：{folder}",
        )
    try:
        os.startfile(str(folder))  # type: ignore[attr-defined]
    except Exception as exc:
        fail(f"无法打开目录: {exc}", 500)
    return ok(folder=str(folder), message="已在资源管理器打开")


# --------------------------------------------------------------------- health
@router.get("/health")
def health():
    ffmpeg = shutil.which("ffmpeg")
    free = 0
    try:
        free = shutil.disk_usage(str(settings.download_root)).free
    except OSError:
        pass
    return ok(
        ffmpeg=bool(ffmpeg),
        ffmpeg_path=ffmpeg or "",
        disk_free=free,
        download_root=str(settings.download_root),
        workers=downloads.worker_count,
        version=VERSION,
    )


@router.get("/site-check")
def site_check(source: str = "huangguo"):
    site_scraper = scrapers.get(source) or scraper
    started = time.perf_counter()
    try:
        kwargs = {"timeout": 12}
        site_scraper.http.get_bytes(site_scraper.check_url, **kwargs)
    except ScrapeError as exc:
        fail(f"站点连接失败: {exc}", 502)
    except Exception as exc:
        fail(f"站点连接失败: {exc}", 502)
    latency = int((time.perf_counter() - started) * 1000)
    return ok(latency_ms=latency, source=source, message=f"站点连接正常 · {latency}ms")


@router.get("/stats")
def stats():
    data = db.stats(ttl=0)
    data["scan"] = scan.status()
    data["tasks"] = downloads.summary()
    return ok(**data)


@router.get("/activity")
def activity():
    return ok(
        scan=scan.status(),
        tasks=downloads.active(),
        summary=downloads.summary(),
        covers=covers.status(),
        stats=db.stats(),
    )


@router.get("/modules")
def modules():
    return ok(modules=[{"key": k, "label": v["label"], "desc": v["desc"]} for k, v in MODULES.items()])


# --------------------------------------------------------------------- dramas
@router.get("/dramas")
def list_dramas(
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
):
    return ok(**db.list_dramas(
        module, q, status, tag, sort, page, per_page, score_min, year, site_tag,
        source, include_duplicates,
    ))


@router.get("/dramas/{drama_id}")
def drama_detail(drama_id: int):
    data = db.get_drama_detail(drama_id)
    if not data:
        fail("剧集不存在", 404)
    data["estimate"] = _estimate(data.get("episodes", []), int(data.get("total_ep") or 0))
    data["folder"] = str(nfo.show_folder(settings, data))
    data["quality_label"] = {
        "hevc": "HEVC 原画 · 体积小，默认",
        "h264": "H264 原画 · 兼容性最好",
        "720p": "720P 压缩 · 转码较慢",
    }
    return ok(drama=data)


@router.post("/dramas/{drama_id}/scan")
def scan_drama(drama_id: int):
    row = _drama_or_404(drama_id)
    source = str(row.get("source") or "huangguo")
    site_scraper = scrapers.get(source) or scraper
    try:
        detail = site_scraper.fetch_detail(str(row["site_id"]))
        db.apply_detail(str(row["site_id"]), detail)
        site_scraper.save_cover(str(row["site_id"]), detail.get("cover_url") or "")
        scan.auto_download(drama_id)
    except ScrapeError as exc:
        fail(f"同步失败: {exc}", 502)
    data = db.get_drama_detail(drama_id)
    return ok(drama=data, message="详情已更新")


@router.post("/dramas/{drama_id}/download")
def download_drama(drama_id: int, payload: dict | None = Body(None)):
    payload = payload or {}
    row = _ensure_episodes(drama_id)
    scope = str(payload.get("scope") or "all")
    eps = payload.get("eps") or []
    quality = str(payload.get("quality") or settings.get("quality", "hevc"))
    force = bool(payload.get("force"))
    numbers = _episode_numbers(drama_id, scope, [int(e) for e in eps])
    if not numbers:
        return ok(queued=0, skipped=0, message="没有需要下载的分集")
    result = downloads.enqueue(drama_id, numbers, quality, force=force)
    return ok(
        **result,
        message=f"已加入队列 {result['queued']} 集，跳过 {result['skipped']} 集",
        total_ep=int(row.get("total_ep") or 0),
    )


@router.post("/dramas/{drama_id}/cancel")
def cancel_drama(drama_id: int):
    count = downloads.cancel_drama(drama_id)
    return ok(canceled=count, message=f"已取消 {count} 个下载任务")


@router.post("/dramas/{drama_id}/tags")
def set_tags(drama_id: int, payload: dict = Body(...)):
    _drama_or_404(drama_id)
    db.set_drama_tags(drama_id, [str(t) for t in payload.get("add", [])], [str(t) for t in payload.get("remove", [])])
    return ok(tags=db.tags_for_drama(drama_id))


@router.post("/dramas/{drama_id}/favorite")
def favorite(drama_id: int, payload: dict | None = Body(None)):
    row = _drama_or_404(drama_id)
    value = int((payload or {}).get("value", 0 if row["favorite"] else 1))
    db.set_drama_fields(drama_id, favorite=1 if value else 0)
    return ok(favorite=bool(value))


@router.post("/dramas/{drama_id}/clear-update")
def clear_update(drama_id: int):
    _drama_or_404(drama_id)
    db.clear_update(drama_id)
    return ok(message="已标记更新为已处理")


@router.post("/dramas/{drama_id}/nfo")
def build_nfo(drama_id: int):
    data = db.get_drama_detail(drama_id)
    if not data:
        fail("剧集不存在", 404)
    episodes = data.get("episodes", [])
    folder = nfo.prepare_show(settings, data, episodes)
    for ep in episodes:
        if ep.get("state") == "done":
            nfo.write_episode_nfo(settings, data, ep)
    return ok(folder=str(folder), message="NFO 与海报已生成")


@router.post("/dramas/{drama_id}/open-folder")
def open_folder(drama_id: int):
    data = db.get_drama_detail(drama_id)
    if not data:
        fail("剧集不存在", 404)
    folder = nfo.show_folder(settings, data)
    return _open_folder(folder)


@router.post("/open-root")
def open_root():
    return _open_folder(settings.download_root)


@router.post("/dramas/batch")
def batch(payload: dict = Body(...)):
    ids = [int(i) for i in payload.get("ids", [])]
    action = str(payload.get("action") or "")
    if not ids:
        fail("没有选择剧集")
    quality = str(payload.get("quality") or settings.get("quality", "hevc"))
    force = bool(payload.get("force"))

    if action in ("download", "download_new"):
        total_queued = total_skipped = 0
        for drama_id in ids:
            try:
                _ensure_episodes(drama_id)
                if action == "download_new":
                    result = downloads.enqueue_new(drama_id, quality, force=force)
                else:
                    result = downloads.enqueue_all(drama_id, quality, force=force)
                total_queued += result["queued"]
                total_skipped += result["skipped"]
            except Exception:
                continue
        return ok(queued=total_queued, skipped=total_skipped,
                  message=f"批量加入 {total_queued} 集，跳过 {total_skipped} 集")

    if action == "scan":
        def worker():
            for drama_id in ids:
                try:
                    row = db.get_drama(drama_id)
                    if not row:
                        continue
                    detail = scraper.fetch_detail(str(row["site_id"]))
                    db.apply_detail(str(row["site_id"]), detail)
                    if not row["cover_path"]:
                        scraper.save_cover(str(row["site_id"]), detail.get("cover_url") or "")
                except Exception:
                    continue

        threading.Thread(target=worker, name="batch-scan", daemon=True).start()
        return ok(message=f"已开始同步 {len(ids)} 部剧集")

    if action == "tag":
        add = [str(t) for t in payload.get("add", [])]
        remove = [str(t) for t in payload.get("remove", [])]
        for drama_id in ids:
            db.set_drama_tags(drama_id, add, remove)
        return ok(message=f"已更新 {len(ids)} 部剧集的标签")

    if action == "favorite":
        value = int(payload.get("value", 1))
        for drama_id in ids:
            db.set_drama_fields(drama_id, favorite=1 if value else 0)
        return ok(message="已更新收藏")

    if action == "clear_update":
        for drama_id in ids:
            db.clear_update(drama_id)
        return ok(message="已处理更新标记")

    fail("未知的批量操作")


# -------------------------------------------------------------------- updates
@router.get("/updates")
def updates(limit: int = 120):
    items = db.updates(limit)
    return ok(items=items, total=len(items))


# --------------------------------------------------------------- subscriptions
@router.get("/subscriptions")
def subscriptions():
    items = db.list_subscriptions()
    return ok(
        items=items,
        total=len(items),
        updates=sum(1 for item in items if item.get("has_update")),
        auto=sum(1 for item in items if item.get("sub_auto")),
    )


@router.post("/dramas/{drama_id}/subscribe")
def subscribe(drama_id: int, payload: dict | None = Body(None)):
    payload = payload or {}
    row = _drama_or_404(drama_id)
    quality = str(payload.get("quality") or settings.get("quality", "hevc"))
    if quality not in ("hevc", "h264", "720p"):
        quality = "hevc"
    auto = bool(payload.get("auto_download", True))
    enabled = bool(payload.get("enabled", True))
    if enabled:
        db.set_subscription(drama_id, True, quality, auto)
    else:
        db.delete_subscription(drama_id)
    queued = 0
    if enabled and payload.get("download_now"):
        result = downloads.enqueue_new(drama_id, quality)
        queued = result["queued"]
    return ok(
        subscribed=enabled,
        quality=quality,
        auto_download=auto,
        queued=queued,
        message=("已订阅，扫库发现更新会自动下载" if enabled else "已取消订阅"),
    )


@router.delete("/subscriptions/{drama_id}")
def unsubscribe(drama_id: int):
    _drama_or_404(drama_id)
    db.delete_subscription(drama_id)
    return ok(message="已取消订阅")


@router.post("/subscriptions/download")
def subscriptions_download(payload: dict | None = Body(None)):
    payload = payload or {}
    ids = [int(i) for i in (payload.get("ids") or [])]
    rows = db.list_subscriptions()
    if ids:
        rows = [row for row in rows if int(row["id"]) in ids]
    queued = 0
    for row in rows:
        if not row.get("sub_enabled") or not row.get("has_update"):
            continue
        quality = row.get("sub_quality") or settings.get("quality", "hevc")
        queued += downloads.enqueue_new(int(row["id"]), quality)["queued"]
    return ok(queued=queued, message=f"已加入队列 {queued} 集")


@router.post("/subscriptions/auto")
def subscriptions_auto(payload: dict = Body(...)):
    drama_id = int(payload.get("drama_id") or 0)
    if not drama_id:
        fail("缺少剧集 ID")
    row = db.get_subscription(drama_id)
    if not row:
        fail("尚未订阅", 404)
    auto = bool(payload.get("auto_download", True))
    quality = str(payload.get("quality") or row["quality"] or "hevc")
    db.set_subscription(drama_id, True, quality, auto)
    return ok(auto_download=auto, quality=quality)


# ----------------------------------------------------------------------- tags
@router.get("/tags")
def tags():
    return ok(tags=db.list_tags())


@router.post("/tags")
def create_tag(payload: dict = Body(...)):
    name = str(payload.get("name") or "").strip()
    if not name:
        fail("标签名不能为空")
    color = str(payload.get("color") or "#e5484d")
    db.ensure_tag(name, color, "custom")
    return ok(tags=db.list_tags())


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int):
    db.delete_tag(tag_id)
    return ok(tags=db.list_tags())


# ------------------------------------------------------------------- settings
@router.get("/settings")
def get_settings():
    return ok(settings=settings.all(), modules=list(MODULES.keys()), ffmpeg=bool(shutil.which("ffmpeg")))


@router.post("/settings/check-path")
def check_download_path(payload: dict = Body(...)):
    text = str(payload.get("path") or "").strip()
    if not text:
        fail("路径不能为空")
    path = Path(text).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".huangguo_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        fail(f"目录不可写: {exc}", 400)
    try:
        free = shutil.disk_usage(str(path)).free
    except OSError:
        free = 0
    return ok(path=str(path.resolve()), free=free, message="目录可用")


@router.get("/fs/list")
def fs_list(path: str = ""):
    home = Path.home()
    raw = path.strip() if path else str(settings.download_root)
    target = Path(raw).expanduser()
    try:
        target = target.resolve()
    except OSError:
        target = home
    if not target.exists() or not target.is_dir():
        target = target.parent if target.parent.exists() else home
    dirs = []
    try:
        with os.scandir(target) as iterator:
            for entry in iterator:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({
                            "name": entry.name,
                            "path": str(Path(entry.path)),
                            "writable": os.access(entry.path, os.W_OK),
                        })
                except OSError:
                    continue
    except OSError as exc:
        fail(f"无法读取目录: {exc}", 400)
    dirs.sort(key=lambda item: item["name"].lower())
    drives = []
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:\\")
            if root.exists():
                drives.append(str(root))
    parent = str(target.parent) if target.parent != target else ""
    return ok(
        current=str(target),
        parent=parent,
        dirs=dirs,
        drives=drives,
        home=str(home),
        writable=os.access(str(target), os.W_OK),
        separator=os.sep,
    )


@router.post("/fs/mkdir")
def fs_mkdir(payload: dict = Body(...)):
    parent = Path(str(payload.get("parent") or "")).expanduser()
    name = str(payload.get("name") or "").strip()
    if not name or any(ch in name for ch in '\\/:*?"<>|'):
        fail("文件夹名不合法")
    target = parent / name
    try:
        target.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        fail("文件夹已存在")
    except OSError as exc:
        fail(f"创建失败: {exc}", 400)
    return ok(path=str(target.resolve()), message="文件夹已创建")


@router.put("/settings")
def update_settings(payload: dict = Body(...)):
    settings.update(payload)
    return ok(settings=settings.all(), message="设置已保存")


# ---------------------------------------------------------------------- tasks
@router.get("/tasks")
def tasks(state: str = "all", limit: int = 300):
    states = None if state == "all" else [s for s in state.split(",") if s]
    return ok(tasks=db.list_tasks(states, limit), summary=downloads.summary())


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int):
    if not downloads.cancel(task_id):
        fail("任务无法取消")
    return ok(message="已请求取消")


@router.post("/tasks/retry-failed")
def retry_failed_tasks():
    result = downloads.retry_failed()
    return ok(**result, message=f"已重新入队 {result['queued']} 个失败任务")


@router.post("/tasks/clear")
def clear_tasks(payload: dict | None = Body(None)):
    payload = payload or {}
    states = payload.get("states") or ["done", "canceled"]
    states = [str(s) for s in states]
    db.clear_tasks(states)
    return ok(message="已清理任务记录")


@router.get("/history")
def history():
    return ok(items=db.recent_activity(40))


# ----------------------------------------------------------------------- scan
@router.get("/scan")
def scan_status():
    return ok(scan=scan.status())


@router.post("/scan")
def start_scan(payload: dict | None = Body(None)):
    payload = payload or {}
    modules = payload.get("modules") or [payload.get("module") or "all"]
    if isinstance(modules, str):
        modules = [modules]
    if "all" in modules:
        modules = list(MODULES.keys())
    pages = payload.get("pages")
    details = bool(payload.get("details", True))
    refresh = bool(payload.get("refresh", False))
    started, message = scan.start(
        modules,
        details=details,
        max_pages=int(pages) if pages else None,
        refresh=refresh,
    )
    if not started:
        fail(message, 409)
    return ok(scan=scan.status(), message=message)


@router.post("/scan/cancel")
def cancel_scan():
    scan.cancel()
    return ok(message="正在停止扫描")


# ------------------------------------------------------------------ schedules
def _schedule_payload(payload: dict) -> dict:
    name = str(payload.get("name") or "全量扫库").strip()[:60]
    time_text = str(payload.get("time") or "03:00").strip()
    try:
        hour, minute = [int(x) for x in time_text.split(":", 1)]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, TypeError):
        fail("时间格式应为 HH:MM")
    weekdays = []
    for value in payload.get("weekdays", [0, 1, 2, 3, 4, 5, 6]):
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            weekdays.append(day)
    if not weekdays:
        fail("至少选择一天")
    modules = [m for m in (payload.get("modules") or ["original", "duanju", "manju", "yeguo"]) if m in MODULES]
    if not modules:
        fail("至少选择一个模块")
    return {
        "name": name,
        "time": f"{hour:02d}:{minute:02d}",
        "weekdays": sorted(set(weekdays)),
        "modules": modules,
        "details": bool(payload.get("details", True)),
        "enabled": bool(payload.get("enabled", True)),
    }


@router.get("/schedules")
def list_schedules():
    return ok(
        items=db.list_schedules(),
        active_schedule_id=scheduler.active_schedule_id,
        scan=scan.status(),
    )


@router.post("/schedules")
def create_schedule(payload: dict = Body(...)):
    data = _schedule_payload(payload)
    target = next_run_at(data["time"], data["weekdays"])
    schedule_id = db.create_schedule(
        data["name"], data["time"], data["weekdays"], data["modules"],
        data["details"], data["enabled"], format_datetime(target),
    )
    scheduler.refresh(schedule_id)
    return ok(schedule=db.get_schedule(schedule_id), message="定时任务已创建")


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, payload: dict = Body(...)):
    if not db.get_schedule(schedule_id):
        fail("定时任务不存在", 404)
    data = _schedule_payload(payload)
    target = next_run_at(data["time"], data["weekdays"])
    db.update_schedule(
        schedule_id,
        name=data["name"],
        time=data["time"],
        weekdays=data["weekdays"],
        modules=data["modules"],
        details=data["details"],
        enabled=data["enabled"],
        next_run_at=format_datetime(target),
    )
    return ok(schedule=db.get_schedule(schedule_id), message="定时任务已更新")


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    db.delete_schedule(schedule_id)
    return ok(message="定时任务已删除")


@router.post("/schedules/{schedule_id}/run")
def run_schedule(schedule_id: int):
    started, message = scheduler.run_now(schedule_id)
    if not started:
        fail(message, 409)
    return ok(message=message, scan=scan.status())


# --------------------------------------------------------------- cover backfill
@router.get("/covers")
def cover_status():
    return ok(covers=covers.status())


@router.post("/covers/backfill")
def backfill_covers(payload: dict | None = Body(None)):
    payload = payload or {}
    ids = [int(i) for i in (payload.get("drama_ids") or [])]
    all_flag = bool(payload.get("all"))
    if not ids and not all_flag:
        fail("请选择剧集，或选择补全全部已下载")
    started, message = covers.start(ids or None, force=bool(payload.get("force")))
    if not started:
        fail(message, 409)
    return ok(message=message, covers=covers.status())


# -------------------------------------------------------------------- playback
@router.get("/watch/{drama_id}/{ep}")
def watch(drama_id: int, ep: int):
    try:
        return hls.manifest_response(drama_id, ep)
    except (ScrapeError, Exception) as exc:
        return Response(status_code=502, content=f"# playback error: {exc}\n",
                        media_type="application/vnd.apple.mpegurl")


@router.get("/stream")
def stream(request: Request, u: str = Query(...), t: str = Query(...)):
    return hls.stream(u, t, request.headers.get("range", ""))






