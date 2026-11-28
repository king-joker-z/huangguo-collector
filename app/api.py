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
from .actors import ensure_avatars
from .config import (
    GROUP_ORDER,
    MODULE_GROUPS,
    MODULES,
    VERSION,
    module_group,
    module_media_type,
    modules_in_group,
)
from .creators import CreatorManager
from .db import now_str
from .groupctx import reset_group, set_group
from .providers import provider_summary
from .runtime import (
    covers,
    creators,
    db,
    downloads,
    hls,
    scan,
    scheduler,
    scraper,
    scrapers,
    settings,
    title_translate,
    translator,
)
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
    token = set_group(module_group(str(row.get("module") or "")))
    try:
        detail = site_scraper.fetch_detail(str(row["site_id"]))
        ensure_avatars(site_scraper, detail)
        db.apply_detail(str(row["site_id"]), detail)
        if not row.get("cover_path"):
            site_scraper.save_cover(str(row["site_id"]), detail.get("cover_url") or "")
    except ScrapeError as exc:
        fail(f"同步详情失败: {exc}", 502)
    finally:
        reset_group(token)
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
def health(group: str = ""):
    ffmpeg = shutil.which("ffmpeg")
    root = settings.root_for(group)
    free = 0
    try:
        free = shutil.disk_usage(str(root)).free
    except OSError:
        pass
    return ok(
        ffmpeg=bool(ffmpeg),
        ffmpeg_path=ffmpeg or "",
        disk_free=free,
        download_root=str(root),
        workers=downloads.worker_count,
        version=VERSION,
        group=group,
    )


@router.get("/site-check")
def site_check(source: str = "huangguo", group: str = ""):
    site_scraper = scrapers.get(source) or scraper
    if not str(getattr(site_scraper, "site_base", "") or "").strip():
        fail("欧美站点地址未配置：请到「欧美影视 → 设置」填写站点根地址", 400)
    started = time.perf_counter()
    token = set_group(group)
    try:
        kwargs = {"timeout": 12}
        site_scraper.http.get_bytes(site_scraper.check_url, **kwargs)
    except ScrapeError as exc:
        fail(f"站点连接失败: {exc}", 502)
    except Exception as exc:
        fail(f"站点连接失败: {exc}", 502)
    finally:
        reset_group(token)
    latency = int((time.perf_counter() - started) * 1000)
    return ok(latency_ms=latency, source=source, message=f"站点连接正常 · {latency}ms")


@router.get("/stats")
def stats(group: str = ""):
    data = db.stats(ttl=0, group=group)
    data["scan"] = scan.status()
    data["tasks"] = downloads.summary(group=group)
    return ok(**data)


@router.get("/activity")
def activity(group: str = ""):
    return ok(
        scan=scan.status(),
        tasks=downloads.active(group=group),
        summary=downloads.summary(group=group),
        covers=covers.status(),
        translate=title_translate.status(),
        stats=db.stats(group=group),
        group=group,
    )


@router.get("/modules")
def modules():
    groups = []
    for key in GROUP_ORDER:
        meta = MODULE_GROUPS.get(key, {})
        groups.append(
            {
                "key": key,
                "label": meta.get("label", key),
                "icon": meta.get("icon", ""),
                "desc": meta.get("desc", ""),
                "modules": modules_in_group(key),
            }
        )
    return ok(
        modules=[
            {
                "key": k,
                "label": v["label"],
                "icon": v.get("icon", ""),
                "desc": v["desc"],
                "group": v.get("group", "duanju"),
                "media_type": module_media_type(k),
            }
            for k, v in MODULES.items()
        ],
        groups=groups,
    )


@router.get("/providers")
def providers():
    return ok(providers=provider_summary(scrapers))


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
    group: str = "",
):
    return ok(**db.list_dramas(
        module, q, status, tag, sort, page, per_page, score_min, year, site_tag,
        source, include_duplicates, group,
    ))


