"""当前分区上下文。

后台线程（扫描 / 下载 / 翻译 / 封面）在处理某个剧集时把分区写入
contextvar，Settings 在未显式传 group 时会读取它，从而让代理、Cookie、
画质、目录模板等设置自动落到正确的分区。
"""
from __future__ import annotations

import contextvars

current_group: contextvars.ContextVar[str] = contextvars.ContextVar(
    "drama_group", default=""
)


def set_group(group: str):
    return current_group.set(group or "")


def reset_group(token) -> None:
    try:
        current_group.reset(token)
    except (ValueError, RuntimeError):
        pass


def get_group() -> str:
    return current_group.get()
