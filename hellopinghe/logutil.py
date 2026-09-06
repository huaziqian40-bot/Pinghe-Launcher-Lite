"""日志工具: 写入数据目录 logs/app.log, 供公测期间故障分析."""
from __future__ import annotations

import datetime
import traceback

from . import paths

_MAX_SIZE = 2 * 1024 * 1024  # 2MB 轮转


def log(msg: str, level: str = "INFO") -> None:
    try:
        log_dir = paths.data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        f = log_dir / "app.log"
        # 简单轮转: 超过 2MB 归档为 app.log.1
        if f.exists() and f.stat().st_size > _MAX_SIZE:
            f.rename(f.with_suffix(".log.1"))
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] [{level}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass  # 日志失败绝不影响主流程


def error(msg: str) -> None:
    log(msg, "ERROR")


def warn(msg: str) -> None:
    log(msg, "WARN")


def exc(msg: str) -> None:
    """记录异常(含堆栈)."""
    try:
        log_dir = paths.data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        f = log_dir / "app.log"
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(f"[{ts}] [ERROR] {msg}\n")
            fh.write(traceback.format_exc())
    except Exception:  # noqa: BLE001
        pass
