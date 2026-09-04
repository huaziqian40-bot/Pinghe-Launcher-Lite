"""SchoolHub 桌面应用入口: python -m schoolhub.app [--smoke]"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

# 确保 UTF-8 输出，避免 Windows 控制台编码问题
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import webview

from .bridge import Api

# UI 目录: 尝试多个位置
_APP_DIR = Path(__file__).resolve().parent          # schoolhub/app/
_PKG_DIR = _APP_DIR.parent                          # schoolhub/
_CWD_UI = Path.cwd() / "ui"                         # 从运行目录找
_PKG_UI = _PKG_DIR / "ui"                           # 从包目录找
_PARENT_UI = _APP_DIR.parents[2] / "ui"             # 项目根目录

# PyInstaller 冻结环境: 资源被解包到 sys._MEIPASS (--add-data "ui;ui")
_FROZEN_UI = Path(getattr(sys, "_MEIPASS", "")) / "ui" if getattr(sys, "frozen", False) else None

_UI_DIR = next(
    (p for p in [_FROZEN_UI, _CWD_UI, _PKG_UI, _PARENT_UI] if p and p.exists()),
    _CWD_UI,
)


def main() -> None:
    smoke = "--smoke" in sys.argv
    api = Api()
    window = webview.create_window(
        "SchoolHub",
        str(_UI_DIR / "index.html"),
        js_api=api,
        width=1340,
        height=860,
        min_size=(1080, 720),
        background_color="#fbfaf6",
    )

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

    webview.start()


if __name__ == "__main__":
    main()
