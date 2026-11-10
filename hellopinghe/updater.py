# -*- coding: utf-8 -*-
"""Lite 更新器：静默发现并缓存已校验的新版本，只有用户确认才启动安装。

检查端点按平台区分。发现合法新版本后先通知界面，再在后台单飞下载；取消只关闭
卡片，跳过才撤销下载并清理缓存。检查和下载阶段都绝不会启动安装器。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Windows 使用独立的 NSIS 渠道。旧版仍查 platform=win 并拿裸 exe，因而可安全
# 升到 1.2.8；新版查 win-nsis，未来永远只执行安装器，避免旧 updater 把 Setup
# 复制覆盖成应用 exe。发布端必须同时保留 win(兼容裸 exe)与 win-nsis(NSIS)。
CHECK_URL = "https://phix.ing/api/v1/update/check?product=phl-lite&platform={platform}"
DOWNLOAD_BASE = "https://phix.ing/media/downloads"
MAC_URL = "https://phix.ing/download/"
MAC_DOWNLOAD_URL = MAC_URL
APP_VERSION = "1.2.8"  # release automation / lead owns version bumps
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
CHECK_ERROR = "检查失败，请稍后再试。"
DOWNLOAD_ERROR = "下载或校验失败，请稍后重试。"
INSTALL_ERROR = "安装准备失败，请稍后重试。"
_log = logging.getLogger(__name__)
_lock = threading.RLock()
_state = {"stage": "idle", "version": "", "current": APP_VERSION, "notes": "", "percent": None}
_entry: dict | None = None
_worker: threading.Thread | None = None
_worker_generation = -1
_retry_queued = False
_generation = 0
_skipped: set[str] = set()


def _update_dir() -> Path:
    from . import paths
    directory = paths.data_dir() / ".updates"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def current_version() -> str:
    return APP_VERSION


def get_update_status() -> dict:
    with _lock:
        return {**_state, "current": APP_VERSION}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(value: object) -> tuple[int, ...] | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _check_url() -> str:
    return CHECK_URL.format(platform="mac" if sys.platform == "darwin" else "win-nsis")


def _check_remote(timeout: float = 10.0):
    req = urllib.request.Request(_check_url(), headers={"User-Agent": f"PHL-Lite-{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(1 << 20)
        if resp.read(1):
            raise ValueError("oversized update manifest")
    return json.loads(data.decode("utf-8"))


def _safe_url(url: object, mac: bool, version: str | None = None) -> str:
    if not isinstance(url, str):
        raise ValueError("missing download URL")
    parsed = urllib.parse.urlsplit(url)
    # 初始 URL 和重定向后都必须是 HTTPS；绝不信任 URL 提供的任意文件名。
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("unsafe download URL")
    name = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
    if mac:
        if not name.lower().endswith(".zip"):
            raise ValueError("mac update must be a zip")
    elif not re.search(r"(?:^|[-_. ])setup\.exe$", name, re.IGNORECASE):
        raise ValueError("Windows update must be an NSIS Setup.exe")
    elif version is not None and name.lower() != f"PingheLauncherLite-{version}-Setup.exe".lower():
        raise ValueError("installer filename does not match release version")
    return url


def _parse_entry(data: object, mac: bool) -> dict:
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise ValueError("invalid update manifest")
    latest = data.get("latest_version")
    if _version(latest) is None:
        raise ValueError("missing or invalid latest_version")
    entry = {"version": latest, "notes": str(data.get("release_notes") or "")[:400]}
    if _version(latest) <= _version(APP_VERSION):
        return entry
    entry["url"] = _safe_url(data.get("url"), mac, latest)
    sha = data.get("sha256")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
        raise ValueError("invalid SHA256")
    entry["sha256"] = sha.lower()
    size = data.get("size")
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD_BYTES:
            raise ValueError("invalid manifest file size")
        entry["size"] = size
    return entry


def _skipped_version(config) -> str:
    try:
        return str(getattr(config, "skipped_update_version", "") or "")
    except Exception:
        return ""


def _cache_paths(mac: bool) -> tuple[Path, Path]:
    root = _update_dir()
    name = "update.zip" if mac else "PingheLauncherLite-Setup.exe"
    return root / name, root / (name + ".json")


def _discard_cache(mac: bool) -> None:
    package, meta = _cache_paths(mac)
    for path in (package, meta, *package.parent.glob(package.name + ".part.*")):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Windows 不能删除仍被读写线程占用的文件；对应代次的线程退出时再清理。
            _log.debug("Updater cache path is currently busy")


def _write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".part")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _cached(entry: dict, mac: bool) -> Path | None:
    package, metadata = _cache_paths(mac)
    try:
        if metadata.is_symlink() or package.is_symlink():
            return None
        with metadata.open("r", encoding="utf-8") as stream:
            saved = json.load(stream)
        if not all(saved.get(key) == entry.get(key) for key in ("version", "url", "sha256", "size")):
            return None
        if package.is_symlink() or metadata.is_symlink() or not package.is_file() or not 0 < package.stat().st_size <= MAX_DOWNLOAD_BYTES:
            return None
        if entry.get("size") is not None and package.stat().st_size != entry["size"]:
            return None
        if _sha256(package) != entry["sha256"]:
            return None
        return package
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _set_state(stage: str, generation: int | None = None, **changes) -> bool:
    with _lock:
        if generation is not None and generation != _generation:
            return False
        _state.update(stage=stage, **changes)
        snapshot = {**_state, "current": APP_VERSION}
    # available 卡片由 _notify_ui 单独推一次，其余阶段走统一的进度回调。
    if stage != "available":
        _notify_ui_progress(stage, **{k: v for k, v in snapshot.items() if k != "stage"})
    return True


def _notify_ui(info: dict, attempts: int = 60, interval: float = 0.5) -> bool:
    payload = json.dumps(info, ensure_ascii=False)
    script = ("(function(){ return !!(window.__updateAvailable"
              f" && window.__updateAvailable({payload})); }})()")
    for attempt in range(attempts):
        # 首个窗口仍在加载时用户也可能已跳过该版本，重试推送前先检查。
        if info.get("version") in _skipped:
            return False
        try:
            import webview
            if webview.windows and webview.windows[0].evaluate_js(script):
                return True
        except Exception:
            pass
        if attempt + 1 < attempts:
            time.sleep(interval)
    return False


def _notify_ui_progress(stage: str, **extra) -> None:
    payload = json.dumps({"stage": stage, **extra}, ensure_ascii=False)
    script = ("(function(){ return !!(window.__updateProgress"
              f" && window.__updateProgress({payload})); }})()")
    try:
        import webview
        if webview.windows:
            webview.windows[0].evaluate_js(script)
    except Exception:
        pass


def _download(entry: dict, mac: bool, generation: int) -> None:
    package, metadata = _cache_paths(mac)
    part = package.with_name(package.name + f".part.{generation}")
    try:
        if _cached(entry, mac):
            _set_state("downloaded", generation, percent=100, size=package.stat().st_size, message="更新包已下载，等待安装。")
            return
        with _lock:
            if generation != _generation:
                return
            _discard_cache(mac)
        _set_state("downloading", generation, percent=None, message="正在后台下载更新…")
        req = urllib.request.Request(entry["url"], headers={"User-Agent": f"PHL-Lite-{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=600) as response:
            final_url = response.geturl() if hasattr(response, "geturl") else entry["url"]
            # 重定向后的文件名也必须对应同一版本，不能只在首个 URL 上检查。
            _safe_url(final_url, mac, entry["version"])
            status = getattr(response, "status", 200)
            if status != 200:
                raise ValueError("download returned non-200 status")
            raw_length = response.headers.get("Content-Length") if hasattr(response, "headers") else None
            total = int(raw_length) if raw_length and re.fullmatch(r"[0-9]+", raw_length) else None
            # 不可信的 Content-Length 不能拿来画百分比；缺失或分块传输也不能绕过大小上限。
            if total is not None and (total <= 0 or total > MAX_DOWNLOAD_BYTES or
                                      entry.get("size") is not None and total != entry["size"]):
                raise ValueError("untrusted Content-Length")
            count = 0
            digest = hashlib.sha256()
            last_percent = -1
            with part.open("wb") as stream:
                while True:
                    with _lock:
                        if generation != _generation:
                            return
                    chunk = response.read(1 << 20)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MAX_DOWNLOAD_BYTES or total is not None and count > total or (entry.get("size") and count > entry["size"]):
                        raise ValueError("download exceeds expected size")
                    digest.update(chunk)
                    stream.write(chunk)
                    if total is not None:
                        percent = min(99, int(count * 100 / total))
                        if percent != last_percent:
                            _set_state("downloading", generation, percent=percent, message="正在后台下载更新…")
                            last_percent = percent
                stream.flush()
                os.fsync(stream.fileno())
            if not count or total is not None and count != total or entry.get("size") is not None and count != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise ValueError("download size/hash mismatch")
            with _lock:
                if generation != _generation:
                    return
                os.replace(part, package)
                _write_json_atomic(metadata, {k: entry.get(k) for k in ("version", "url", "sha256", "size")})
            _set_state("downloaded", generation, percent=100, size=count, message="更新包已下载，等待安装。")
    except Exception as exc:
        _log.warning("Update download/verification failed (%s)", type(exc).__name__)
        with _lock:
            if generation == _generation:
                _discard_cache(mac)
        _set_state("error", generation, percent=None, message=DOWNLOAD_ERROR)
    finally:
        # 代次专属临时文件不能误删另一个版本正在下载的文件。
        try:
            part.unlink(missing_ok=True)
        except OSError:
            _log.warning("Could not clean updater scratch file")


def _start_download(entry: dict, mac: bool, generation: int) -> None:
    global _worker, _worker_generation, _retry_queued
    with _lock:
        if generation != _generation or _state["stage"] == "downloaded":
            return
        if _worker and _worker.is_alive() and _worker_generation == generation:
            if _state["stage"] == "error" and not _retry_queued:
                _retry_queued = True
                old = _worker
                def retry_after_exit():
                    global _retry_queued
                    old.join()
                    with _lock:
                        _retry_queued = False
                    _start_download(entry, mac, generation)
                threading.Thread(target=retry_after_exit, name="update-retry", daemon=True).start()
            return
        _retry_queued = False
        _worker_generation = generation
        _worker = threading.Thread(target=_download, args=(entry, mac, generation), name="update-download", daemon=True)
        _worker.start()


def _probe(config, mac: bool, notify: bool) -> dict:
    global _entry, _generation
    try:
        entry = _parse_entry(_check_remote(), mac)
    except Exception as exc:
        _log.warning("Update check failed (%s)", type(exc).__name__)
        return {"ok": False, "status": "failed", "current": APP_VERSION, "error": CHECK_ERROR}
    version = entry["version"]
    if _version(version) <= _version(APP_VERSION):
        return {"ok": True, "status": "up-to-date", "current": APP_VERSION}
    if version in _skipped or version == _skipped_version(config):
        return {"ok": True, "status": "skipped", "current": APP_VERSION, "version": version}
    with _lock:
        if _entry != entry:
            _generation += 1
            _entry = entry
            _state.clear()
            _state.update(stage="available", version=version, current=APP_VERSION,
                          notes=entry["notes"], percent=None)
            if entry.get("size") is not None:
                _state["size"] = entry["size"]
        generation = _generation
        state = {**_state}
    # 更新窗口可能尚未就绪，回调会重试至多 30 秒；先开下载线程才是真正的
    # 「后台预下载」，不能让界面初始化时序拖住网络请求。
    _start_download(entry, mac, generation)
    if notify:
        _notify_ui({"stage": "available", "version": version, "current": APP_VERSION,
                    "notes": entry["notes"], "percent": state["percent"]})
    state = get_update_status()
    return {"ok": True, "status": "new", "current": APP_VERSION, "version": version,
            "notes": entry["notes"], "stage": state["stage"], "percent": state["percent"]}


def check_for_update_async(config=None) -> None:
    if not getattr(sys, "frozen", False):
        return
    threading.Thread(target=lambda: _probe(config, False, True), name="update-check", daemon=True).start()


def check_for_update_mac_async(config=None) -> None:
    if not getattr(sys, "frozen", False):
        return
    threading.Thread(target=lambda: _probe(config, True, True), name="update-check-mac", daemon=True).start()


def check_for_update_manual(config=None) -> dict:
    with _lock:
        entry = _entry
        stage = _state["stage"]
        generation = _generation
    if entry and _version(entry["version"]) > _version(APP_VERSION) and stage != "idle":
        version = entry["version"]
        if version in _skipped or version == _skipped_version(config):
            return {"ok": True, "status": "skipped", "current": APP_VERSION, "version": version}
        if stage == "error":
            _start_download(entry, sys.platform == "darwin", generation)
        state = get_update_status()
        return {"ok": True, "status": "new", "current": APP_VERSION, "version": version,
                "notes": entry["notes"], "stage": state["stage"], "percent": state["percent"]}
    return _probe(config, sys.platform == "darwin", False)


def skip_update(version: str) -> None:
    """撤销正在读的版本并清缓存；界面的「取消」绝不能调用它。"""
    global _entry, _generation
    if not version:
        return
    with _lock:
        _skipped.add(str(version))
        if _entry is None or _entry["version"] != str(version):
            return
        _generation += 1
        _entry = None
        _state.clear()
        _state.update(stage="idle", version="", current=APP_VERSION, notes="", percent=None)
        _discard_cache(sys.platform == "darwin")
    _notify_ui_progress("idle", version="", current=APP_VERSION, notes="", percent=None)


def _verified_pending(mac: bool) -> Path | None:
    with _lock:
        entry = _entry
        if not entry or _state["stage"] != "downloaded" or _state["version"] != entry["version"] or entry["version"] in _skipped:
            return None
        package = _cached(entry, mac)
    if package is None:
        _set_state("error", message="更新包校验失败，请重新下载。", percent=None)
    return package


def _launch_updater(setup: Path, target_exe: Path) -> None:
    """精确等待本 PID 退出，在原安装目录静默运行 NSIS，再尝试重启。

    安装器拥有程序文件与注册项，绝不能裸复制覆盖被管理的 exe；安装失败时保留
    已校验的安装包，并且不能提前向用户宣称成功。
    """
    target_dir = target_exe.parent.resolve()
    bat = _update_dir() / "phl_updater.bat"
    # cmd 引号里不能出现控制字符和命令元字符（&|<>%! 等），否则本地路径可被注入命令。
    for value in (str(setup), str(target_exe), str(target_dir)):
        if any(c in value for c in '\r\n"&|<>%!'):
            raise ValueError("unsafe installer path")
    body = ("@echo off\r\n"
            "rem Pinghe Launcher Lite NSIS installer; user-initiated only\r\n"
            f'set "PARENT_PID={os.getpid()}"\r\n'
            ':wait\r\n'
            'tasklist /FI "PID eq %PARENT_PID%" /NH 2>nul | findstr /R /C:"^[^ ]" | findstr /C:"%PARENT_PID%" >nul\r\n'
            'if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\r\n'
            f'"{setup}" /S /D={target_dir}\r\n'
            'if errorlevel 1 exit /b 1\r\n'
            f'if not exist "{target_exe}" exit /b 1\r\n'
            f'start "" "{target_exe}"\r\n'
            'exit /b 0\r\n')
    tmp = bat.with_suffix(".bat.part")
    tmp.write_bytes(body.encode("utf-8"))
    os.replace(tmp, bat)
    subprocess.Popen(["cmd.exe", "/c", str(bat)],
                     creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                                    getattr(subprocess, "DETACHED_PROCESS", 0)), close_fds=True)


def apply_update() -> bool:
    """只有用户同意才启动「已经下载并通过重新校验」的 NSIS 安装器。"""
    if not getattr(sys, "frozen", False):
        return False
    package = _verified_pending(False)
    if package is None:
        return False  # still downloading or corrupt: never initiate another download here
    target = Path(sys.executable).resolve()
    if not target.is_file():
        return False
    try:
        _launch_updater(package, target)
        _set_state("installing", message="正在退出并准备安装…")
        return True
    except Exception as exc:
        _log.warning("Could not launch Windows installer (%s)", type(exc).__name__)
        _set_state("error", message=INSTALL_ERROR)
        return False


def _current_app_bundle() -> str:
    path = Path(sys.executable).resolve()
    for _ in range(5):
        if path.name.endswith(".app"):
            return str(path)
        if path.parent == path:
            break
        path = path.parent
    return ""


def _find_app_bundle(root: Path) -> str:
    for current, dirs, _ in os.walk(root):
        for name in dirs:
            if name.endswith(".app"):
                return str(Path(current) / name)
    return ""


def _mac_notify(title: str, body: str) -> None:
    try:
        import shlex
        script = f'display notification {shlex.quote(body)} with title {shlex.quote(title)}'
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _shq(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def _write_and_launch_swap_script(app_path: str, new_app: str, staging: Path, latest: str) -> None:
    script = staging / "swap.sh"
    trash_dir = Path.home() / ".Trash"
    trash_target = trash_dir / f"{Path(app_path).name}.old-{int(time.time())}"
    body = f'''#!/bin/bash
# 用户已确认的更新；安装失败时不能把旧应用弄没。脚本用 UTF-8 注释，bash 可直接读。
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
if kill -0 "$PID" 2>/dev/null; then exit 1; fi
mkdir -p {_shq(str(trash_dir))} || exit 1
if [ -d "$TARGET" ]; then mv "$TARGET" "$TRASH" || exit 1; fi
if ! /usr/bin/ditto "$NEW" "$TARGET"; then
  mv "$TRASH" "$TARGET" 2>/dev/null || true
  exit 1
fi
/usr/bin/xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true
/usr/bin/codesign --sign - --deep --force "$TARGET" 2>/dev/null || true
/usr/bin/open "$TARGET" 2>/dev/null || true
rm -rf "$STAGING" 2>/dev/null || true
'''
    tmp = script.with_suffix(".sh.part")
    tmp.write_text(body, encoding="utf-8")
    tmp.chmod(0o755)
    os.replace(tmp, script)
    subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_update_mac() -> bool:
    """只有用户点了「安装」才解压已校验的 zip，并准备替换 .app。"""
    package = _verified_pending(True)
    if package is None:
        return False
    app = _current_app_bundle()
    if not app:
        return False
    staging = _update_dir() / "extract"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(package), str(staging)],
                       check=True, timeout=600)
        new_app = _find_app_bundle(staging)
        if not new_app:
            raise ValueError("zip contains no app")
        _write_and_launch_swap_script(app, new_app, _update_dir(), get_update_status()["version"])
        _set_state("installing", message="正在退出并准备安装…")
        return True
    except Exception as exc:
        _log.warning("Could not install macOS update (%s)", type(exc).__name__)
        _set_state("error", message=INSTALL_ERROR)
        return False