@router.get("/drama-ids")
def list_drama_ids(
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
    group: str = "",
):
    ids = db.list_drama_ids(
        module, q, status, tag, sort, score_min, year, site_tag, source,
        include_duplicates, group,
    )
    return ok(ids=ids, total=len(ids))


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
    token = set_group(module_group(str(row.get("module") or "")))
    try:
        detail = site_scraper.fetch_detail(str(row["site_id"]))
        ensure_avatars(site_scraper, detail)
        db.apply_detail(str(row["site_id"]), detail)
        site_scraper.save_cover(str(row["site_id"]), detail.get("cover_url") or "")
        scan.auto_download(drama_id)
    except ScrapeError as exc:
        fail(f"同步失败: {exc}", 502)
    finally:
        reset_group(token)
    data = db.get_drama_detail(drama_id)
    return ok(drama=data, message="详情已更新")


@router.post("/dramas/{drama_id}/download")
def download_drama(drama_id: int, payload: dict | None = Body(None)):
    payload = payload or {}
    row = _ensure_episodes(drama_id)
    scope = str(payload.get("scope") or "all")
    eps = payload.get("eps") or []
    drama_group = module_group(str(row.get("module") or ""))
    quality = str(payload.get("quality") or settings.get("quality", group=drama_group))
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
    row = _drama_or_404(drama_id)
    group = module_group(str(row.get("module") or ""))
    db.set_drama_tags(
        drama_id,
        [str(t) for t in payload.get("add", [])],
        [str(t) for t in payload.get("remove", [])],
        group=group,
    )
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
def open_root(group: str = ""):
    return _open_folder(settings.root_for(group))


@router.post("/dramas/batch")
def batch(payload: dict = Body(...)):
    ids = [int(i) for i in payload.get("ids", [])]
    action = str(payload.get("action") or "")
    if not ids:
        fail("没有选择剧集")
    quality = str(payload.get("quality") or "")
    force = bool(payload.get("force"))

    def quality_for(drama_id: int) -> str:
        if quality in ("hevc", "h264", "720p"):
            return quality
        row = db.get_drama(drama_id)
        group = module_group(str(row["module"] or "")) if row else ""
        return settings.get("quality", group=group)

    if action in ("download", "download_new"):
        total_queued = total_skipped = 0
        for drama_id in ids:
            try:
                _ensure_episodes(drama_id)
                if action == "download_new":
                    result = downloads.enqueue_new(drama_id, quality_for(drama_id), force=force)
                else:
                    result = downloads.enqueue_all(drama_id, quality_for(drama_id), force=force)
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
                    source = str(row["source"] or "huangguo")
                    site_scraper = scrapers.get(source) or scraper
                    token = set_group(module_group(str(row["module"] or "")))
                    try:
                        detail = site_scraper.fetch_detail(str(row["site_id"]))
                        ensure_avatars(site_scraper, detail)
                        db.apply_detail(str(row["site_id"]), detail)
                        if not row["cover_path"]:
                            site_scraper.save_cover(str(row["site_id"]), detail.get("cover_url") or "")
                        scan.auto_download(drama_id)
                    finally:
                        reset_group(token)
                except Exception:
                    continue

        threading.Thread(target=worker, name="batch-scan", daemon=True).start()
        return ok(message=f"已开始同步 {len(ids)} 部剧集")

    if action in ("subscribe", "unsubscribe"):
        auto = bool(payload.get("auto_download", True))
        download_now = bool(payload.get("download_now"))
        changed = skipped = queued = failed = 0
        for drama_id in ids:
            row = db.get_drama(drama_id)
            if not row or int(row["is_duplicate"] or 0) or db.is_tag_excluded(drama_id):
                skipped += 1
                continue
            if action == "unsubscribe":
                if db.get_subscription(drama_id):
                    db.delete_subscription(drama_id)
                    changed += 1
                else:
                    skipped += 1
                continue
            drama_quality = quality_for(drama_id)
            db.set_subscription(drama_id, True, drama_quality, auto)
            changed += 1
            if download_now:
                try:
                    _ensure_episodes(drama_id)
                    result = downloads.enqueue_new(drama_id, drama_quality)
                    queued += int(result.get("queued") or 0)
                except Exception:
                    failed += 1
        if action == "unsubscribe":
            return ok(changed=changed, skipped=skipped,
                      message=f"已取消 {changed} 部订阅，跳过 {skipped} 部")
        return ok(
            changed=changed, skipped=skipped, queued=queued, failed=failed,
            quality=quality or "按分区默认", auto_download=auto,
            message=f"已订阅 {changed} 部，跳过 {skipped} 部" + (f"，入队 {queued} 集" if queued else ""),
        )

    if action == "tag":
        add = [str(t) for t in payload.get("add", [])]
        remove = [str(t) for t in payload.get("remove", [])]
        for drama_id in ids:
            row = db.get_drama(drama_id)
            drama_group = module_group(str(row["module"] or "")) if row else ""
            db.set_drama_tags(drama_id, add, remove, group=drama_group)
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


