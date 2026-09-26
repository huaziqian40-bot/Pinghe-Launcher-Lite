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
import time
import urllib.parse
import urllib.request
from pathlib import Path

CHECK_URL = "https://phix.ing/api/v1/update/check?product=phl-lite&platform=win"
DOWNLOAD_BASE = "https://phix.ing/media/downloads"
MAC_URL = "https://phix.ing/download/"

#: 打包时的版本号（发布时由构建/发布脚本更新；源码运行取 0.0.0 表示"开发版，不更新"）
APP_VERSION = "1.2.4"

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


def _notify_ui(info: dict) -> None:
    """把"发现新版本"推给前端，由它弹卡片让用户选。

    **只通知，不下载**：用户在卡片上点「更新」之后才会走下载/替换（见 apply_update）。
    """
    try:
        import json

        import webview

        if webview.windows:
            payload = json.dumps(info, ensure_ascii=False)
            webview.windows[0].evaluate_js(
                f"window.__updateAvailable && window.__updateAvailable({payload});"
            )
    except Exception:  # noqa: BLE001
        pass


def check_for_update_async(config=None) -> None:
    """后台线程：**只检查**是否有新版本；有就通知界面弹卡片。

    绝不自动下载或替换 —— 那是用户点「更新」之后的事（apply_update）。
    config 用来读「跳过本版本」：同一个版本被跳过过就不再提示。
    """

    def _run() -> None:
        try:
            if not getattr(sys, "frozen", False):
                return
            data = _check_remote()
            if not data.get("ok"):
                return
            latest = str(data.get("latest_version") or "")
            if not latest or latest == APP_VERSION:
                return
            skipped = ""
            try:
                skipped = str(getattr(config, "skipped_update_version", "") or "")
            except Exception:  # noqa: BLE001
                skipped = ""
            if skipped and skipped == latest:
                return  # 用户点过「跳过本版本」→ 静默
            _notify_ui({
                "version": latest,
                "current": APP_VERSION,
                "notes": str(data.get("release_notes") or ""),
            })
        except Exception:  # noqa: BLE001  任何失败都不影响启动
            pass

    threading.Thread(target=_run, name="update-check", daemon=True).start()


def apply_update() -> bool:
    """**用户点过「更新」之后**才调用：重新取一次载荷信息 → 下载 → 校验 → 替换。

    成功返回 True（调用方应尽快退出，把舞台交给替换脚本）。
    """
    try:
        data = _check_remote()
        if not data or not data.get("ok"):
            _notify_ui_progress("error", message="拿不到更新信息，请稍后再试。")
            return False
        entry = {"url": data.get("url"), "sha256": data.get("sha256")}
        _notify_ui_progress("downloading", percent=0)
        if not _apply_update(entry):
            _notify_ui_progress("error", message="下载或校验失败，请稍后再试。")
            return False
        _notify_ui_progress("applying", message="正在替换新版本…")
        return True
    except Exception as exc:  # noqa: BLE001
        _notify_ui_progress("error", message=str(exc))
        return False


def _notify_ui_progress(stage: str, **extra) -> None:
    """把更新进度推给前端卡片显示。"""
    try:
        import json

        import webview

        if webview.windows:
            payload = json.dumps({"stage": stage, **extra}, ensure_ascii=False)
            webview.windows[0].evaluate_js(
                f"window.__updateProgress && window.__updateProgress({payload});"
            )
    except Exception:  # noqa: BLE001
        pass


# ---- macOS：自动下载 zip → 替换 .app → 清 quarantine → ad-hoc 重签 → 重启 ----
#
# 未签名 .app 也能被自己替换：替换后清掉 com.apple.quarantine 再做 ad-hoc 重签，
# 就不会显示"应用已损坏"；用户只需在新版本**首次启动时右键 →「打开」**一次
# （Gatekeeper 对未签名应用的固有要求，无法绕过）。
# 自动替换准备失败时降级：把新版放进「下载」并用 Finder 显示，用户拖一下即可。

MAC_DOWNLOAD_URL = "https://phix.ing/download/"


def _mac_notify(title: str, body: str) -> None:
    """macOS 用 osascript 发通知（未签名环境最省事，无需权限）。"""
    try:
        import shlex

        script = f'display notification {shlex.quote(body)} with title {shlex.quote(title)}'
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def _current_app_bundle() -> str:
    """当前 .app 路径（从 sys.executable 上溯找 .app）。非 .app 运行返回空串。"""
    p = Path(sys.executable).resolve()
    for _ in range(5):
        if p.name.endswith(".app"):
            return str(p)
        if p.parent == p:
            break
        p = p.parent
    return ""


def _find_app_bundle(root: Path) -> str:
    """在解压目录里找 .app（zip 顶层可能直接是 .app，也可能包一层目录）。"""
    for cur, dirs, _files in os.walk(root):
        for d in dirs:
            if d.endswith(".app"):
                return str(Path(cur) / d)
    return ""


