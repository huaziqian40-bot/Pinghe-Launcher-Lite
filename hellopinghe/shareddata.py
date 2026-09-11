# -*- coding: utf-8 -*-
"""与 PH Launcher 的共享数据: 同系列互斥运行锁 + 共享课表写入.

两个程序共用 data/ 下的 settings.yaml、Schedule、agent/ 与 Timetable,
因此不能同时运行(同时写会互相覆盖)。每个程序把自己的 PID 写进运行标记,
检查对方标记时用 PID 存活判断, 崩溃残留不会挡住下次启动。

共享课表 `data/Timetable` 的键与 ``EdupageService.personal()`` 的课卡一致
(subject/teacher/room/start/end/group/cancelled), 两个程序都不需要转换。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

MARKERS = {"pll": ".pll-running", "phl": ".phl-running"}
NAMES = {"pll": "Pinghe Launcher Lite", "phl": "PH Launcher"}
_lock = threading.Lock()


def _marker(data_dir: Path, kind: str) -> Path:
    return data_dir / MARKERS[kind]


def _pid_alive(pid: int) -> bool:
    """Windows 下用 OpenProcess 探活; os.kill(pid, 0) 会真的杀进程, 不能用."""
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:  # noqa: BLE001
        return False


def read_marker(data_dir: Path, kind: str) -> dict | None:
    try:
        data = json.loads(_marker(data_dir, kind).read_text(encoding="utf-8"))
        pid = int(data.get("pid") or 0)
        if pid <= 0:
            return None
        return {"pid": pid, "kind": data.get("kind") or kind}
    except Exception:  # noqa: BLE001
        return None


def sibling_running(data_dir: Path, kind: str = "pll") -> dict | None:
    sibling = "phl" if kind == "pll" else "pll"
    marker = read_marker(data_dir, sibling)
    if marker and _pid_alive(marker["pid"]):
        return marker
    return None


def acquire(data_dir: Path, kind: str = "pll") -> dict | None:
    """写自己的运行标记; 同系列软件在跑则返回冲突信息(调用方弹窗后退出)."""
    conflict = sibling_running(data_dir, kind)
    if conflict:
        return conflict
    with _lock:
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = _marker(data_dir, kind).with_suffix(".tmp")
        tmp.write_text(json.dumps({"kind": kind, "pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _marker(data_dir, kind))
    return None


def release(data_dir: Path, kind: str = "pll") -> None:
    try:
        marker = read_marker(data_dir, kind)
        if marker and marker["pid"] != os.getpid():
            return
        _marker(data_dir, kind).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 共享课表
_timetable_lock = threading.Lock()


def write_timetable_days(days: dict[str, list[dict]], data_dir: Path | None = None) -> Path:
    """把 personal() 的按天课卡写进共享 ``data/Timetable``(原子替换, 读改写)."""
    if data_dir is None:
        from . import paths

        data_dir = paths.data_dir()
    target = data_dir / "Timetable"
    with _timetable_lock:
        doc: dict = {}
        try:
            doc = json.loads(target.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            doc = {}
        if doc.get("kind") not in (None, "pinghe-timetable"):
            doc = {}
        days_out = doc.get("days") if isinstance(doc.get("days"), dict) else {}
        for day, lessons in (days or {}).items():
            if lessons is None:
                days_out.pop(day, None)
            else:
                days_out[day] = lessons
        payload = {
            "version": 1,
            "kind": "pinghe-timetable",
            "app": NAMES["pll"],
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "days": dict(sorted(days_out.items())),
        }
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    return target
