# -*- coding: utf-8 -*-
"""无边框窗口的"窗口控件"（用户 2026-09-21 要求：不要一个单独的标题栏框，
要像 PH Launcher 那样是软件自己的一部分）。

做完的事：

* 窗口 `frameless=True`（pywebview 设 `FormBorderStyle=None`，系统不再画那条标题栏）；
* **不加** `WS_THICKFRAME`：它会围出一圈系统色的非客户区边框带，看起来就是"窗口四周的白边"
  （用户 2026-09-21 实测反馈）。缩放改由界面自己的边缘把手调 `set_bounds()` 实现；
* 保留 MINIMIZEBOX / MAXIMIZEBOX / SYSMENU：任务栏按钮、Win+方向键、Alt+Space 都靠它们；
* `drag_start()`：在自绘的可拖动区域上按下时，交给系统的**原生拖动循环**
  （`ReleaseCapture` + `WM_NCLBUTTONDOWN`/`HTCAPTION`）——原生拖动带 Aero Snap，
  比自己算坐标改窗口位置稳得多；
* `maximize_toggle()`：自己按**工作区**铺满/还原。不用 `WindowState.MAXIMIZED`，
  因为无边框窗口那样最大化会盖住任务栏。

拿不到句柄、系统不支持、非 Windows —— 一律安静跳过：这只是窗口观感，
绝不该让程序起不来。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes  # noqa: F401  (RECT 等结构体；只 import ctypes 不保证它已加载)
import sys

#: Win32 常量（只在 Windows 上用到）
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_SYSMENU = 0x00080000
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
SPI_GETWORKAREA = 0x0030

_state: dict = {"hwnd": 0, "maximized": False, "restore": None}


def _user32():
    return ctypes.windll.user32


def find_hwnd(title: str = "Pinghe Launcher Lite") -> int:
    """按标题找主窗口句柄（pywebview 的窗口类名不稳定，标题最可靠）。"""
    if sys.platform != "win32":
        return 0
    try:
        return int(_user32().FindWindowW(None, title) or 0)
    except Exception:  # noqa: BLE001
        return 0


def apply(window, title: str = "Pinghe Launcher Lite", *, resizable: bool = True) -> int:
    """无边框窗口的样式修补；返回句柄（拿不到就 0）。

    2026-09-21 用户实测反馈「窗口四周有一圈白边」—— 那是 `WS_THICKFRAME` 的**非客户区边框带**
    （WinForms 把它涂成系统色，看着就是一圈白）。所以这里**不再加** THICKFRAME：
    缩放改由界面自己的边缘把手做（`win_set_bounds`），一圈白边就没了。
    MIN/MAXBOX/SYSMENU 要留着：任务栏按钮、Win+方向键、Alt+Space 菜单都靠它们。
    """
    hwnd = find_hwnd(title)
    if not hwnd:
        return 0
    _state["hwnd"] = hwnd
    try:
        style = _user32().GetWindowLongW(hwnd, GWL_STYLE)
        style |= WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
        style &= ~WS_THICKFRAME          # 去掉那圈白边（缩放交给界面的把手）
        _user32().SetWindowLongW(hwnd, GWL_STYLE, style)
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x2, 0x1, 0x4, 0x20
        x, y, w, h = _rect_of(hwnd)
        _user32().SetWindowPos(
            hwnd, 0, x, y, w, h,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    except Exception:  # noqa: BLE001
        pass
    return hwnd


def _rect_of(hwnd: int) -> tuple[int, int, int, int]:
    rect = ctypes.wintypes.RECT()
    _user32().GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


#: 界面拖把手时允许的最小尺寸（物理像素；比向导能看下的尺寸略小）
MIN_WIDTH, MIN_HEIGHT = 1000, 640


def set_bounds(x: int, y: int, width: int, height: int) -> dict:
    """把窗口摆到指定位置/大小（界面的缩放手把手用这个）。"""
    hwnd = _state.get("hwnd") or find_hwnd()
    if not hwnd:
        return {"ok": False, "error": "找不到窗口"}
    width = max(MIN_WIDTH, int(width))
    height = max(MIN_HEIGHT, int(height))
    try:
        # SWP_NOZORDER | SWP_NOACTIVATE：拖动时别抢焦点、别改层叠顺序
        _user32().SetWindowPos(hwnd, 0, int(x), int(y), width, height, 0x14)
        return {"ok": True, "bounds": _rect_of(hwnd)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def get_bounds() -> dict:
    hwnd = _state.get("hwnd") or find_hwnd()
    if not hwnd:
        return {"ok": False}
    return {"ok": True, "bounds": _rect_of(hwnd)}


def drag_start() -> bool:
    """原生拖动（自绘标题栏按下时调）。带 Aero Snap，比自己算坐标稳。"""
    hwnd = _state.get("hwnd") or find_hwnd()
    if not hwnd or sys.platform != "win32":
        return False
    try:
        _user32().ReleaseCapture()
        _user32().SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def work_area() -> tuple[int, int, int, int]:
    """主屏工作区（不含任务栏）：(x, y, w, h)。"""
    try:
        rect = ctypes.wintypes.RECT()
        if _user32().SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:  # noqa: BLE001
        pass
    return (0, 0, 1280, 800)


def window_rect() -> tuple[int, int, int, int]:
    hwnd = _state.get("hwnd") or find_hwnd()
    try:
        rect = ctypes.wintypes.RECT()
        _user32().GetWindowRect(hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:  # noqa: BLE001
        return (0, 0, 1340, 860)


def maximize_toggle(window) -> bool:
    """铺满工作区 / 还原。返回切换后是不是"最大化"状态。"""
    if window is None:
        return False
    if _state.get("maximized") and _state.get("restore"):
        x, y, w, h = _state["restore"]
        _state["maximized"] = False
        _state["restore"] = None
    else:
        _state["restore"] = window_rect()
        x, y, w, h = work_area()
        _state["maximized"] = True
    try:
        window.move(int(x), int(y))
        window.resize(int(w), int(h))
    except Exception:  # noqa: BLE001
        pass
    return bool(_state.get("maximized"))


def is_maximized() -> bool:
    return bool(_state.get("maximized"))