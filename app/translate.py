"""标题翻译：免费 Google 翻译接口 + 本地缓存 + 后台批量任务。

翻译结果只写入 `dramas.title_zh`，`dramas.title` 始终保留站点原文，
因此同名去重（title_key）和扫描逻辑不受影响。
"""
from __future__ import annotations

import re
import threading
from typing import Any

import requests

from .config import USER_AGENT, module_group
from .db import Database, now_str
from .groupctx import reset_group, set_group
from .settings import Settings

GOOGLE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


class TranslateError(RuntimeError):
    pass


def cjk_ratio(text: str) -> float:
    raw = str(text or "")
    stripped = re.sub(r"\s+", "", raw)
    if not stripped:
        return 1.0
    return len(CJK_RE.findall(stripped)) / len(stripped)


def is_traditional_text(text: str) -> bool:
    """检测简/繁：GB2312 只收录简体常用字，繁体字大多无法编码。

    站点标题常是繁体（如「紅鑽細跟黃色螺旋飛機杯」），目标 zh-CN 时
    需要走 Google 做繁→简转换，而不是当作"已是中文"跳过。
    """
    for ch in str(text or ""):
        if "\u4e00" <= ch <= "\u9fff":
            try:
                ch.encode("gb2312")
            except UnicodeEncodeError:
                return True
    return False


def needs_script_conversion(text: str, lang: str) -> bool:
    """中文目标语言下，原文简繁与目标字形不一致时需要转换。"""
    lang_lower = str(lang or "").lower()
    if not lang_lower.startswith("zh"):
        return False
    if cjk_ratio(text) <= 0.35:
        return False
    target_traditional = lang_lower in ("zh-tw", "zh-hant")
    return is_traditional_text(text) != target_traditional


class Translator:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._session: requests.Session | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ http
    def _http(self) -> requests.Session:
        with self._lock:
            if self._session is None:
                session = requests.Session()
                session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
                self._session = session
            return self._session

    @property
    def enabled(self) -> bool:
        return self.settings.get_bool("translate_titles", False)

    def enabled_for(self, group: str = "") -> bool:
        return self.settings.get_bool("translate_titles", False, group=group)

    @property
    def target(self) -> str:
        return (self.settings.get("translate_target") or "zh-CN").strip() or "zh-CN"

    def target_for(self, group: str = "") -> str:
        raw = self.settings.get("translate_target", "zh-CN", group=group)
        return (raw or "zh-CN").strip() or "zh-CN"

    # ------------------------------------------------------------- translate
    def translate(self, text: str, target: str | None = None) -> str:
        source = str(text or "").strip()
        if not source:
            return ""
        lang = (target or self.target).strip() or "zh-CN"
        cached = self.db.get_translation(source, lang)
        if cached:
            return cached
        if lang.lower().startswith("zh") and cjk_ratio(source) > 0.35:
            # 已是目标字形（简体→zh-CN / 繁体→zh-TW）才跳过；
            # 繁体标题要继续走 Google 转成简体。
            if not needs_script_conversion(source, lang):
                return source

        params = {
            "client": "gtx",
            "sl": "auto",
            "tl": lang,
            "dt": "t",
            "q": source,
        }
        proxy = self.settings.get("proxy").strip()
        proxies = {"http": proxy, "https": proxy} if proxy else None
        try:
            response = self._http().get(
                GOOGLE_ENDPOINT, params=params, timeout=20, proxies=proxies
            )
        except requests.RequestException as exc:
            raise TranslateError(f"翻译请求失败: {exc}") from exc
        if response.status_code >= 400:
            raise TranslateError(f"翻译接口 HTTP {response.status_code}")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise TranslateError("翻译接口返回非 JSON") from exc
        translated = self._join_segments(payload)
        if not translated:
            raise TranslateError("翻译结果为空")
        self.db.save_translation(source, lang, translated)
        return translated

    @staticmethod
    def _join_segments(payload: Any) -> str:
        if not isinstance(payload, list) or not payload:
            return ""
        segments = payload[0]
        if not isinstance(segments, list):
            return ""
        parts: list[str] = []
        for segment in segments:
            if isinstance(segment, list) and segment and isinstance(segment[0], str):
                parts.append(segment[0])
        return "".join(parts).strip()

    # ---------------------------------------------------------------- dramas
    def translate_drama(self, drama_id: int, force: bool = False) -> str:
        row = self.db.get_drama(drama_id)
        if not row:
            return ""
        existing = str(row["title_zh"] or "").strip()
        if existing and not force:
            return existing
        original = str(row["title"] or "").strip()
        if not original:
            return ""
        group = module_group(str(row["module"] or ""))
        lang = self.target_for(group)
        if cjk_ratio(original) > 0.35 and lang.lower().startswith("zh"):
            # 已是目标字形的中文标题无需翻译；繁体→简体继续走转换
            if not needs_script_conversion(original, lang):
                return ""
        translated = self.translate(original, lang)
        if translated and translated != original:
            self.db.set_title_zh(drama_id, translated)
            return translated
        return ""

    def maybe_translate_drama(self, drama_id: int) -> str:
        """扫描流程调用：开关打开时静默翻译，失败不影响采集。"""
        row = self.db.get_drama(drama_id)
        if not row:
            return ""
        if not self.enabled_for(module_group(str(row["module"] or ""))):
            return ""
        try:
            return self.translate_drama(drama_id)
        except Exception:
            return ""


class TitleTranslateManager:
    """批量翻译已有剧集标题，后台执行并暴露进度。"""

    def __init__(self, db: Database, settings: Settings, translator: Translator) -> None:
        self.db = db
        self.settings = settings
        self.translator = translator
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.job = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {
            "state": "idle",
            "total": 0,
            "done": 0,
            "translated": 0,
            "skipped": 0,
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
                return False, "翻译任务正在进行中"
            rows = self.db.dramas_needing_translation(drama_ids, group=group)
            if force and drama_ids:
                rows = [dict(row) for row in self.db.query(
                    f"SELECT id,title FROM dramas WHERE id IN ({','.join('?' for _ in drama_ids)})",
                    drama_ids,
                )]
            self.job = self._idle()
            self.job.update(
                {
                    "state": "running",
                    "total": len(rows),
                    "message": f"准备翻译 {len(rows)} 个标题",
                    "started_at": now_str(),
                }
            )
            self.thread = threading.Thread(
                target=self._run, args=(rows,), name="title-translate", daemon=True
            )
            self.thread.start()
        return True, f"开始翻译 {len(rows)} 个标题"

    def _run(self, rows: list[dict]) -> None:
        translated = failed = skipped = 0
        try:
            for index, row in enumerate(rows, start=1):
                drama = self.db.get_drama(int(row["id"]))
                token = set_group(module_group(str(drama["module"] or ""))) if drama else None
                try:
                    value = self.translator.translate_drama(int(row["id"]), force=True)
                    if value:
                        translated += 1
                    elif cjk_ratio(str(row.get("title") or "")) > 0.35:
                        skipped += 1
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
                        translated=translated,
                        skipped=skipped,
                        failed=failed,
                        message=f"翻译标题 {index}/{len(rows)}",
                    )
            with self.lock:
                self.job.update(
                    state="done",
                    message=f"完成：翻译 {translated} 个，跳过 {skipped} 个，失败 {failed} 个",
                    finished_at=now_str(),
                )
        except Exception as exc:
            with self.lock:
                self.job.update(
                    state="error",
                    message=f"翻译异常：{exc}",
                    finished_at=now_str(),
                )
