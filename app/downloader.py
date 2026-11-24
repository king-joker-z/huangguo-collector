"""下载队列：m3u8 -> ffmpeg 合并 -> 分集落盘 -> NFO/海报。"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import nfo
from .config import COVERS_DIR, SITE_BASE, TMP_DIR
from .db import Database, now_str
from .scraper import ScrapeError, Scraper
from .settings import Settings

QUALITY_LABEL = {"hevc": "1080P HEVC", "h264": "1080P H264", "720p": "720P 压缩"}
logger = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000


class CanceledError(RuntimeError):
    pass


class DownloadManager:
    def __init__(self, db: Database, settings: Settings, scraper: Scraper) -> None:
        self.db = db
        self.settings = settings
        self.scraper = scraper
        self.queue: queue.Queue[int] = queue.Queue()
        self.lock = threading.RLock()
        self.procs: dict[int, subprocess.Popen] = {}
        self.canceled: set[int] = set()
        self.stop_event = threading.Event()
        self.workers: list[threading.Thread] = []
        self.ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    # ------------------------------------------------------------------ lifecycle
    def start(self, count: int | None = None) -> None:
        if self.workers:
            return
        # 上次进程可能被强制结束，遗留的临时分片直接清掉。
        for stale in TMP_DIR.glob("*.mp4"):
            try:
                stale.unlink()
            except OSError:
                pass
        self.db.reset_running_tasks()
        self.db.execute(
            "UPDATE episodes SET state='pending' WHERE state='downloading'"
        )
        for task in self.db.query("SELECT id FROM tasks WHERE state='queued' ORDER BY id"):
            self.queue.put(int(task["id"]))
        count = max(1, min(6, count or self.settings.get_int("concurrency", 2)))
        for index in range(count):
            thread = threading.Thread(target=self._worker_loop, name=f"dl-{index + 1}", daemon=True)
            thread.start()
            self.workers.append(thread)

    def shutdown(self) -> None:
        self.stop_event.set()
        for proc in list(self.procs.values()):
            try:
                proc.kill()
            except Exception:
                pass

    @property
    def worker_count(self) -> int:
        return len(self.workers)

    # -------------------------------------------------------------------- enqueue
    def enqueue(self, drama_id: int, eps: list[int], quality: str | None = None,
                force: bool = False) -> dict:
        drama = self.db.get_drama(drama_id)
        if not drama:
            raise KeyError("剧集不存在")
        quality = quality or self.settings.get("quality", "hevc")
        if quality not in QUALITY_LABEL:
            quality = "hevc"
        max_ep = max(int(drama["total_ep"] or 0), max(eps or [0]))
        queued = 0
        skipped = 0
        for ep in sorted({int(e) for e in eps}):
            if ep < 1 or ep > max_ep:
                skipped += 1
                continue
            ep_row = self.db.get_episode(drama_id, ep)
            if not ep_row:
                skipped += 1
                continue
            if ep_row["state"] == "done" and not force:
                skipped += 1
                continue
            task_id = self.db.upsert_task(drama_id, ep, quality, force=force)
            if task_id is None:
                skipped += 1
                continue
            self.canceled.discard(task_id)
            self.db.set_episode_state(drama_id, ep, "queued")
            self.queue.put(task_id)
            queued += 1
        return {"queued": queued, "skipped": skipped, "quality": quality}

    def enqueue_all(self, drama_id: int, quality: str | None = None, force: bool = False) -> dict:
        eps = [int(r["ep"]) for r in self.db.get_episodes(drama_id)]
        return self.enqueue(drama_id, eps, quality, force)

    def enqueue_new(self, drama_id: int, quality: str | None = None, force: bool = False) -> dict:
        rows = self.db.query(
            "SELECT ep FROM episodes WHERE drama_id=? AND state<>'done' ORDER BY ep", (drama_id,)
        )
        return self.enqueue(drama_id, [int(r["ep"]) for r in rows], quality, force)

    def cancel(self, task_id: int) -> bool:
        task = self.db.get_task(task_id)
        if not task:
            return False
        self.canceled.add(task_id)
        if task["state"] == "queued":
            self.db.update_task(task_id, state="canceled", finished_at=now_str())
            self.db.set_episode_state(int(task["drama_id"]), int(task["ep"]), "pending")
            return True
        if task["state"] == "downloading":
            with self.lock:
                proc = self.procs.get(task_id)
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            return True
        return False

    def cancel_drama(self, drama_id: int) -> int:
        rows = self.db.query(
            "SELECT id FROM tasks WHERE drama_id=? AND state IN ('queued','downloading')", (drama_id,)
        )
        return sum(1 for row in rows if self.cancel(int(row["id"])))

    def retry_failed(self) -> dict:
        """把所有 failed 任务重新入队，保留原任务画质。"""
        rows = self.db.query(
            "SELECT drama_id,ep,quality FROM tasks WHERE state='failed' ORDER BY id"
        )
        queued = skipped = 0
        for row in rows:
            task_id = self.db.upsert_task(
                int(row["drama_id"]), int(row["ep"]), str(row["quality"] or "hevc"), force=True
            )
            if task_id is None:
                skipped += 1
                continue
            self.canceled.discard(task_id)
            self.db.set_episode_state(int(row["drama_id"]), int(row["ep"]), "queued")
            self.queue.put(task_id)
            queued += 1
        return {"queued": queued, "skipped": skipped}

    # --------------------------------------------------------------------- worker
    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                task_id = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if self.stop_event.is_set():
                break
            try:
                self._process(task_id)
            except Exception:
                pass
            finally:
                self.queue.task_done()

    def _process(self, task_id: int) -> None:
        task = self.db.get_task(task_id)
        if not task or task["state"] != "queued":
            return
        drama = self.db.get_drama(int(task["drama_id"]))
        episode = self.db.get_episode(int(task["drama_id"]), int(task["ep"]))
        if not drama or not episode:
            self.db.update_task(task_id, state="failed", error="剧集或分集不存在")
            return
        if task_id in self.canceled:
            self._finish_canceled(task_id, drama, episode)
            return

        self.db.update_task(task_id, state="downloading", started_at=now_str(), progress=0)
        self.db.set_episode_state(int(drama["id"]), int(episode["ep"]), "downloading")

        quality = task["quality"] or self.settings.get("quality", "hevc")
        last_error = ""
        for attempt in range(1, 4):
            if task_id in self.canceled or self.stop_event.is_set():
                self._finish_canceled(task_id, drama, episode)
                return
            # HEVC 原画在部分 ffmpeg/容器环境可能不兼容；第二次起自动回退 H264。
            attempt_quality = "h264" if quality == "hevc" and attempt >= 2 else quality
            try:
                self._download_once(task_id, dict(drama), dict(episode), attempt_quality)
                return
            except CanceledError:
                self._finish_canceled(task_id, drama, episode)
                return
            except Exception as exc:
                last_error = str(exc)
                note = "（已回退 H264）" if attempt_quality != quality else ""
                self.db.update_task(task_id, error=f"第{attempt}次失败{note}: {last_error}")
                self.db.set_episode_state(int(drama["id"]), int(episode["ep"]), "queued")
                if attempt < 3:
                    time.sleep(2 * attempt)
        self.db.update_task(
            task_id, state="failed", error=last_error[-500:], finished_at=now_str(), speed=0
        )
        logger.error("download failed task=%s drama=%s ep=%s: %s",
                     task_id, drama["id"], episode["ep"], last_error[-800:])
        self.db.set_episode_state(int(drama["id"]), int(episode["ep"]), "failed")
        self._cleanup_tmp(drama, episode)

    def _cleanup_tmp(self, drama, episode) -> None:
        site_id = drama["site_id"]
        ep = episode["ep"]
        pattern = f"{site_id}_ep{ep}_*.mp4"
        for path in TMP_DIR.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass
        # 视频临时文件现在写在目标目录，兼容清理旧位置和同目录 .part
        try:
            target = nfo.episode_path(self.settings, drama, int(ep))
            for path in target.parent.glob(f".hg_{site_id}_{ep}_*.part.mp4"):
                try:
                    path.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _finish_canceled(self, task_id: int, drama, episode) -> None:
        self.db.update_task(task_id, state="canceled", finished_at=now_str(), speed=0)
        self.db.set_episode_state(int(drama["id"]), int(episode["ep"]), "pending")
        self.canceled.discard(task_id)
        self._cleanup_tmp(drama, episode)

    # ------------------------------------------------------------------ one file
    def _download_once(self, task_id: int, drama: dict, episode: dict, quality: str) -> None:
        payload = self.scraper.get_playback(str(drama["site_id"]), int(episode["ep"]))
        streams = payload.get("streams") or {}
        use_hevc = quality == "hevc" and bool(streams.get("hevc"))
        url = streams.get("hevc") if use_hevc else streams.get("main")
        if not url:
            raise ScrapeError("没有可用的视频流")
        headers = Scraper.cdn_headers(payload)
        header_text = "".join(f"{key}: {value}\r\n" for key, value in headers.items())

        target = nfo.episode_path(self.settings, drama, int(episode["ep"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        # 临时文件必须和最终文件在同一挂载点：Docker 里 /data 与 /downloads
        # 是两个独立 volume，跨挂载点 os.replace 会报 EXDEV。
        tmp = target.parent / f".hg_{drama['site_id']}_{episode['ep']}_{task_id}.part.mp4"
        for stale in target.parent.glob(f".hg_{drama['site_id']}_{episode['ep']}_*.part.mp4"):
            try:
                stale.unlink()
            except OSError:
                pass

        duration = int(episode.get("duration") or 0)
        cmd = [
            self.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-progress", "pipe:1",
            "-nostats",
            "-headers", header_text,
            "-rw_timeout", "120000000",
            "-i", url,
            "-map", "0:v:0",
            "-map", "0:a:0?",
        ]
        if quality == "720p":
            cmd += [
                "-vf", "scale=-2:720",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
            ]
        else:
            cmd += ["-c", "copy"]
            if use_hevc:
                cmd += ["-tag:v", "hvc1"]
        cmd += ["-movflags", "+faststart", "-max_muxing_queue_size", "2048", "-f", "mp4", str(tmp)]

        env = os.environ.copy()
        proxy = self.settings.get("proxy").strip()
        if proxy:
            env["http_proxy"] = proxy
            env["https_proxy"] = proxy

        creationflags = CREATE_NO_WINDOW if os.name == "nt" else 0
        started = time.time()
        total_size = 0
        prev_size = 0
        prev_time = started
        last_db = 0.0
        error_lines: list[str] = []
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
        with self.lock:
            self.procs[task_id] = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if task_id in self.canceled:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    raise CanceledError()
                if "=" not in line:
                    if line:
                        error_lines.append(line)
                        error_lines = error_lines[-8:]
                    continue
                key, _, value = line.partition("=")
                if key == "total_size":
                    try:
                        total_size = int(value)
                    except ValueError:
                        pass
                elif key == "out_time_ms":
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    now = time.time()
                    if duration > 0:
                        progress = min(99.0, seconds / duration * 100)
                    else:
                        progress = 0.0
                    if now - last_db >= 1.5:
                        elapsed = max(0.001, now - prev_time)
                        speed = max(0.0, (total_size - prev_size) / elapsed)
                        self.db.update_task(
                            task_id,
                            progress=round(progress, 1),
                            size=total_size,
                            speed=round(speed, 0),
                        )
                        prev_size, prev_time = total_size, now
                        last_db = now
                elif key == "speed" and value.strip() == "N/A":
                    continue
            returncode = proc.wait()
        finally:
            with self.lock:
                self.procs.pop(task_id, None)

        if task_id in self.canceled:
            raise CanceledError()
        if returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
            detail = " | ".join(error_lines[-4:]) or f"ffmpeg 退出码 {returncode}"
            raise RuntimeError(detail[:600])

        size = tmp.stat().st_size
        os.replace(tmp, target)
        self.db.set_episode_state(
            int(drama["id"]), int(episode["ep"]), "done", str(target), size
        )
        self.db.update_task(
            task_id,
            state="done",
            progress=100,
            size=size,
            speed=0,
            finished_at=now_str(),
            error="",
        )

        # 集封面：从刚下载的视频抽帧，写 -thumb.jpg
        try:
            fresh = self._drama_for_nfo(int(drama["id"]))
            episode_info = dict(episode)
            episode_info.update({"file_path": str(target), "state": "done"})
            thumb = nfo.generate_episode_thumb(self.settings, fresh, episode_info, str(target))
            if thumb:
                self.db.set_episode_cover(int(drama["id"]), int(episode["ep"]), thumb)
            episodes = [dict(row) for row in self.db.get_episodes(int(drama["id"]))]
            nfo.prepare_show(self.settings, fresh, episodes)
            nfo.write_episode_nfo(self.settings, fresh, episode_info)
        except Exception as exc:
            self.db.update_task(task_id, error=f"媒体信息写入失败: {exc}"[:300])

        progress = self.db.refresh_drama_progress(int(drama["id"]))
        if progress["total_ep"] > 0 and progress["done_ep"] >= progress["total_ep"]:
            if self.settings.get_bool("auto_tag_done", True):
                tag_id = self.db.ensure_tag("已下载", "#3fb950", "system")
                self.db.add_tag_to_drama(int(drama["id"]), tag_id)
            self.db.clear_update(int(drama["id"]))

    def _drama_for_nfo(self, drama_id: int) -> dict:
        row = self.db.get_drama(drama_id)
        data = dict(row) if row else {}
        try:
            data["tags_site"] = json.loads(data.get("tags_json") or "[]")
        except Exception:
            data["tags_site"] = []
        if not data.get("cover_path"):
            candidate = COVERS_DIR / f"{data.get('site_id')}.jpg"
            if candidate.exists():
                data["cover_path"] = str(candidate)
        return data

    # -------------------------------------------------------------------- status
    def active(self) -> list[dict]:
        return self.db.active_tasks()

    def summary(self) -> dict:
        row = self.db.query_one(
            """SELECT
                 SUM(CASE WHEN state='downloading' THEN 1 ELSE 0 END) AS downloading,
                 SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END) AS queued,
                 SUM(CASE WHEN state='done' THEN 1 ELSE 0 END) AS done,
                 SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END) AS failed
               FROM tasks"""
        )
        return {
            "downloading": int(row["downloading"] or 0) if row else 0,
            "queued": int(row["queued"] or 0) if row else 0,
            "done": int(row["done"] or 0) if row else 0,
            "failed": int(row["failed"] or 0) if row else 0,
            "workers": self.worker_count,
            "ffmpeg": self.ffmpeg,
        }
