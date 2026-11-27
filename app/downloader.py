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
from .config import COVERS_DIR, SITE_BASE, TMP_DIR, module_group
from .db import Database, now_str
from .groupctx import reset_group, set_group
from .scraper import ScrapeError, Scraper
from .settings import Settings

QUALITY_LABEL = {"hevc": "1080P HEVC", "h264": "1080P H264", "720p": "720P 压缩"}
logger = logging.getLogger(__name__)
CREATE_NO_WINDOW = 0x08000000


class CanceledError(RuntimeError):
    pass


class DownloadManager:
    # 下载停滞看门狗：这么久没有新进度就杀掉 ffmpeg，走失败重试
    # （重试会重新 get_playback，拿到新 token / 换到可用 CDN）。
    STALL_KILL_SECONDS = 90

    def __init__(self, db: Database, settings: Settings, scrapers) -> None:
        self.db = db
        self.settings = settings
        if isinstance(scrapers, dict):
            self.scrapers = scrapers
        else:
            self.scrapers = {"huangguo": scrapers} if scrapers is not None else {}
        self.scraper = self.scrapers.get("huangguo")
        self.queue: queue.Queue[int] = queue.Queue()
        self.lock = threading.RLock()
        self.procs: dict[int, subprocess.Popen] = {}
        self.canceled: set[int] = set()
        self.stop_event = threading.Event()
        self.paused = threading.Event()
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

    def pause(self) -> None:
        self.paused.set()

    def resume(self) -> None:
        self.paused.clear()

    @property
    def is_paused(self) -> bool:
        return self.paused.is_set()

    @property
    def worker_count(self) -> int:
        return len(self.workers)

    # -------------------------------------------------------------------- enqueue
    def enqueue(self, drama_id: int, eps: list[int], quality: str | None = None,
                force: bool = False) -> dict:
        drama = self.db.get_drama(drama_id)
        if not drama:
            raise KeyError("剧集不存在")
        drama_group = module_group(str(drama["module"] or ""))
        quality = quality or self.settings.get("quality", group=drama_group)
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

    def retry_failed(self, group: str = "") -> dict:
        """把所有 failed 任务重新入队，保留原任务画质。"""
        if group:
            table = self.db.scope_table(group)
            rows = self.db.query(
                f"SELECT t.drama_id,t.ep,t.quality FROM tasks t "
                f"JOIN {table} d ON d.id=t.drama_id "
                "WHERE t.state='failed' ORDER BY t.id"
            )
        else:
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
            if self.paused.is_set():
                self.stop_event.wait(0.5)
                continue
            try:
                task_id = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if self.stop_event.is_set():
                break
            if self.paused.is_set():
                self.queue.put(task_id)
                self.queue.task_done()
                continue
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
        if not drama:
            self.db.update_task(task_id, state="failed", error="剧集或分集不存在")
            return
        token = set_group(module_group(str(drama["module"] or "")))
        try:
            self._process_drama(task_id, task, drama)
        finally:
            reset_group(token)

    # ------------------------------------------------------------------ helpers
    def _probe_media_duration(self, path: Path) -> float:
        """ffprobe 读媒体时长（秒），失败返回 0。"""
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            candidate = Path(str(self.ffmpeg)).parent / "ffprobe.exe"
            ffprobe = str(candidate) if candidate.exists() else ""
        if not ffprobe:
            return 0.0
        try:
            proc = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True, text=True, timeout=90,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return float(str(proc.stdout).strip())
        except (ValueError, subprocess.SubprocessError, OSError):
            return 0.0

    def _process_drama(self, task_id: int, task, drama) -> None:
        episode = self.db.get_episode(int(task["drama_id"]), int(task["ep"]))
        if not episode:
            self.db.update_task(task_id, state="failed", error="剧集或分集不存在")
            return
        if task_id in self.canceled:
            self._finish_canceled(task_id, drama, episode)
            return

        self.db.update_task(task_id, state="downloading", started_at=now_str(), progress=0)
        self.db.set_episode_state(int(drama["id"]), int(episode["ep"]), "downloading")

        quality = task["quality"] or self.settings.get("quality", "hevc")
        last_error = ""
        for attempt in range(1, 5):
            if task_id in self.canceled or self.stop_event.is_set():
                self._finish_canceled(task_id, drama, episode)
                return
            # HEVC 原画在部分 ffmpeg/容器环境可能不兼容；第二次起自动回退 H264。
            attempt_quality = "h264" if quality == "hevc" and attempt >= 2 else quality
            transcode_video = attempt >= 3
            include_audio = attempt < 4
            try:
                self._download_once(
                    task_id, dict(drama), dict(episode), attempt_quality,
                    include_audio=include_audio,
                    transcode_video=transcode_video,
                )
                return
            except CanceledError:
                self._finish_canceled(task_id, drama, episode)
                return
            except Exception as exc:
                last_error = str(exc)
                note = "（已回退 H264）" if attempt_quality != quality else ""
                if transcode_video:
                    note += "（视频转码兜底）"
                if not include_audio:
                    note += "（无音轨兜底）"
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
    def _download_once(self, task_id: int, drama: dict, episode: dict, quality: str,
                       include_audio: bool = True, transcode_video: bool = False) -> None:
        source = str(drama.get("source") or "huangguo")
        scraper = self.scrapers.get(source) or self.scraper
        if scraper is None:
            raise ScrapeError(f"未配置站点采集器: {source}")
        payload = scraper.get_playback(str(drama["site_id"]), int(episode["ep"]))
        streams = payload.get("streams") or {}
        # use_hevc 必须始终有值：后面 hvc1 tag 的判断要用（欧美的 mp4 直链没有 hevc 流，恒为 False）。
        use_hevc = quality == "hevc" and bool(streams.get("hevc"))
        url = ""
        # 适配器可实现 stream_url(payload, quality) 自定义画质选择
        # （欧美影视：720p 优先 720 直链，hevc/h264 取最高画质 mp4）。
        stream_url_fn = getattr(scraper, "stream_url", None)
        if callable(stream_url_fn):
            try:
                url = str(stream_url_fn(payload, quality) or "")
            except Exception:
                url = ""
        if not url:
            url = streams.get("hevc") if use_hevc else streams.get("main")
        if not url:
            raise ScrapeError("没有可用的视频流")
        headers = scraper.cdn_headers(payload) if hasattr(scraper, "cdn_headers") else Scraper.cdn_headers(payload)
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

        # ------------------------------------------------------------------
        # 分段流（欧美影视）：先逐片下载到本地缓存（断点续传，重试不浪费
        # 流量；token 失效自动刷新续传），再让 ffmpeg 本地合并。所选流是
        # HLS 且适配器实现了 download_stream 时启用。
        local_input = False
        seg_dir = TMP_DIR / f"{drama['site_id']}_{episode['ep']}_segs"
        download_stream = getattr(scraper, "download_stream", None)
        if callable(download_stream) and ".m3u8" in url:
            seg_state = {"last_db": 0.0, "bytes": 0, "time": time.time()}

            def seg_progress(done: int, total: int, bytes_done: int) -> None:
                now = time.time()
                if now - seg_state["last_db"] < 1.5:
                    return
                elapsed = max(0.001, now - seg_state["time"])
                speed = max(0.0, (bytes_done - seg_state["bytes"]) / elapsed)
                self.db.update_task(
                    task_id,
                    progress=round(min(99.0, done / max(1, total) * 100), 1),
                    size=bytes_done,
                    speed=round(speed, 0),
                )
                seg_state.update(last_db=now, bytes=bytes_done, time=now)

            seg_info = download_stream(
                payload, str(drama["site_id"]), int(episode["ep"]), quality,
                seg_dir, progress=seg_progress,
                should_cancel=lambda: task_id in self.canceled,
            )
            if seg_info.get("playlist"):
                url = str(seg_info["playlist"])
                local_input = True

        duration = int(episode.get("duration") or 0)
        if local_input:
            # 分段流自带目标画质（1080/720 原生变体），本地合并直接封装，
            # 不再转码；也无需远程请求头/重连参数。
            cmd = [
                self.ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-progress", "pipe:1",
                "-nostats",
                "-analyzeduration", "100M",
                "-probesize", "100M",
                "-i", url,
                "-map", "0:v:0",
            ]
            if include_audio:
                cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"]
            else:
                cmd += ["-an"]
            cmd += ["-c:v", "copy", "-movflags", "+faststart",
                    "-max_muxing_queue_size", "2048", "-f", "mp4", str(tmp)]
        else:
            cmd = [
                self.ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-progress", "pipe:1",
                "-nostats",
                "-headers", header_text,
                # 30 秒无数据即判死连接（默认 120s 太久，卡住的分片会拖住整个任务）
                "-rw_timeout", "30000000",
                # 分片连接被 CDN 掐断时自动重连
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-analyzeduration", "100M",
                "-probesize", "100M",
                "-i", url,
                "-map", "0:v:0",
            ]
            if include_audio:
                cmd += ["-map", "0:a:0?"]
            if quality == "720p":
                cmd += [
                    "-vf", "scale=-2:720",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                ]
                if include_audio:
                    cmd += ["-c:a", "aac", "-b:a", "128k"]
                else:
                    cmd += ["-an"]
            else:
                if transcode_video:
                    # 某些 HLS 流的宽高/SPS 在 copy 时不可用，导致
                    # "dimensions not set / Could not write header"，转码可补齐参数。
                    cmd += [
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-pix_fmt", "yuv420p",
                    ]
                else:
                    # 视频原样复制；音频统一转 AAC，避开 ADTS/AAC 头不兼容导致
                    # aac_adtstoasc 在 MP4 封装时报 Invalid data。
                    cmd += ["-c:v", "copy"]
                if include_audio:
                    cmd += ["-c:a", "aac", "-b:a", "128k"]
                else:
                    cmd += ["-an"]
                if use_hevc and not transcode_video:
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

        # 停滞看门狗：HLS 分片连接被 CDN 挂住时 ffmpeg 不退出也不报错，
        # 进度会冻住。90 秒无新进度直接杀掉进程，走正常失败重试
        # （重试会重新 get_playback 拿新 token / 换可用 CDN）。
        stall_state = {"last": time.time()}
        stall_event = threading.Event()

        def stall_watchdog():
            while proc.poll() is None:
                time.sleep(5)
                if time.time() - stall_state["last"] > self.STALL_KILL_SECONDS:
                    stall_event.set()
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return

        watchdog = threading.Thread(target=stall_watchdog, name=f"stall-{task_id}", daemon=True)
        watchdog.start()
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
                        new_size = int(value)
                    except ValueError:
                        continue
                    if new_size != total_size:
                        total_size = new_size
                        stall_state["last"] = time.time()
                elif key == "out_time_ms":
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    stall_state["last"] = time.time()
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
        if stall_event.is_set():
            raise RuntimeError(
                f"下载停滞超过 {self.STALL_KILL_SECONDS} 秒（CDN 分片连接挂起），已中断"
            )
        if returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
            detail = " | ".join(error_lines[-4:]) or f"ffmpeg 退出码 {returncode}"
            raise RuntimeError(detail[:600])

        # 时长校验：HLS 分片 token 中途失效时，ffmpeg 会静默跳过剩余分片
        # 并正常退出（exit 0），产出被截断的"成功"文件。校验基线用
        # 分段下载的播放列表总时长（流的地面真值）——站点解析的时长
        # 可能抓到推荐位视频的数字，不能作为判据。
        expected = int(episode.get("duration") or 0)
        if local_input and seg_info.get("duration"):
            stream_expected = int(float(seg_info["duration"]))
            if stream_expected > 30:
                expected = stream_expected
        if expected > 30:
            actual = self._probe_media_duration(tmp)
            if actual and actual < expected * 0.9:
                raise RuntimeError(
                    f"下载不完整：文件仅 {actual:.0f} 秒 / 流 {expected} 秒"
                    f"（分片中途失效，重试会换新地址）"
                )

        size = tmp.stat().st_size
        os.replace(tmp, target)
        # 下载成功：清理分片缓存（失败/取消时保留，供下次断点续传）
        if local_input:
            shutil.rmtree(seg_dir, ignore_errors=True)
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
        try:
            data["actors"] = json.loads(data.get("actors_json") or "[]")
        except Exception:
            data["actors"] = []
        if not data.get("cover_path"):
            candidate = COVERS_DIR / f"{data.get('site_id')}.jpg"
            if candidate.exists():
                data["cover_path"] = str(candidate)
        return data

    # -------------------------------------------------------------------- status
    def active(self, group: str = "") -> list[dict]:
        return self.db.active_tasks(group=group)

    def summary(self, group: str = "") -> dict:
        if group:
            table = self.db.scope_table(group)
            query = f"""SELECT
                 SUM(CASE WHEN t.state='downloading' THEN 1 ELSE 0 END) AS downloading,
                 SUM(CASE WHEN t.state='queued' THEN 1 ELSE 0 END) AS queued,
                 SUM(CASE WHEN t.state='done' THEN 1 ELSE 0 END) AS done,
                 SUM(CASE WHEN t.state='failed' THEN 1 ELSE 0 END) AS failed
               FROM tasks t JOIN {table} d ON d.id=t.drama_id"""
        else:
            query = """SELECT
                 SUM(CASE WHEN state='downloading' THEN 1 ELSE 0 END) AS downloading,
                 SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END) AS queued,
                 SUM(CASE WHEN state='done' THEN 1 ELSE 0 END) AS done,
                 SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END) AS failed
               FROM tasks"""
        row = self.db.query_one(query)
        return {
            "downloading": int(row["downloading"] or 0) if row else 0,
            "queued": int(row["queued"] or 0) if row else 0,
            "done": int(row["done"] or 0) if row else 0,
            "failed": int(row["failed"] or 0) if row else 0,
            "paused": self.paused.is_set(),
            "workers": self.worker_count,
            "ffmpeg": self.ffmpeg,
        }
