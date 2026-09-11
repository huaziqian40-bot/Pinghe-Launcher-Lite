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
import time as _time
from datetime import datetime
from pathlib import Path

MARKERS = {"pll": ".pll-running", "phl": ".phl-running"}
NAMES = {"pll": "Pinghe Launcher Lite", "phl": "PH Launcher"}
HEARTBEAT_SECONDS = 30            # 自己的标记多久刷新一次
HEARTBEAT_STALE_SECONDS = 90      # 对方多久没刷新就当它已经不在了
_lock = threading.Lock()


def _clean(value, limit: int = 200) -> str:
    """共享文件里的文本一律压平空白并截断(与 sharedschool._clean 同款)。"""
    return " ".join(str(value if value is not None else "").split())[:limit]


def _started_at() -> str:
    try:
        return datetime.fromtimestamp(_PROCESS_START).astimezone().isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _process_start_time() -> float:
    """本进程启动时间(拿不到就退化成"现在")。"""
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        creation, exit_, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, os.getpid())
        if not handle:
            return _time.time()
        try:
            if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                return _time.time()
        finally:
            kernel32.CloseHandle(handle)
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return ticks / 10_000_000 - 11644473600  # 1601-01-01 → Unix 纪元
    except Exception:  # noqa: BLE001
        return _time.time()


_PROCESS_START = _process_start_time()


def _marker(data_dir: Path, kind: str) -> Path:
    return data_dir / MARKERS[kind]


def _pid_alive(pid: int) -> bool:
    """Windows 下判断进程是否真的活着.

    两个坑:
    * ``os.kill(pid, 0)`` 在 Windows 上会真的杀进程, 不能用;
    * 只看 ``OpenProcess`` 是否成功也不行 —— 进程被强杀后未回收(僵尸)时句柄仍能打开,
      实测 PID 8848 明明已经没有这个进程, OpenProcess 照样返回句柄, 于是误判
      "另一个程序正在运行", 用户会看到弹窗说程序已在运行而打不开。
    因此还要用 ``GetExitCodeProcess`` 确认它仍是 STILL_ACTIVE(259)。
    """
    if pid <= 0:
        return False
    try:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def read_marker(data_dir: Path, kind: str) -> dict | None:
    try:
        data = json.loads(_marker(data_dir, kind).read_text(encoding="utf-8"))
        pid = int(data.get("pid") or 0)
        if pid <= 0:
            return None
        return {"pid": pid, "kind": data.get("kind") or kind,
                "updated_at": str(data.get("updated_at") or "")}
    except Exception:  # noqa: BLE001
        return None


def _marker_fresh(marker: dict, now: float | None = None) -> bool:
    """标记是不是"还在跳"的心跳。

    每 30 秒刷新一次, 90 秒没动静就当对方已经不在(崩溃/被强杀/断电)——
    光看 PID 不够: PID 会被系统回收给别人, 那样会误判成"对方在运行"而打不开程序。
    老版本写的标记没有时间戳, 只能当成仍然有效(保守, 不影响升级前的行为)。
    """
    stamp = str(marker.get("updated_at") or "")
    if not stamp:
        return True
    try:
        at = datetime.fromisoformat(stamp).timestamp()
    except Exception:  # noqa: BLE001
        return True
    return ((now if now is not None else _time.time()) - at) < HEARTBEAT_STALE_SECONDS


def sibling_running(data_dir: Path, kind: str = "pll") -> dict | None:
    sibling = "phl" if kind == "pll" else "pll"
    marker = read_marker(data_dir, sibling)
    if marker and _pid_alive(marker["pid"]) and _marker_fresh(marker):
        return {**marker, "name": NAMES[sibling]}
    return None


def _write_marker(data_dir: Path, kind: str, pid: int) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = _marker(data_dir, kind).with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"kind": kind, "pid": pid, "started_at": _started_at(),
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, _marker(data_dir, kind))


def acquire(data_dir: Path, kind: str = "pll") -> dict | None:
    """写自己的运行标记; 同系列软件在跑则返回冲突信息(调用方弹窗后退出)."""
    conflict = sibling_running(data_dir, kind)
    if conflict:
        return conflict
    with _lock:
        _write_marker(data_dir, kind, os.getpid())
    return None


def touch(data_dir: Path, kind: str = "pll") -> bool:
    """刷新心跳; 标记不是自己的(已被别人接管)时不动它。"""
    try:
        marker = read_marker(data_dir, kind)
        if marker and marker["pid"] != os.getpid():
            return False
        with _lock:
            _write_marker(data_dir, kind, os.getpid())
        return True
    except Exception:  # noqa: BLE001
        return False


def heartbeat(data_dir: Path, kind: str = "pll", interval: int = HEARTBEAT_SECONDS) -> None:
    """后台线程定期刷新自己的标记(调用方拿 daemon 线程跑它)。"""
    while True:
        _time.sleep(interval)
        touch(data_dir, kind)


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
    """把 personal() 的按天课卡写进共享 ``data/Timetable``(原子替换, 读改写).

    未知字段(未来版本/对方程序写的)一律原样保留, 只改自己负责的部分。
    """
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
        if not isinstance(doc, dict) or doc.get("kind") not in (None, "pinghe-timetable"):
            doc = {}
        days_out = doc.get("days") if isinstance(doc.get("days"), dict) else {}
        for day, lessons in (days or {}).items():
            if lessons is None:
                days_out.pop(day, None)
            else:
                days_out[day] = lessons
        payload = {
            **doc,                                  # 保留未知字段(数据规范 §2.3)
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


def read_timetable_days(data_dir: Path | None = None) -> dict[str, list[dict]]:
    """共享 ``data/Timetable`` → 按天课卡(本地没有缓存时的兜底来源之一).

    与 ``sharedschool.edupage_days()``(读 data/School 的 edupage 段)互为备份:
    两个程序都会把课表写进两份文件, 只要有一份在, 对方就能用。
    容错读取: 文件缺失/损坏/不是本格式 → 返回空字典, 绝不抛错。
    """
    if data_dir is None:
        from . import paths

        data_dir = paths.data_dir()
    try:
        doc = json.loads((data_dir / "Timetable").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(doc, dict) or doc.get("kind") not in (None, "pinghe-timetable"):
        return {}
    days = doc.get("days")
    if not isinstance(days, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for day, cards in days.items():
        if not isinstance(cards, list) or not cards:
            continue
        rows = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            subject = _clean(card.get("subject"))
            if not subject:
                continue
            rows.append({
                "subject": subject,
                "teacher": _clean(card.get("teacher"), 100),
                "room": _clean(card.get("room"), 60),
                "start": _clean(card.get("start"), 5),
                "end": _clean(card.get("end"), 5),
                "group": _clean(card.get("group"), 80),
                "cancelled": bool(card.get("cancelled")),
                "curriculum": _clean(card.get("curriculum"), 40),
            })
        if rows:
            out[_clean(day, 20)] = sorted(rows, key=lambda item: (item["start"], item["subject"]))
    return out
