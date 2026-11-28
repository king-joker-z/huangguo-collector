"""Emby / Jellyfin / Kodi 电视剧刮削所需的目录、NFO 与海报。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageFilter

from .config import (
    COVERS_DIR,
    EPISODE_COVERS_DIR,
    MODULE_GROUPS,
    MODULES,
    SOURCES,
    module_group,
    module_media_type,
)
from .settings import Settings

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_name(name: str, max_len: int = 90) -> str:
    text = _INVALID.sub(" ", name or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = "未命名"
    if text.upper() in _RESERVED:
        text = "_" + text
    return text[:max_len].strip(" .") or "未命名"


def drama_year(drama: dict) -> int | None:
    year = drama.get("year")
    if year:
        try:
            return int(year)
        except (TypeError, ValueError):
            pass
    premiered = str(drama.get("premiered") or "")
    match = re.match(r"(\d{4})", premiered)
    return int(match.group(1)) if match else None


def display_title(drama: dict) -> str:
    """优先使用译名，回退站点原文。"""
    return str(drama.get("title_zh") or "").strip() or str(drama.get("title") or "").strip()


def media_title(drama: dict) -> str:
    title = safe_name(display_title(drama) or "未命名")
    if drama.get("keep_separate"):
        suffix = safe_name(str(drama.get("site_id") or drama.get("source") or "独立版本"))
        title = f"{title} [{suffix}]"
    return title


def folder_actor(drama: dict) -> str:
    """目录用的演员名：优先 actors[].name（兼容原始行的 actors_json 字符串）。"""
    actors = drama.get("actors")
    if not actors:
        try:
            actors = json.loads(str(drama.get("actors_json") or "[]"))
        except Exception:
            actors = []
    for actor in actors or []:
        if isinstance(actor, dict):
            name = str(actor.get("name") or "").strip()
            if name:
                return safe_name(name)
    author = str(drama.get("author") or "").strip()
    return safe_name(author) if author else ""


def show_folder(settings: Settings, drama: dict) -> Path:
    module_label = MODULES.get(drama.get("module", ""), {}).get("label", drama.get("module", ""))
    group = module_group(str(drama.get("module") or ""))
    title = media_title(drama)
    year = drama_year(drama)
    default_tpl = "{module}/{title} ({year})"
    template = settings.get("folder_template", group=group) or default_tpl
    actor = folder_actor(drama)
    # 电影模块（欧美影视）按演员分目录：模板未显式包含 {actor} 时自动
    # 插入到 {module} 之后（设置页保存过模板也依然生效）
    if is_movie(drama) and "{actor}" not in template:
        if "{module}/" in template:
            template = template.replace("{module}/", "{module}/{actor}/", 1)
        else:
            template = "{module}/{actor}/" + template
    if "{actor}" in template and not actor:
        # 无演员信息时去掉该段，避免空目录
        template = template.replace("{actor}/", "").replace("/{actor}", "")
    try:
        rel = template.format(
            module=module_label,
            title=title,
            year=year or "",
            site_id=drama.get("site_id", ""),
            actor=actor,
        )
    except (KeyError, ValueError):
        rel = f"{module_label}/{title} ({year})" if year else f"{module_label}/{title}"
    rel = re.sub(r"\(\s*\)", "", rel)
    rel = re.sub(r"[\\/]+", "/", rel)
    return settings.root_for(group) / rel.strip("/ ")


def is_movie(drama: dict) -> bool:
    """模块配置 media_type=movie 时按电影结构刮削。"""
    return module_media_type(str(drama.get("module") or "")) == "movie"


def movie_basename(drama: dict) -> str:
    """Emby 电影惯例：目录与文件同名，标题带年份。"""
    title = media_title(drama)
    year = drama_year(drama)
    return f"{title} ({year})" if year else title


def season_folder(settings: Settings, drama: dict) -> Path:
    return show_folder(settings, drama) / "Season 01"


def episode_basename(drama: dict, ep: int, width: int = 2) -> str:
    if is_movie(drama):
        return movie_basename(drama)
    title = media_title(drama)
    return f"{title} - S01E{int(ep):0{width}d}"


def episode_path(settings: Settings, drama: dict, ep: int) -> Path:
    if is_movie(drama):
        return show_folder(settings, drama) / (movie_basename(drama) + ".mp4")
    return season_folder(settings, drama) / (episode_basename(drama, ep) + ".mp4")


def episode_nfo_path(settings: Settings, drama: dict, ep: int) -> Path:
    if is_movie(drama):
        # 电影模式：NFO 与视频同名（Emby 电影惯例），不再用 movie.nfo
        return show_folder(settings, drama) / (movie_basename(drama) + ".nfo")
    return season_folder(settings, drama) / (episode_basename(drama, ep) + ".nfo")


def episode_thumb_path(settings: Settings, drama: dict, ep: int) -> Path:
    if is_movie(drama):
        return show_folder(settings, drama) / (movie_basename(drama) + "-thumb.jpg")
    return season_folder(settings, drama) / (episode_basename(drama, ep) + "-thumb.jpg")


def _fallback_thumb(drama: dict, target: Path) -> bool:
    cover = Path(str(drama.get("cover_path") or ""))
    if not cover.exists() and drama.get("site_id"):
        candidate = COVERS_DIR / f"{drama['site_id']}.jpg"
        if candidate.exists():
            cover = candidate
    if not cover.exists():
        return False
    try:
        image = Image.open(cover).convert("RGB")
        ratio = max(640 / image.width, 360 / image.height)
        resized = image.resize((int(image.width * ratio) + 1, int(image.height * ratio) + 1), Image.LANCZOS)
        left = (resized.width - 640) // 2
        top = (resized.height - 360) // 2
        canvas = resized.crop((left, top, left + 640, top + 360))
        canvas = canvas.filter(ImageFilter.GaussianBlur(18))
        canvas = canvas.point(lambda value: int(value * 0.7))
        fit = image.copy()
        fit.thumbnail((640, 360), Image.LANCZOS)
        canvas.paste(fit, ((640 - fit.width) // 2, (360 - fit.height) // 2))
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target, "JPEG", quality=85, optimize=True)
        return True
    except Exception:
        return False


def generate_episode_thumb(settings: Settings, drama: dict, episode: dict,
                           video_path: str | None = None) -> str:
    """从视频抽一帧生成 16:9 集封面，失败时退回剧集海报。"""
    site_id = str(drama.get("site_id") or "")
    number = int(episode.get("ep") or 0)
    if not site_id or not number:
        return ""
    target = EPISODE_COVERS_DIR / f"{site_id}_{number}.jpg"
    video = Path(video_path or episode.get("file_path") or "")
    if not target.exists() or target.stat().st_size < 1024:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg and video.exists():
            duration = int(episode.get("duration") or 0)
            seek = min(15, max(2, int(duration * 0.1))) if duration else 3
            creationflags = 0x08000000 if os.name == "nt" else 0
            try:
                subprocess.run(
                    [
                        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", str(seek), "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=640:-2",
                        "-q:v", "3", "-update", "1", str(target),
                    ],
                    capture_output=True,
                    timeout=90,
                    creationflags=creationflags,
                )
            except Exception:
                pass
        if not target.exists() or target.stat().st_size < 1024:
            if not _fallback_thumb(drama, target):
                return ""
    media_thumb = episode_thumb_path(settings, drama, number)
    try:
        media_thumb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, media_thumb)
    except OSError:
        return str(target)
    return str(target)


def _tags(drama: dict) -> list[str]:
    tags: list[str] = []
    for item in drama.get("tags_site") or []:
        text = str(item).strip()
        if text and text not in tags:
            tags.append(text)
    category = str(drama.get("category_text") or "")
    for part in re.split(r"[·/,，、]", category):
        text = part.strip()
        if text and len(text) <= 12 and text not in tags and not text.startswith("AI"):
            tags.append(text)
    group = module_group(str(drama.get("module") or ""))
    group_label = MODULE_GROUPS.get(group, {}).get("label", group)
    source_label = SOURCES.get(str(drama.get("source") or "huangguo"), {}).get("label", "")
    extras = ["AI短剧", f"{source_label}短剧"] if group == "duanju" else [group_label]
    for text in extras:
        text = text.strip()
        if text and text not in tags:
            tags.append(text)
    return tags


def _prepare_actors(folder: Path, drama: dict) -> list[dict]:
    """把演员头像复制到剧集目录的 actors/ 下，返回可写入 NFO 的条目。"""
    entries: list[dict] = []
    for actor in drama.get("actors") or []:
        if not isinstance(actor, dict):
            continue
        name = str(actor.get("name") or "").strip()
        if not name:
            continue
        role = str(actor.get("role") or "").strip()
        relative = ""
        thumb = str(actor.get("thumb") or "").strip()
        if thumb:
            source = Path(thumb)
            if source.is_file():
                suffix = source.suffix.lower() or ".jpg"
                target = folder / "actors" / (safe_name(name, 60) + suffix)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copyfile(source, target)
                    relative = f"actors/{target.name}"
                except OSError:
                    relative = ""
        entries.append({"name": name, "role": role, "thumb": relative})
    return entries


def _write_movie_nfo(settings: Settings, drama: dict, folder: Path) -> Path:
    """电影模式：NFO 与视频同名（Emby 电影惯例），不再是 movie.nfo。"""
    tags = _tags(drama)
    year = drama_year(drama)
    group = module_group(str(drama.get("module") or ""))
    group_label = MODULE_GROUPS.get(group, {}).get("label", group)
    plot_text = str(drama.get("description") or "").strip() or display_title(drama)
    rating = float(drama.get("score") or 0)
    unique = escape(str(drama.get("site_id") or ""))
    actors = _prepare_actors(folder, drama)
    lines = [
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        "<movie>",
        f"  <title>{escape(media_title(drama))}</title>",
        f"  <originaltitle>{escape(str(drama.get('title_original') or drama.get('title') or ''))}</originaltitle>",
        f"  <sorttitle>{escape(media_title(drama))}</sorttitle>",
        f"  <plot>{escape(plot_text)}</plot>",
        f"  <outline>{escape(plot_text[:220])}</outline>",
        f"  <tagline>{escape(group_label)}</tagline>",
        f"  <rating>{rating:.1f}</rating>",
        f"  <year>{year or ''}</year>",
        f"  <premiered>{escape(str(drama.get('premiered') or ''))}</premiered>",
        f"  <studio>{escape(group_label)}</studio>",
        f"  <uniqueid type=\"huangguo\" default=\"true\">{unique}</uniqueid>",
    ]
    for tag in tags:
        lines.append(f"  <tag>{escape(tag)}</tag>")
    for tag in tags[:5]:
        lines.append(f"  <genre>{escape(tag)}</genre>")
    for actor in actors:
        lines.append("  <actor>")
        lines.append(f"    <name>{escape(actor['name'])}</name>")
        if actor["role"]:
            lines.append(f"    <role>{escape(actor['role'])}</role>")
        if actor["thumb"]:
            lines.append(f"    <thumb>{escape(actor['thumb'])}</thumb>")
        lines.append("  </actor>")
    lines += [
        "  <art>",
        "    <poster>poster.jpg</poster>",
        "    <fanart>fanart.jpg</fanart>",
        "  </art>",
        "</movie>",
    ]
    path = folder / (movie_basename(drama) + ".nfo")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 旧版本用 movie.nfo 命名：写新文件时顺手清理，避免 Emby 双重识别
    legacy = folder / "movie.nfo"
    if legacy.exists() and legacy != path:
        try:
            legacy.unlink()
        except OSError:
            pass
    return path


def write_show_nfo(settings: Settings, drama: dict, episodes: list[dict]) -> Path | None:
    group = module_group(str(drama.get("module") or ""))
    if not settings.get_bool("write_nfo", True, group=group):
        return None
    folder = show_folder(settings, drama)
    folder.mkdir(parents=True, exist_ok=True)
    if is_movie(drama):
        return _write_movie_nfo(settings, drama, folder)
    tags = _tags(drama)
    year = drama_year(drama)
    total = len(episodes)
    group_label = MODULE_GROUPS.get(group, {}).get("label", group)
    module_label = MODULES.get(drama.get("module", ""), {}).get("label", "")
    source_label = SOURCES.get(str(drama.get("source") or "huangguo"), {}).get("label", "")
    studio = f"{source_label}短剧" if group == "duanju" and source_label else group_label
    # 没有简介时用标题兜底（影视类站点常见）。
    plot_text = str(drama.get("description") or "").strip() or display_title(drama)
    plot = escape(plot_text)
    status = "Continuing" if "更新" in str(drama.get("status_text") or "") or total else "Ended"
    rating = float(drama.get("score") or 0)
    unique = escape(str(drama.get("site_id") or ""))
    actors = _prepare_actors(folder, drama)
    lines = [
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        "<tvshow>",
        f"  <title>{escape(media_title(drama))}</title>",
        f"  <originaltitle>{escape(str(drama.get('title_original') or drama.get('title') or ''))}</originaltitle>",
        f"  <sorttitle>{escape(media_title(drama))}</sorttitle>",
        f"  <plot>{plot}</plot>",
        f"  <outline>{escape(plot_text[:220])}</outline>",
        f"  <tagline>{escape(' · '.join(part for part in (module_label, group_label) if part))}</tagline>",
        f"  <rating>{rating:.1f}</rating>",
        f"  <year>{year or ''}</year>",
        f"  <premiered>{escape(str(drama.get('premiered') or ''))}</premiered>",
        f"  <status>{status}</status>",
        f"  <studio>{escape(studio)}</studio>",
        f"  <uniqueid type=\"huangguo\" default=\"true\">{unique}</uniqueid>",
        f"  <episode>{total}</episode>",
    ]
    for tag in tags:
        lines.append(f"  <tag>{escape(tag)}</tag>")
    for tag in tags[:5]:
        lines.append(f"  <genre>{escape(tag)}</genre>")
    for actor in actors:
        lines.append("  <actor>")
        lines.append(f"    <name>{escape(actor['name'])}</name>")
        if actor["role"]:
            lines.append(f"    <role>{escape(actor['role'])}</role>")
        if actor["thumb"]:
            lines.append(f"    <thumb>{escape(actor['thumb'])}</thumb>")
        lines.append("  </actor>")
    lines += [
        "  <art>",
        "    <poster>poster.jpg</poster>",
        "    <fanart>fanart.jpg</fanart>",
        "  </art>",
        "</tvshow>",
    ]
    path = folder / "tvshow.nfo"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_episode_nfo(settings: Settings, drama: dict, ep: dict) -> Path | None:
    group = module_group(str(drama.get("module") or ""))
    if not settings.get_bool("write_nfo", True, group=group):
        return None
    if is_movie(drama):
        # 电影模式只有 movie.nfo，不再写分集 NFO。
        return None
    folder = season_folder(settings, drama)
    folder.mkdir(parents=True, exist_ok=True)
    number = int(ep.get("ep") or 0)
    plot = escape(str(ep.get("synopsis") or drama.get("description") or "").strip())
    premiered = escape(str(drama.get("premiered") or ""))
    rating = float(drama.get("score") or 0)
    unique = escape(f"{drama.get('site_id') or ''}-{number}")
    lines = [
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>',
        "<episodedetails>",
        f"  <title>{escape(str(ep.get('title') or f'第{number}集'))}</title>",
        f"  <showtitle>{escape(media_title(drama))}</showtitle>",
        f"  <plot>{plot}</plot>",
    ]
    if episode_thumb_path(settings, drama, number).exists():
        lines.append(f"  <thumb>{escape(episode_basename(drama, number) + '-thumb.jpg')}</thumb>")
    lines += [
        f"  <rating>{rating:.1f}</rating>",
        "  <season>1</season>",
        f"  <episode>{number}</episode>",
        f"  <aired>{premiered}</aired>",
        f"  <uniqueid type=\"huangguo\" default=\"true\">{unique}</uniqueid>",
        "</episodedetails>",
    ]
    path = episode_nfo_path(settings, drama, number)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _landscape_poster(cover: Path, target: Path) -> bool:
    """横版封面（欧美影视 16:9）生成 2:3 海报：模糊背景 + 原图居中。"""
    try:
        image = Image.open(cover).convert("RGB")
        ratio = max(600 / image.width, 900 / image.height)
        resized = image.resize(
            (int(image.width * ratio) + 1, int(image.height * ratio) + 1), Image.LANCZOS
        )
        left = (resized.width - 600) // 2
        top = (resized.height - 900) // 2
        canvas = resized.crop((left, top, left + 600, top + 900))
        canvas = canvas.filter(ImageFilter.GaussianBlur(24))
        canvas = canvas.point(lambda value: int(value * 0.55))
        fit = image.copy()
        fit.thumbnail((600, 900), Image.LANCZOS)
        canvas.paste(fit, ((600 - fit.width) // 2, (900 - fit.height) // 2))
        canvas.save(target, "JPEG", quality=86, optimize=True)
        return True
    except Exception:
        return False


def write_art(settings: Settings, drama: dict, force: bool = False) -> None:
    """生成 poster.jpg / fanart.jpg。

    - 电影模块（欧美影视）：poster 保留站点原始横版封面（16:9），
      Emby 电影库支持横版海报；fanart 为 16:9 模糊背景。
    - 剧集模块：竖版直接复制；横版转 2:3（电视剧库海报惯例）。
    - ``force=True`` 时无视已有文件强制重建（一键重建历史条目用）。
    """
    folder = show_folder(settings, drama)
    folder.mkdir(parents=True, exist_ok=True)
    cover = Path(str(drama.get("cover_path") or ""))
    if not cover.exists() and drama.get("site_id"):
        candidate = COVERS_DIR / f"{drama['site_id']}.jpg"
        if candidate.exists():
            cover = candidate
    if not cover.exists():
        return
    poster = folder / "poster.jpg"
    if force or not poster.exists():
        done = False
        if is_movie(drama):
            # 电影：poster 就是横版封面本身
            try:
                shutil.copyfile(cover, poster)
                done = True
            except OSError:
                done = False
        else:
            try:
                with Image.open(cover) as image:
                    landscape = image.width > image.height
                if landscape:
                    # 横版封面（影视站常见）：转成 2:3 海报
                    done = _landscape_poster(cover, poster)
            except Exception:
                done = False
        if not done:
            try:
                shutil.copyfile(cover, poster)
            except OSError:
                pass
    if not force and not settings.get_bool("write_fanart", True, group=module_group(str(drama.get("module") or ""))):
        return
    if force:
        fanart = folder / "fanart.jpg"
        try:
            fanart.unlink(missing_ok=True)
        except OSError:
            pass
    fanart = folder / "fanart.jpg"
    if fanart.exists():
        return
    try:
        image = Image.open(cover).convert("RGB")
        ratio = max(1920 / image.width, 1080 / image.height)
        resized = image.resize((int(image.width * ratio) + 1, int(image.height * ratio) + 1), Image.LANCZOS)
        left = (resized.width - 1920) // 2
        top = (resized.height - 1080) // 2
        canvas = resized.crop((left, top, left + 1920, top + 1080))
        canvas = canvas.filter(ImageFilter.GaussianBlur(26))
        canvas = canvas.point(lambda value: int(value * 0.55))
        canvas.save(fanart, "JPEG", quality=86, optimize=True)
    except Exception:
        try:
            shutil.copyfile(cover, fanart)
        except OSError:
            pass


def prepare_show(settings: Settings, drama: dict, episodes: list[dict]) -> Path:
    folder = show_folder(settings, drama)
    folder.mkdir(parents=True, exist_ok=True)
    if not is_movie(drama):
        season_folder(settings, drama).mkdir(parents=True, exist_ok=True)
    write_art(settings, drama)
    write_show_nfo(settings, drama, episodes)
    return folder
