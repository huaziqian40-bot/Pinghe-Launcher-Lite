"""Hello Pinghe! Launcher 桌面应用入口: python -m hellopinghe.app [--smoke]"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from pathlib import Path

# 确保 UTF-8 输出，避免 Windows 控制台编码问题
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from .. import paths
from .bridge import Api

# UI 目录: 尝试多个位置
_APP_DIR = Path(__file__).resolve().parent          # hellopinghe/app/
_PKG_DIR = _APP_DIR.parent                          # hellopinghe/
_CWD_UI = Path.cwd() / "ui"                         # 从运行目录找
_PKG_UI = _PKG_DIR / "ui"                           # 从包目录找
_PARENT_UI = _APP_DIR.parents[2] / "ui"             # 项目根目录

# PyInstaller 冻结环境: 资源被解包到 sys._MEIPASS (--add-data "ui;ui")
_FROZEN_UI = Path(getattr(sys, "_MEIPASS", "")) / "ui" if getattr(sys, "frozen", False) else None

_UI_DIR = next(
    (p for p in [_FROZEN_UI, _CWD_UI, _PKG_UI, _PARENT_UI] if p and p.exists()),
    _CWD_UI,
)

# WebView2 Evergreen Bootstrapper(微软官方, 随包分发): 缺运行时的机器静默安装
_WV2_BOOTSTRAP = Path(getattr(sys, "_MEIPASS", "")) / "MicrosoftEdgeWebview2Setup.exe"

# 崩溃日志
_LOG_DIR = paths.data_dir() / "logs"


def _log_error(stage: str, exc: BaseException) -> None:
    """崩溃写日志 + Win 弹窗, 绝不无声闪退。"""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = _LOG_DIR / "error.log"
        with log.open("a", encoding="utf-8") as fh:
            import datetime

            fh.write(f"\n[{datetime.datetime.now().isoformat()}] {stage}\n")
            fh.write(traceback.format_exc())
    except Exception:  # noqa: BLE001
        pass
    try:
        if sys.platform == "win32":
            import ctypes

            msg = (f"{stage}\n\n{exc}\n\n详情见日志:\n{_LOG_DIR / 'error.log'}")
            ctypes.windll.user32.MessageBoxW(0, msg, "Hello Pinghe! Launcher 启动失败", 0x10)
    except Exception:  # noqa: BLE001
        pass


def _webview2_runtime_ok() -> bool:
    """按微软官方文档检测 WebView2 运行时(HKLM/HKCU 两处)."""
    if sys.platform != "win32":
        return True
    import winreg

    key = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, key):
                return True
        except OSError:
            continue
    return False


def _ensure_webview2() -> None:
    """Windows: 缺 WebView2 运行时 → 运行捆绑的 Evergreen Bootstrapper 静默安装."""
    if sys.platform != "win32" or _webview2_runtime_ok():
        return
    boot = _WV2_BOOTSTRAP
    if not boot.exists():
        boot = Path("MicrosoftEdgeWebview2Setup.exe")
    if not boot.exists():
        raise RuntimeError(
            "这台电脑缺少 WebView2 运行时, 且安装包里没有找到引导安装器。\n"
            "请联网安装: https://developer.microsoft.com/microsoft-edge/webview2/")
    import subprocess

    subprocess.run([str(boot), "/silent", "/install"], timeout=600, check=False)
    if not _webview2_runtime_ok():
        raise RuntimeError(
            "WebView2 运行时自动安装未成功(可能需要联网), 请手动安装后重试:\n"
            "https://developer.microsoft.com/microsoft-edge/webview2/")


def main() -> None:
    smoke = "--smoke" in sys.argv
    try:
        _ensure_webview2()
        import webview   # noqa: E402  (在 WebView2 检测之后导入)

        api = Api()
        window = webview.create_window(
            "Hello Pinghe! Launcher",
            str(_UI_DIR / "index.html"),
            js_api=api,
            width=1340,
            height=860,
            min_size=(1080, 720),
            background_color="#fbfaf6",
        )
    except Exception as exc:  # noqa: BLE001
        _log_error("启动失败", exc)
        sys.exit(1)

    if smoke:
        def close_later():
            time.sleep(5)
            try:
                result = window.evaluate_js(
                    "(document.getElementById('app') ? 'dom-ok' : 'dom-missing') + '|' + "
                    "((typeof window.__agentEvent === 'function' "
                    "&& typeof boot === 'function' && typeof loadGradett === 'function') "
                    "? 'js-ok' : 'js-broken')"
                )
                print(f"SMOKE_JS: {result}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"SMOKE_JS_ERR: {exc}", flush=True)
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=close_later, daemon=True).start()

    try:
        webview.start()
    except Exception as exc:  # noqa: BLE001
        _log_error("运行时崩溃", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