# -------------------------------------------------------------------- duplicates
@router.get("/duplicates")
def duplicates(group: str = ""):
    groups = db.list_duplicate_groups(scope=group)
    return ok(groups=groups, total=len(groups), group=group)


@router.post("/duplicates/batch")
def batch_duplicates(payload: dict = Body(...)):
    ids = [int(i) for i in payload.get("ids", [])]
    action = str(payload.get("action") or "")
    if not ids:
        fail("没有选择版本")
    if action not in ("keep", "unkeep", "subscribe", "download"):
        fail("未知的批量操作")
    scope = str(payload.get("group") or "")
    quality = str(payload.get("quality") or "")
    auto = bool(payload.get("auto_download", True))
    changed = skipped = queued = failed = 0
    for drama_id in ids:
        row = db.get_drama(drama_id)
        if not row or db.is_tag_excluded(drama_id):
            skipped += 1
            continue
        drama_group = module_group(str(row["module"] or ""))
        drama_quality = quality if quality in ("hevc", "h264", "720p") else settings.get(
            "quality", group=drama_group
        )
        try:
            if action in ("keep", "unkeep"):
                db.set_duplicate_keep(drama_id, action == "keep")
                changed += 1
            elif action == "subscribe":
                if int(row["is_duplicate"] or 0):
                    db.set_duplicate_keep(drama_id, True)
                db.set_subscription(drama_id, True, drama_quality, auto)
                changed += 1
            elif action == "download":
                if int(row["is_duplicate"] or 0):
                    db.set_duplicate_keep(drama_id, True)
                _ensure_episodes(drama_id)
                result = downloads.enqueue_all(drama_id, drama_quality)
                queued += int(result.get("queued") or 0)
                changed += 1
        except Exception:
            failed += 1
    if action == "keep":
        message = f"已独立保留 {changed} 个版本，跳过 {skipped} 个"
    elif action == "unkeep":
        message = f"已取消独立 {changed} 个版本，跳过 {skipped} 个"
    elif action == "subscribe":
        message = f"已订阅 {changed} 个版本，跳过 {skipped} 个"
    else:
        message = f"已入队 {queued} 集，处理 {changed} 个版本" + (f"，失败 {failed} 个" if failed else "")
    return ok(
        groups=db.list_duplicate_groups(scope=scope),
        changed=changed, skipped=skipped, queued=queued, failed=failed,
        message=message,
    )


@router.post("/duplicates/{drama_id}/prefer")
def prefer_duplicate(drama_id: int, payload: dict | None = Body(None)):
    payload = payload or {}
    scope = str(payload.get("group") or "")
    try:
        db.prefer_duplicate(drama_id, merge_metadata=bool(payload.get("merge_metadata", True)))
    except KeyError as exc:
        fail(str(exc), 404)
    except ValueError as exc:
        fail(str(exc))
    return ok(groups=db.list_duplicate_groups(scope=scope), message="已设为主版本")


@router.post("/duplicates/{drama_id}/keep")
def keep_duplicate(drama_id: int, payload: dict | None = Body(None)):
    payload = payload or {}
    scope = str(payload.get("group") or "")
    try:
        db.set_duplicate_keep(drama_id, keep=bool(payload.get("keep", True)))
    except KeyError as exc:
        fail(str(exc), 404)
    except ValueError as exc:
        fail(str(exc))
    return ok(groups=db.list_duplicate_groups(scope=scope), message="已更新独立保留状态")


@router.post("/duplicates/{drama_id}/auto")
def reset_duplicate(drama_id: int, payload: dict | None = Body(None)):
    payload = payload or {}
    scope = str(payload.get("group") or "")
    try:
        db.reset_duplicate_preference(drama_id)
    except KeyError as exc:
        fail(str(exc), 404)
    except ValueError as exc:
        fail(str(exc))
    return ok(groups=db.list_duplicate_groups(scope=scope), message="已恢复自动选择")


# -------------------------------------------------------------------- updates
@router.get("/updates")
def updates(limit: int = 120, group: str = ""):
    items = db.updates(limit, group=group)
    return ok(items=items, total=len(items))


