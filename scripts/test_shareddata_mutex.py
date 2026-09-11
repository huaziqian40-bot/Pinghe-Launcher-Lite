# -*- coding: utf-8 -*-
"""同系列互斥运行锁自检(不需要网络).

    python scripts/test_shareddata_mutex.py

覆盖: 正常抢占、对方在跑时冲突、崩溃残留的死 PID 不挡启动、
心脏跳过期的标记当作对方不在(PID 被回收的情况)、touch 不抢别人的标记。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe import shareddata  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def spawn_idle() -> subprocess.Popen:
    """一个真实存活、但不是本进程的 PID。"""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_marker(data_dir: Path, kind: str, pid: int, updated_at: str | None) -> None:
    payload = {"kind": kind, "pid": pid}
    if updated_at is not None:
        payload["updated_at"] = updated_at
    (data_dir / shareddata.MARKERS[kind]).write_text(json.dumps(payload), encoding="utf-8")


def main() -> int:
    children: list[subprocess.Popen] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)

            # 1. 空目录: 直接拿到标记
            check(shareddata.acquire(data, "pll") is None, "空目录应当能拿到标记")
            check((data / ".pll-running").exists(), "标记文件应当被写出来")
            check(shareddata.read_marker(data, "pll")["pid"] == os.getpid(), "标记里应是本进程 PID")

            # 2. 对面在跑 → 冲突并报出名字
            child = spawn_idle()
            children.append(child)
            write_marker(data, "phl", child.pid, datetime.now().astimezone().isoformat(timespec="seconds"))
            conflict = shareddata.acquire(data, "pll")
            check(conflict is not None and conflict["name"] == "PH Launcher", "应当报出对方程序名")

            # 3. 崩溃残留的死 PID 不挡启动
            (data / ".phl-running").write_text(json.dumps({"kind": "phl", "pid": 999999}), encoding="utf-8")
            check(shareddata.acquire(data, "pll") is None, "死 PID 的标记不该挡住启动")

            # 4. 心跳过期(即使 PID 还活着) → 当作对方已经不在
            write_marker(data, "phl", child.pid,
                         (datetime.now(timezone.utc) - timedelta(minutes=10)).astimezone().isoformat(timespec="seconds"))
            check(shareddata.acquire(data, "pll") is None, "心跳过期的标记不该挡住启动")

            # 5. 心跳是新的 → 仍然按"对方在运行"处理
            write_marker(data, "phl", child.pid, datetime.now().astimezone().isoformat(timespec="seconds"))
            check(shareddata.acquire(data, "pll") is not None, "心跳新的标记应当挡住启动")

            # 6. 老版本写的标记(没有时间戳) → 保守当作仍然有效
            write_marker(data, "phl", child.pid, None)
            marker = shareddata.read_marker(data, "phl")
            check(shareddata._marker_fresh(marker) is True, "没有时间戳的标记当作有效")
            check(shareddata.acquire(data, "pll") is not None, "没有时间戳的标记仍然挡启动")

            # 7. touch 刷新自己的标记, 不抢别人的
            (data / ".phl-running").unlink()
            check(shareddata.acquire(data, "pll") is None, "清掉对方标记后应当能拿到")
            before = shareddata.read_marker(data, "pll")["updated_at"]
            time.sleep(1.1)
            check(shareddata.touch(data, "pll") is True, "touch 应当成功")
            check(shareddata.read_marker(data, "pll")["updated_at"] != before, "心跳时间应当被刷新")
            write_marker(data, "pll", 999998, datetime.now().astimezone().isoformat(timespec="seconds"))
            check(shareddata.touch(data, "pll") is False, "标记被别人接管时不该抢")
            check(shareddata.read_marker(data, "pll")["pid"] == 999998, "别人的标记保持原样")

            # 8. release 只删自己的
            shareddata.release(data, "pll")
            check(shareddata.read_marker(data, "pll") is not None, "别人的标记不能被 release 删掉")
            write_marker(data, "pll", os.getpid(), datetime.now().astimezone().isoformat(timespec="seconds"))
            shareddata.release(data, "pll")
            check(shareddata.read_marker(data, "pll") is None, "自己的标记应当被删掉")
    finally:
        for child in children:
            try:
                child.kill()
            except Exception:  # noqa: BLE001
                pass

    print("shareddata mutex self-check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
