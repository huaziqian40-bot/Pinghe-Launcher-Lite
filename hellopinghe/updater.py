# -*- coding: utf-8 -*-
"""Pinghe Launcher Lite 应用内自动更新（Windows 单文件 exe）。

流程（启动时后台线程执行，不阻塞主界面）：
1. GET https://phix.ing/api/v1/update/check?product=phl-lite&platform=win
2. 有新版本 → 下载新 exe 到数据目录的 .updates/ 临时位置
3. SHA256 校验（与清单比对，不匹配丢弃）
4. 写 updater.bat（等待本进程退出 → 覆盖 exe → 重启 → 删自身），启动它并退出

安装版（%LOCALAPPDATA%\\Programs\\PingheLauncherLite\\PingheLauncherLite.exe）
与便携版（解压目录）都适用 —— 都通过 sys.executable 定位要替换的 exe。
用户数据不落在 exe 目录（在 data 目录），替换 exe 不影响任何数据。

macOS 未签名：不走自动替换（Gatekeeper），有新版时提示去官网下载页。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

CHECK_URL = "https://phix.ing/api/v1/update/check?product=phl-lite&platform=win"
DOWNLOAD_BASE = "https://phix.ing/media/downloads"
MAC_URL = "https://phix.ing/download/"

#: 打包时的版本号（发布时由构建/发布脚本更新；源码运行取 0.0.0 表示"开发版，不更新"）
APP_VERSION = "1.2.1"

#: 替换用临时目录（数据目录下，与 exe 可能不同盘也 OK —— 但替换仍需同盘，见 _launch_updater）
def _update_dir() -> Path:
    from . import paths

    d = paths.data_dir() / ".updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def current_version() -> str:
    return APP_VERSION


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_remote(timeout: float = 10.0):
    req = urllib.request.Request(CHECK_URL, headers={"User-Agent": f"PHL-Lite-{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    import json

    return json.loads(data.decode("utf-8"))


def _launch_updater(new_exe: Path, target_exe: Path) -> None:
    """写一个 bat：等本进程退出 → 覆盖目标 exe → 重启 → 删自身。"""
    bat = _update_dir() / "phl_updater.bat"
    with open(bat, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(
            "@echo off\r\n"
            "rem PH Launcher Lite auto-updater (generated)\r\n"
            f'cd /d "%~dp0"\r\n'
            f':wait\r\n'
            f'tasklist /FI "IMAGENAME eq {os.path.basename(target_exe)}" 2>nul | find /I "{os.path.basename(target_exe)}" >nul\r\n'
            f'if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\r\n'
            f'copy /Y "{new_exe}" "{target_exe}" >nul\r\n'
            f'del /F /Q "{new_exe}" >nul 2>nul\r\n'
            f'start "" "{target_exe}"\r\n'
            f'del /F /Q "%~f0" >nul 2>nul\r\n'
        )
    # 命令行里带引号路径；DETACHED 让 bat 脱离本进程独立存活
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS),
        close_fds=True,
    )


def _apply_update(entry) -> bool:
    """下载 → 校验 → 替换。返回是否已成功接管（True 时调用方应立即退出）。"""
    from . import paths

    # 便携版/安装版都直接替换 sys.executable；源码运行时（非冻结）不自动更新
    if not getattr(sys, "frozen", False):
        return False
    target = Path(sys.executable).resolve()
    if not target.exists():
        return False

    url = entry.get("url") or ""
    sha = str(entry.get("sha256") or "").lower()
    if not url or not sha:
        return False

    # 下载到数据目录的 .updates/（保证与后续替换逻辑简单）
    tmp = _update_dir() / "phl_lite_new.exe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"PHL-Lite-{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        if _sha256(tmp) != sha:
            tmp.unlink(missing_ok=True)
            return False
    except Exception:  # noqa: BLE001  网络/下载失败静默跳过，下次启动再试
        tmp.unlink(missing_ok=True)
        return False

    # 替换 exe 必须同盘（copy 在同一盘才能工作）：目标 exe 与临时目录分属两盘时，
    # 把临时文件挪到目标的同盘临时目录。
    if tmp.drive.lower() != target.drive.lower():
        same_drive_tmp = Path(tempfile.gettempdir()) / "phl_lite_new.exe"
        try:
            same_drive_tmp.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "rb") as src_f, open(same_drive_tmp, "wb") as dst_f:
                while True:
                    chunk = src_f.read(1 << 20)
                    if not chunk:
                        break
                    dst_f.write(chunk)
            tmp.unlink(missing_ok=True)
            tmp = same_drive_tmp
        except OSError:
            tmp.unlink(missing_ok=True)
            return False

    _launch_updater(tmp, target)
    return True


def check_for_update_async() -> None:
    """后台线程：检查更新。有新版且下载替换成功则让主进程退出。"""

    def _run() -> None:
        try:
            if getattr(sys, "frozen", False):
                data = _check_remote()
                if not data.get("ok"):
                    return
                latest = str(data.get("latest_version") or "")
                if not latest or latest == APP_VERSION:
                    return
                if current := data.get("platforms") or data.get("url"):
                    pass
                # 响应结构：{ok, latest_version, url, sha256, size, ...}
                entry = {"url": data.get("url"), "sha256": data.get("sha256")}
                if _apply_update(entry):
                    # 替换脚本已接管，稍等它写盘后退出当前实例
                    import time as _t

                    _t.sleep(1.5)
                    try:
                        if sys.platform == "win32":
                            os._exit(0)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001  任何失败都不影响启动
            pass

    threading.Thread(target=_run, name="auto-updater", daemon=True).start()


# ---- macOS：未签名，半自动（提示打开下载页）----

def _mac_notify(title: str, body: str) -> None:
    """macOS 用 osascript 发通知（未签名环境最省事，无需权限）。"""
    try:
        import shlex

        script = f'display notification {shlex.quote(body)} with title {shlex.quote(title)}'
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def check_for_update_mac_async() -> None:
    """启动后异步检查 macOS 版本；有新版时发系统通知提示去官网下载。"""

    def _run() -> None:
        if not getattr(sys, "frozen", False):
            return
        try:
            data = _check_remote()
            if not data.get("ok") or str(data.get("latest_version") or "") == APP_VERSION:
                return
            latest = str(data.get("latest_version"))
            _mac_notify("发现新版本 Pinghe Launcher Lite",
                        f"v{latest} 已发布，请前往官网下载更新。")
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, name="auto-updater-mac", daemon=True).start()