# --------------------------------------------------------------- subscriptions
@router.get("/subscriptions")
def subscriptions(group: str = ""):
    items = db.list_subscriptions(group=group)
    return ok(
        items=items,
        total=len(items),
        updates=sum(1 for item in items if item.get("has_update")),
        auto=sum(1 for item in items if item.get("sub_auto")),
        group=group,
    )


@router.post("/dramas/{drama_id}/subscribe")
def subscribe(drama_id: int, payload: dict | None = Body(None)):
    payload = payload or {}
    row = _drama_or_404(drama_id)
    drama_group = module_group(str(row.get("module") or ""))
    quality = str(payload.get("quality") or settings.get("quality", group=drama_group))
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
    group = str(payload.get("group") or "")
    rows = db.list_subscriptions(group=group)
    if ids:
        rows = [row for row in rows if int(row["id"]) in ids]
    queued = 0
    for row in rows:
        if not row.get("sub_enabled") or not row.get("has_update"):
            continue
        drama_group = module_group(str(row.get("module") or ""))
        quality = row.get("sub_quality") or settings.get("quality", group=drama_group)
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


# ---------------------------------------------------------------- creator subs
@router.get("/creator-subs")
def creator_subs(group: str = ""):
    items = creators.list()
    return ok(
        items=items,
        total=len(items),
        enabled=sum(1 for item in items if item.get("enabled")),
        auto=sum(1 for item in items if item.get("auto_download")),
        base=str((scrapers.get("ph").site_base if scrapers.get("ph") else "") or ""),
        group=group,
    )


@router.post("/creator-subs")
def creator_sub_add(payload: dict = Body(...)):
    url = str(payload.get("url") or "").strip()
    if not url:
        fail("请填写博主页地址")
    try:
        item, warning = creators.add(
            url,
            auto_download=bool(payload.get("auto_download", True)),
            quality=str(payload.get("quality") or ""),
            max_pages=int(payload.get("max_pages") or 0),
            enabled=bool(payload.get("enabled", True)),
        )
    except ScrapeError as exc:
        fail(str(exc), 502)
    except Exception as exc:
        fail(f"添加演员订阅失败: {exc}", 502)
    message = f"已订阅博主「{item.get('name') or item.get('creator_key')}」"
    if warning:
        message += f"；{warning}"
    return ok(creator=item, message=message, warning=warning)


@router.put("/creator-subs/{creator_id}")
def creator_sub_update(creator_id: int, payload: dict = Body(...)):
    try:
        item = creators.update(creator_id, **(payload or {}))
    except KeyError:
        fail("演员订阅不存在", 404)
    except ScrapeError as exc:
        fail(str(exc), 502)
    return ok(creator=item, message="演员订阅已更新")


@router.delete("/creator-subs/{creator_id}")
def creator_sub_delete(creator_id: int):
    try:
        creators.delete(creator_id)
    except KeyError:
        fail("演员订阅不存在", 404)
    return ok(message="已删除演员订阅")


@router.post("/creator-subs/{creator_id}/refresh")
def creator_sub_refresh(creator_id: int):
    try:
        item, message = creators.refresh(creator_id)
    except KeyError:
        fail("演员订阅不存在", 404)
    except ScrapeError as exc:
        fail(str(exc), 502)
    return ok(creator=item, message=message)


@router.post("/creator-subs/{creator_id}/scan")
def creator_sub_scan(creator_id: int):
    item = db.get_creator_sub(creator_id)
    if not item:
        fail("演员订阅不存在", 404)
    started, message = scan.start(
        ["ph_creator"], details=True, tab=f"sub:{int(creator_id)}"
    )
    if not started:
        fail(message, 409)
    return ok(scan=scan.status(), message=f"开始采集「{item.get('name') or item.get('creator_key')}」")


@router.post("/creator-subs/download")
def creator_subs_download(payload: dict = None):
    """把演员订阅博主的全部未下载影片加入队列。"""
    payload = payload or {}
    ids = [int(i) for i in (payload.get("ids") or [])]
    items = creators.list()
    if ids:
        items = [item for item in items if int(item["id"]) in ids]
    queued = 0
    for item in items:
        if not item.get("enabled"):
            continue
        creator_key = str(item.get("creator_key") or "")
        rows = db.query(
            "SELECT id FROM dramas WHERE source='ph' AND creator_key=? AND is_duplicate=0",
            (creator_key,),
        )
        quality = str(item.get("quality") or "") or settings.get("quality", group="film")
        for row in rows:
            queued += downloads.enqueue_new(int(row["id"]), quality)["queued"]
    return ok(queued=queued, message=f"已加入队列 {queued} 部")