def _download_file(url: str, dest: Path, timeout: float = 600.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": f"PHL-Lite-{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)


def _stage_mac_update(entry, latest: str) -> bool:
    """下载 zip → 校验 → 解压 → 写替换脚本并启动。成功则调用方应退出主进程。"""
    from . import paths

    url = str(entry.get("url") or "")
    sha = str(entry.get("sha256") or "").lower()
    if not url.endswith(".zip") or not sha:
        return False

    app_path = _current_app_bundle()
    if not app_path:
        return False  # 开发模式（非 .app）不自动替换

    staging = paths.data_dir() / ".update-staging"
    zip_path = staging / "update.zip"
    try:
        if staging.exists():
            import shutil as _sh

            _sh.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        _download_file(url, zip_path)

        if _sha256(zip_path) != sha:
            import shutil as _sh

            _sh.rmtree(staging, ignore_errors=True)
            return False  # 坏包：丢弃

        # macOS 自带 ditto：解 zip 时保留权限位与符号链接（用 zipfile 会丢）
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zip_path), str(staging)],
                       check=True, timeout=600)
        zip_path.unlink(missing_ok=True)

        new_app = _find_app_bundle(staging)
        if not new_app:
            import shutil as _sh

            _sh.rmtree(staging, ignore_errors=True)
            return False

        _write_and_launch_swap_script(app_path, new_app, staging, latest)
        return True
    except Exception:  # noqa: BLE001
        import shutil as _sh

        _sh.rmtree(staging, ignore_errors=True)
        return False


def _write_and_launch_swap_script(app_path: str, new_app: str, staging: Path, latest: str) -> None:
    """写 bash 脚本：等本进程退出 → 旧 .app 进废纸篓 → 新 .app 就位 → 清隔离 → 重签 → 重启。"""
    script = staging / "swap.sh"
    trash_dir = Path.home() / ".Trash"
    trash_target = trash_dir / f"{Path(app_path).name}.old-{int(time.time())}"
    body = f"""#!/bin/bash
# Pinghe Launcher Lite 自动更新替换脚本（生成的）
set -u
TARGET={_shq(app_path)}
NEW={_shq(new_app)}
STAGING={_shq(str(staging))}
TRASH={_shq(str(trash_target))}
PID={os.getpid()}

for i in $(seq 1 60); do
  if ! kill -0 "$PID" 2>/dev/null; then break; fi
  sleep 1
done
sleep 1

if [ -d "$TARGET" ]; then
  mkdir -p {_shq(str(trash_dir))} 2>/dev/null || true
  mv "$TARGET" "$TRASH" 2>/dev/null || rm -rf "$TARGET"
fi

/usr/bin/ditto "$NEW" "$TARGET" || exit 1
/usr/bin/xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true
/usr/bin/codesign --sign - --deep --force "$TARGET" 2>/dev/null || true
/usr/bin/open "$TARGET" 2>/dev/null || true
rm -rf "$STAGING" 2>/dev/null || true
"""
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _mac_notify("Pinghe Launcher Lite 正在更新",
                f"退出后将自动替换为 v{latest}；下次打开请右键 →「打开」一次。")


def _shq(s: str) -> str:
    """shell 单引号转义。"""
    return "'" + str(s).replace("'", "'\\''") + "'"


def _mac_fallback(latest: str) -> None:
    """自动替换准备失败时的降级：提示 + 打开下载页（至少不让用户自己找）。"""
    _mac_notify("发现新版本 Pinghe Launcher Lite",
                f"v{latest} 已发布，请前往官网下载更新。")
    try:
        subprocess.Popen(["open", MAC_DOWNLOAD_URL],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def check_for_update_mac_async(config=None) -> None:
    """macOS：**只检查**是否有新版本，有就通知界面弹卡片（绝不自动替换）。

    config 用来读「跳过本版本」。
    """

    def _run() -> None:
        if not getattr(sys, "frozen", False):
            return
        try:
            data = _check_remote()
            if not data.get("ok"):
                return
            latest = str(data.get("latest_version") or "")
            if not latest or latest == APP_VERSION:
                return
            skipped = str(getattr(config, "skipped_update_version", "") or "")
            if skipped and skipped == latest:
                return  # 用户点过「跳过本版本」→ 静默
            _notify_ui({
                "version": latest,
                "current": APP_VERSION,
                "notes": str(data.get("release_notes") or ""),
            })
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_run, name="update-check-mac", daemon=True).start()


def apply_update_mac() -> bool:
    """**用户点过「更新」之后**才调用：下载 zip → 校验 → 解压 → 写替换脚本。

    成功返回 True（调用方应尽快退出，脚本会替换并重启）。
    """
    try:
        data = _check_remote()
        if not data or not data.get("ok"):
            _notify_ui_progress("error", message="拿不到更新信息，请稍后再试。")
            return False
        latest = str(data.get("latest_version") or "")
        _notify_ui_progress("downloading", percent=0)
        if not _stage_mac_update(data, latest):
            _notify_ui_progress("error", message="下载或校验失败，已为你打开下载页。")
            _mac_fallback(latest)
            return False
        _notify_ui_progress("applying", message="正在替换应用…")
        return True
    except Exception as exc:  # noqa: BLE001
        _notify_ui_progress("error", message=str(exc))
        return False