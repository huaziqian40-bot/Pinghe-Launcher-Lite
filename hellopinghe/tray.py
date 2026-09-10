"""系统托盘图标 + 单实例守护.

两块功能都在"用户不重新开窗口"的体验上:

- **单实例**: 首个实例独占绑定 127.0.0.1 的一个端口作为"我正在跑"标志;
  再次双击 exe 时, 新进程连上去发一个 ``show`` 信号就退出 —— 不重复开窗,
  而是把已有实例的主界面唤到前台(顺便从托盘里恢复出来)。
- **托盘**: 图标常驻。关闭窗口 = 隐藏窗口(应用继续留在托盘里跑),
  右键菜单提供: 打开主界面 / 各功能快捷入口 / 退出。

仅在 Windows 上有托盘(其它平台 create_tray 返回 None, 不影响主流程)。
"""
from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

#: 本机回环端口(只绑 127.0.0.1, 不对外; 仅作为"已有实例"的锁与唤醒通道)
PORT = 51237
_HOST = "127.0.0.1"

#: 托盘菜单的功能快捷入口(与前端 show(view) 的 view 名一一对应)
QUICK_VIEWS: list[tuple[str, str]] = [
    ("🏠 首页", "home"),
    ("📅 我的课表", "timetable"),
    ("🗓️ 我的日程", "schedule"),
    ("📚 我的课程", "courses"),
    ("✉️ 平和邮箱", "mail"),
    ("🌿 心履", "xinlv"),
    ("🤖 Agent 助手", "agent"),
    ("⚙️ 设置", "settings"),
]


class SingleInstance:
    """回环端口独占绑定 = 单实例锁 + 唤醒通道(无需额外依赖)."""

    def __init__(self, port: int = PORT):
        self.port = port
        self._sock: socket.socket | None = None

    def acquire(self) -> bool:
        """True = 本进程是第一个实例(已拿到锁)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((_HOST, self.port))
            s.listen(4)
        except OSError:
            s.close()
            return False
        self._sock = s
        return True

    def notify_existing(self, timeout: float = 2.0) -> bool:
        """唤醒已在运行的实例(让它显示主界面)."""
        try:
            with socket.create_connection((_HOST, self.port), timeout=timeout) as c:
                c.sendall(b"show")
            return True
        except OSError:
            return False

    def listen(self, on_show) -> None:
        """后台线程: 收到 show 信号就回调."""
        sock = self._sock
        if sock is None:
            return

        def loop():
            while True:
                try:
                    conn, _ = sock.accept()
                except OSError:
                    return          # 端口已释放(进程退出中)
                try:
                    data = conn.recv(16)
                    if data.strip() == b"show":
                        on_show()
                except OSError:
                    pass
                finally:
                    try:
                        conn.close()
                    except OSError:
                        pass

        threading.Thread(target=loop, name="single-instance", daemon=True).start()

    def release(self) -> None:
        try:
            if self._sock is not None:
                self._sock.close()
                self._sock = None
        except OSError:
            pass


def _icon_image():
    """托盘图标: 优先复用应用 logo(冻结时在 _MEIPASS/ui 下)."""
    from PIL import Image

    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ui" / "logo.png")
    here = Path(__file__).resolve().parent
    candidates += [here.parent / "ui" / "logo.png", Path.cwd() / "ui" / "logo.png"]
    for p in candidates:
        try:
            if p.exists():
                return Image.open(p).convert("RGBA")
        except Exception:  # noqa: BLE001
            continue
    return Image.new("RGBA", (64, 64), (31, 90, 70, 255))


def create_tray(window, on_quit, on_show=None):
    """创建托盘图标(后台线程跑, 不阻塞主流程); 不支持时返回 None."""
    if sys.platform != "win32":
        return None
    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception:  # noqa: BLE001
        return None

    def _reveal() -> None:
        if on_show is not None:
            on_show()
            return
        try:
            window.show()
        except Exception:  # noqa: BLE001
            pass

    def _menu_handler(*_args):
        _reveal()

    def _goto(view: str):
        def handler(*_args):
            _reveal()
            try:
                window.evaluate_js(f"show('{view}')")
            except Exception:  # noqa: BLE001
                pass
        return handler

    def _quit(*_args):
        on_quit()

    items = [MenuItem("打开主界面", _menu_handler, default=True), Menu.SEPARATOR]
    items += [MenuItem(label, _goto(view)) for label, view in QUICK_VIEWS]
    items += [Menu.SEPARATOR, MenuItem("退出", _quit)]

    try:
        icon = pystray.Icon(
            "pinghe-launcher-lite", _icon_image(), "Pinghe Launcher Lite",
            Menu(*items),
        )
        threading.Thread(target=icon.run, name="tray", daemon=True).start()
        return icon
    except Exception:  # noqa: BLE001
        return None