# ----------------------------------------------------------------------- tags
@router.get("/tags")
def tags(group: str = ""):
    return ok(tags=db.list_tags(group=group), excluded=db.list_excluded_tags(), group=group)


@router.post("/tags")
def create_tag(payload: dict = Body(...)):
    name = str(payload.get("name") or "").strip()
    if not name:
        fail("标签名不能为空")
    color = str(payload.get("color") or "#e5484d")
    group = str(payload.get("group") or "")
    db.ensure_tag(name, color, "custom", group=group)
    return ok(tags=db.list_tags(group=group))


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, group: str = ""):
    db.delete_tag(tag_id)
    return ok(tags=db.list_tags(group=group))


@router.get("/excluded-tags")
def excluded_tags():
    return ok(excluded=db.list_excluded_tags())


@router.post("/excluded-tags")
def add_excluded_tag(payload: dict = Body(...)):
    name = str(payload.get("name") or "").strip()
    if not name:
        fail("标签名不能为空")
    try:
        db.add_excluded_tag(name)
    except ValueError as exc:
        fail(str(exc))
    return ok(excluded=db.list_excluded_tags(), message=f"已排除标签「{name}」")


@router.delete("/excluded-tags")
def remove_excluded_tag(name: str = Query(...)):
    db.remove_excluded_tag(name)
    return ok(excluded=db.list_excluded_tags(), message=f"已恢复标签「{name}」")


# ------------------------------------------------------------------- settings
@router.get("/settings")
def get_settings(group: str = ""):
    return ok(
        settings=settings.all(group=group),
        modules=list(MODULES.keys()),
        ffmpeg=bool(shutil.which("ffmpeg")),
        group=group,
    )


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
def fs_list(path: str = "", group: str = ""):
    home = Path.home()
    raw = path.strip() if path else str(settings.root_for(group))
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
def update_settings(payload: dict = Body(...), group: str = ""):
    settings.update(payload, group=group)
    return ok(settings=settings.all(group=group), message="设置已保存", group=group)


# ---------------------------------------------------------------------- tasks
@router.get("/tasks")
def tasks(state: str = "all", limit: int = 300, group: str = ""):
    states = None if state == "all" else [s for s in state.split(",") if s]
    return ok(
        tasks=db.list_tasks(states, limit, group=group),
        summary=downloads.summary(group=group),
        group=group,
    )


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int):
    if not downloads.cancel(task_id):
        fail("任务无法取消")
    return ok(message="已请求取消")


@router.post("/downloads/pause")
def pause_downloads():
    downloads.pause()
    return ok(paused=True, message="下载队列已暂停，当前正在下载的分集会继续完成")


@router.post("/downloads/resume")
def resume_downloads():
    downloads.resume()
    return ok(paused=False, message="下载队列已继续")


@router.post("/tasks/retry-failed")
def retry_failed_tasks(group: str = ""):
    result = downloads.retry_failed(group=group)
    return ok(**result, message=f"已重新入队 {result['queued']} 个失败任务")


@router.post("/tasks/clear")
def clear_tasks(payload: dict | None = Body(None)):
    payload = payload or {}
    states = payload.get("states") or ["done", "canceled"]
    states = [str(s) for s in states]
    group = str(payload.get("group") or "")
    db.clear_tasks(states, group=group)
    return ok(message="已清理任务记录")


@router.get("/history")
def history(group: str = ""):
    return ok(items=db.recent_activity(40, group=group))


# ----------------------------------------------------------------------- scan
@router.get("/scans")
def scan_history(limit: int = 60, group: str = ""):
    return ok(items=db.list_scan_runs(limit, group=group), group=group)


@router.post("/scans/{run_id}/resume")
def resume_scan(run_id: int):
    started, message = scan.resume(run_id)
    if not started:
        fail(message, 409)
    return ok(message=message, scan=scan.status())


@router.get("/scan")
def scan_status():
    return ok(scan=scan.status())


@router.post("/scan")
def start_scan(payload: dict | None = Body(None)):
    payload = payload or {}
    modules = payload.get("modules") or [payload.get("module") or "all"]
    if isinstance(modules, str):
        modules = [modules]
    group = str(payload.get("group") or "")
    if "all" in modules:
        modules = modules_in_group(group) if group else list(MODULES.keys())
    pages = payload.get("pages")
    details = bool(payload.get("details", True))
    refresh = bool(payload.get("refresh", False))
    tab = str(payload.get("tab") or "")
    started, message = scan.start(
        modules,
        details=details,
        max_pages=int(pages) if pages else None,
        refresh=refresh,
        tab=tab,
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
    group = str(payload.get("group") or "")
    default_modules = modules_in_group(group) if group else list(MODULES.keys())
    modules = [m for m in (payload.get("modules") or default_modules) if m in MODULES]
    if group:
        allowed = set(default_modules)
        modules = [m for m in modules if m in allowed]
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
def list_schedules(group: str = ""):
    return ok(
        items=db.list_schedules(group=group),
        active_schedule_id=scheduler.active_schedule_id,
        scan=scan.status(),
        group=group,
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


# --------------------------------------------------------------- nfo rebuild
_nfo_rebuild = {
    "state": "idle",
    "total": 0,
    "done": 0,
    "message": "",
    "errors": [],
    "started_at": "",
    "finished_at": "",
}
_nfo_rebuild_lock = threading.Lock()


def _migrate_to_actor_folder(folder: Path, episodes: list, drama_id: int, root: Path) -> None:
    """把旧结构目录里的已下载内容搬进新目录（演员目录），并回写分集路径。"""
    done_eps = [e for e in episodes if e.get("state") == "done" and e.get("file_path")]
    if not done_eps:
        return
    old_folder = Path(str(done_eps[0]["file_path"])).parent
    try:
        old_resolved = old_folder.resolve()
        root_resolved = root.resolve()
    except OSError:
        return
    if not str(old_resolved).startswith(str(root_resolved)) or old_resolved == root_resolved:
        return
    if old_resolved == folder.resolve():
        return
    if any(old_resolved == p.resolve() for p in folder.parents):
        return  # 旧目录是新目录的祖先：避免自我嵌套
    if not old_resolved.exists():
        return
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not folder.exists():
            # 整目录搬迁（视频 + 刮削文件一起）
            shutil.move(str(old_resolved), str(folder))
        else:
            # 目标已有内容（例如上次重建先生成了刮削文件）：把旧目录里
            # 目标缺少的文件搬过去（视频等），随后整个清除旧目录——
            # 新目录会重新生成全部刮削文件，旧的都是过期副本
            for item in old_resolved.iterdir():
                target = folder / item.name
                if target.exists():
                    continue
                try:
                    shutil.move(str(item), str(target))
                except OSError:
                    continue
            shutil.rmtree(old_resolved, ignore_errors=True)
    except OSError:
        return
    for episode in done_eps:
        name = Path(str(episode["file_path"])).name
        new_path = folder / name
        if new_path.exists():
            db.set_episode_state(
                drama_id, int(episode["ep"]), "done", str(new_path),
                int(episode.get("file_size") or 0),
            )


def _run_nfo_rebuild(ids: list[int]) -> None:
    global _nfo_rebuild
    rebuilt = 0
    errors: list[str] = []
    try:
        for index, drama_id in enumerate(ids, start=1):
            detail = db.get_drama_detail(drama_id)
            if not detail or not detail.get("cover_path"):
                continue
            try:
                group = module_group(str(detail.get("module") or ""))
                root = settings.root_for(group)
                folder = nfo.show_folder(settings, detail)
                episodes = detail.get("episodes") or []
                # 先迁移目录（旧结构 → 演员目录），再生成刮削文件
                _migrate_to_actor_folder(folder, episodes, drama_id, root)
                folder.mkdir(parents=True, exist_ok=True)
                # 清理旧产物：poster / fanart 全部重生成；电影模式删掉
                # 旧的 movie.nfo（新命名与视频同名）
                for name in ("poster.jpg", "fanart.jpg"):
                    (folder / name).unlink(missing_ok=True)
                if nfo.is_movie(detail):
                    (folder / "movie.nfo").unlink(missing_ok=True)
                nfo.write_art(settings, detail, force=True)
                nfo.write_show_nfo(settings, detail, episodes)
                for episode in episodes:
                    if episode.get("state") == "done":
                        nfo.write_episode_nfo(settings, detail, episode)
                rebuilt += 1
            except Exception as exc:
                errors.append(f"#{drama_id}: {exc}")
            with _nfo_rebuild_lock:
                _nfo_rebuild.update(
                    done=index,
                    message=f"重建刮削文件 {index}/{len(ids)}",
                )
        with _nfo_rebuild_lock:
            _nfo_rebuild.update(
                state="done",
                message=f"完成：重建 {rebuilt} 部"
                + (f"，失败 {len(errors)} 部" if errors else ""),
                errors=errors[-20:],
                finished_at=now_str(),
            )
    except Exception as exc:
        with _nfo_rebuild_lock:
            _nfo_rebuild.update(
                state="error", message=f"重建异常: {exc}", finished_at=now_str()
            )


@router.get("/nfo/rebuild")
def nfo_rebuild_status():
    with _nfo_rebuild_lock:
        return ok(rebuild=dict(_nfo_rebuild))


@router.post("/nfo/rebuild")
def nfo_rebuild_start(payload: dict | None = Body(None)):
    global _nfo_rebuild
    payload = payload or {}
    group = str(payload.get("group") or "")
    ids = [int(i) for i in (payload.get("ids") or [])]
    with _nfo_rebuild_lock:
        if _nfo_rebuild.get("state") == "running":
            fail("重建任务正在进行中", 409)
        if not ids:
            rows = db.query(
                "SELECT DISTINCT d.id FROM dramas d JOIN episodes e ON e.drama_id=d.id "
                "WHERE e.state='done' AND d.is_duplicate=0"
            )
            ids = [int(r["id"]) for r in rows]
            if group:
                allowed = set(modules_in_group(group))
                ids = [
                    i for i in ids
                    if str((db.get_drama(i) or {"module": ""})["module"] or "") in allowed
                ]
        if not ids:
            return ok(rebuild=dict(_nfo_rebuild), message="没有已下载的剧集需要重建")
        _nfo_rebuild = {
            "state": "running",
            "total": len(ids),
            "done": 0,
            "message": f"准备重建 {len(ids)} 部",
            "errors": [],
            "started_at": now_str(),
            "finished_at": "",
        }
        threading.Thread(
            target=_run_nfo_rebuild, args=(ids,), name="nfo-rebuild", daemon=True
        ).start()
    return ok(rebuild=dict(_nfo_rebuild), message=f"开始重建 {len(ids)} 部的 NFO 与海报")


@router.post("/covers/backfill")
def backfill_covers(payload: dict | None = Body(None)):
    payload = payload or {}
    ids = [int(i) for i in (payload.get("drama_ids") or [])]
    all_flag = bool(payload.get("all"))
    group = str(payload.get("group") or "")
    if not ids and not all_flag:
        fail("请选择剧集，或选择补全全部已下载")
    started, message = covers.start(
        ids or None, force=bool(payload.get("force")), group=group
    )
    if not started:
        fail(message, 409)
    return ok(message=message, covers=covers.status())


# --------------------------------------------------------------- title translate
@router.get("/translate")
def translate_status(group: str = ""):
    return ok(
        translate=title_translate.status(),
        enabled=translator.enabled_for(group),
        target=translator.target_for(group),
        group=group,
    )


@router.post("/translate")
def translate_titles(payload: dict | None = Body(None)):
    payload = payload or {}
    ids = [int(i) for i in (payload.get("ids") or [])]
    group = str(payload.get("group") or "")
    started, message = title_translate.start(
        ids or None, force=bool(payload.get("force")), group=group
    )
    if not started:
        fail(message, 409)
    return ok(translate=title_translate.status(), message=message)


@router.post("/dramas/{drama_id}/translate")
def translate_one(drama_id: int):
    row = _drama_or_404(drama_id)
    token = set_group(module_group(str(row.get("module") or "")))
    try:
        value = translator.translate_drama(drama_id, force=True)
    except Exception as exc:
        fail(f"翻译失败: {exc}", 502)
    finally:
        reset_group(token)
    if not value:
        return ok(title_zh="", message="该标题无需翻译")
    return ok(title_zh=value, drama=db.get_drama_detail(drama_id), message="标题已翻译")


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






