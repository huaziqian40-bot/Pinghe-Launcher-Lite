r"""Hello Pinghe! Launcher 安装程序(替代 WiX MSI).

- 默认安装目录: D:\Program Files\HPHL(D 盘不存在则 C:\Program Files\HPHL), 可浏览修改
- 写入 portable.flag → 应用的所有数据(配置/数据库/缓存/密钥)都存在安装目录\data
- 可选: 桌面快捷方式 / 固定到任务栏; 无论选择都会写入"已安装应用"列表与卸载器
- 卸载: 同目录 Uninstall.exe(本程序副本), 可选保留用户数据

构建: python -m PyInstaller --noconfirm --clean installer/installer.spec (需先构建好 app exe)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import traceback
import winreg
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_EXE = "HelloPingheLauncher.exe"
UNINSTALL_EXE = "Uninstall.exe"
APP_NAME = "Hello Pinghe! Launcher"
REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\HelloPingheLauncher"
LNK_NAME = "Hello Pinghe! Launcher.lnk"
VERSION = "1.0.0"

BUNDLED_APP = Path(getattr(sys, "_MEIPASS", ".")) / APP_EXE


def default_install_dir() -> str:
    for drv in ("D:\\", "C:\\"):
        if os.path.exists(drv):
            return os.path.join(drv, "Program Files", "HPHL")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    return os.path.join(pf, "HPHL")


def is_admin() -> bool:
    try:
        import ctypes

        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:  # noqa: BLE001
        return False


def ps(script: str) -> None:
    """跑一段 PowerShell(创建/删除快捷方式用), 失败抛异常."""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-Command", script], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "PowerShell 执行失败")


def _desktop_path() -> str:
    """当前用户桌面路径(从注册表读, 处理 OneDrive 重定向)."""
    import winreg

    for root, path in (
        (winreg.HKEY_CURRENT_USER,
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"),
        (winreg.HKEY_CURRENT_USER,
         r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"),
    ):
        try:
            with winreg.OpenKey(root, path) as k:
                val, _ = winreg.QueryValueEx(k, "Desktop")
                return os.path.expandvars(val)
        except OSError:
            continue
    return os.path.join(os.path.expanduser("~"), "Desktop")


def _ps_quote(s: str) -> str:
    """转义 PowerShell 单引号字符串里的单引号."""
    return s.replace("'", "''")


def _make_lnk(lnk_path: str, target_exe: str, workdir: str) -> None:
    """用 PowerShell WScript.Shell 创建 .lnk 快捷方式.

    所有路径在 Python 里解析为字面值再传入, 不用 PowerShell 表达式,
    彻底避免嵌套引号/特殊字符问题。
    """
    lnk = _ps_quote(lnk_path)
    tgt = _ps_quote(target_exe)
    wd = _ps_quote(workdir)
    ps(f"$ws = New-Object -ComObject WScript.Shell; "
       f"$s = $ws.CreateShortcut('{lnk}'); "
       f"$s.TargetPath = '{tgt}'; "
       f"$s.WorkingDirectory = '{wd}'; "
       f"$s.IconLocation = '{tgt},0'; "
       f"$s.Save()")


def make_shortcuts(target: str, desktop: bool, startmenu: bool, taskbar: bool) -> list[str]:
    made = []
    exe = os.path.join(target, APP_EXE)
    workdir = target

    if desktop:
        lnk = os.path.join(_desktop_path(), LNK_NAME)
        _make_lnk(lnk, exe, workdir)
        made.append(lnk)
    if startmenu:
        sm = os.path.expandvars(
            r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")
        lnk = os.path.join(sm, LNK_NAME)
        _make_lnk(lnk, exe, workdir)
        made.append(lnk)
    if taskbar:
        # 早期固定机制(Win10 有效; Win11 可能不生效, 失败不阻塞)
        ql = os.path.expandvars(
            r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar")
        lnk = os.path.join(ql, LNK_NAME)
        try:
            os.makedirs(ql, exist_ok=True)
            _make_lnk(lnk, exe, workdir)
            made.append(lnk)
        except Exception:  # noqa: BLE001
            pass
    return made


def register_app(target: str) -> None:
    exe = os.path.join(target, APP_EXE)
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "Hello Pinghe")
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, target)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, exe)
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ,
                          f'"{os.path.join(target, UNINSTALL_EXE)}" /uninstall')
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        size_kb = sum(f.stat().st_size for f in Path(target).rglob("*") if f.is_file()) // 1024
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)


def unregister_app() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, REG_KEY)
    except OSError:
        pass


def remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


class InstallerUI:
    def __init__(self, root: tk.Tk, uninstall: bool) -> None:
        self.root = root
        self.uninstall = uninstall
        self.target = os.environ.get("HPHL_TARGET", "") or default_install_dir()
        root.title(f"{APP_NAME} 安装程序")
        root.geometry("640x480")
        root.minsize(520, 400)

        # 让列/行可伸缩, 元素跟随窗口大小
        root.columnconfigure(0, weight=0)   # 标签列
        root.columnconfigure(1, weight=1)   # 内容列(伸缩)
        root.columnconfigure(2, weight=0)   # 按钮列
        root.rowconfigure(7, weight=1)      # 日志区(伸缩)

        pad = {"padx": 14, "pady": 6, "sticky": "ew"}

        if uninstall:
            tk.Label(root, text=f"卸载 {APP_NAME}",
                     font=("Segoe UI", 15, "bold")).grid(row=0, column=0,
                     columnspan=3, **pad)
            self.keep_data = tk.BooleanVar(value=False)
            tk.Checkbutton(root, text="保留用户数据(配置/缓存/数据库)",
                           variable=self.keep_data).grid(row=1, column=0,
                           columnspan=3, **pad)
            tk.Label(root, text=f"安装位置: {self.target}",
                     wraplength=500, justify="left").grid(row=2, column=0,
                     columnspan=3, **pad)
            self.go = tk.Button(root, text="卸载", bg="#8b3445", fg="white",
                                command=self.do_uninstall)
            self.go.grid(row=3, column=0, columnspan=3, **pad)
        else:
            tk.Label(root, text=f"安装 {APP_NAME}",
                     font=("Segoe UI", 15, "bold")).grid(row=0, column=0,
                     columnspan=3, **pad)
            tk.Label(root, text="安装目录:").grid(row=1, column=0, sticky="e", pady=6)
            self.dir_var = tk.StringVar(value=self.target)
            dir_entry = tk.Entry(root, textvariable=self.dir_var)
            dir_entry.grid(row=1, column=1, pady=6, sticky="ew")
            tk.Button(root, text="浏览…", command=self.browse)\
                .grid(row=1, column=2, padx=(0, 14), pady=6)

            self.desktop = tk.BooleanVar(value=True)
            self.taskbar = tk.BooleanVar(value=False)
            self.launch = tk.BooleanVar(value=True)
            tk.Checkbutton(root, text="创建桌面快捷方式", variable=self.desktop)\
                .grid(row=2, column=0, columnspan=3, sticky="w", padx=14)
            tk.Checkbutton(root, text="固定到任务栏(Windows 10; Win11 可能需手动固定)",
                           variable=self.taskbar)\
                .grid(row=3, column=0, columnspan=3, sticky="w", padx=14)
            tk.Checkbutton(root, text="安装完成后启动应用", variable=self.launch)\
                .grid(row=4, column=0, columnspan=3, sticky="w", padx=14)

            info = tk.Label(root, text="应用的所有数据(配置/数据库/缓存/密钥)都会保存在"
                            "安装目录的 data 文件夹里, 卸载时可选保留或删除。",
                            wraplength=500, justify="left", fg="#4d5d55")
            info.grid(row=5, column=0, columnspan=3, **pad)

            btn_frame = tk.Frame(root)
            btn_frame.grid(row=6, column=0, columnspan=3, **pad)
            self.go = tk.Button(btn_frame, text="安装", bg="#1f5a46", fg="white",
                                width=18, command=self.do_install)
            self.go.pack(side="left", padx=(0, 10))

        self.log = tk.Text(root, height=10, state="disabled", bg="#f5f2e9",
                           font=("Consolas", 9), wrap="word")
        self.log.grid(row=7, column=0, columnspan=3, padx=14, pady=6, sticky="nesw")

        btn_bar = tk.Frame(root)
        btn_bar.grid(row=8, column=0, columnspan=3, padx=14, pady=(0, 10), sticky="ew")
        tk.Button(btn_bar, text="退出", command=root.destroy).pack(side="right")

    def browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.dir_var.get() or "D:\\",
                                    title="选择安装目录")
        if d:
            self.dir_var.set(os.path.join(d, "HPHL") if os.path.basename(d).lower()
                             not in ("hphl",) else d)

    def logline(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.update_idletasks()

    # ------------------------------------------------------------ 安装
    def do_install(self) -> None:
        target = os.path.abspath(self.dir_var.get().strip())
        if not target.lower().endswith("\\hphl"):
            target = os.path.join(target, "HPHL")
        self.dir_var.set(target)
        self.go.configure(state="disabled")
        threading.Thread(target=self._install, args=(target,), daemon=True).start()

    def _install(self, target: str) -> None:
        try:
            self.logline(f"创建目录: {target}")
            os.makedirs(target, exist_ok=True)
            self.logline("复制应用主程序…")
            shutil.copyfile(BUNDLED_APP, os.path.join(target, APP_EXE))
            self.logline("写入便携数据标记(portable.flag)与 data 目录…")
            Path(target, "portable.flag").write_text("", encoding="utf-8")
            data = Path(target, "data")
            data.mkdir(exist_ok=True)
            self.logline("授予 data 目录写入权限(Users)…")
            subprocess.run(["icacls", str(data), "/grant", "*S-1-5-32-545:(OI)(CI)M"],
                           capture_output=True, timeout=60)
            self.logline("写入卸载器…")
            shutil.copyfile(sys.executable, os.path.join(target, UNINSTALL_EXE))
            self.logline("注册到系统「已安装应用」…")
            register_app(target)
            self.logline("创建快捷方式…")
            lnks = make_shortcuts(target, self.desktop.get(), True, self.taskbar.get())
            for l in lnks:
                self.logline(f"  快捷方式: {l}")
            self.logline("✅ 安装完成!")
            self.go.configure(state="normal", text="重新安装")
            if self.launch.get():
                subprocess.Popen([os.path.join(target, APP_EXE)], cwd=target)
        except Exception as exc:  # noqa: BLE001
            self.logline(f"❌ 安装失败: {exc}")
            self.logline(traceback.format_exc()[-800:])
            messagebox.showerror("安装失败", str(exc))
            self.go.configure(state="normal")

    # ------------------------------------------------------------ 卸载
    def do_uninstall(self) -> None:
        self.go.configure(state="disabled")
        threading.Thread(target=self._uninstall, daemon=True).start()

    def _uninstall(self) -> None:
        try:
            target = Path(sys.executable).resolve().parent
            self.logline("结束正在运行的应用…")
            subprocess.run(["taskkill", "/IM", APP_EXE, "/F"], capture_output=True)
            self.logline("移除快捷方式与注册表项…")
            unregister_app()
            desktop = _desktop_path()
            sm = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")
            for lnk in (os.path.join(desktop, LNK_NAME),
                        os.path.join(sm, LNK_NAME),
                        os.path.expandvars(
                            r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch"
                            r"\User Pinned\TaskBar" + "\\" + LNK_NAME)):
                remove_file(lnk)
            keep = self.keep_data.get()
            self.logline("删除程序文件…" + ("(保留 data 用户数据)" if keep else ""))
            def _rmtree():
                for child in target.iterdir():
                    if keep and child.name == "data":
                        continue
                    if child.name == UNINSTALL_EXE:
                        continue
                    shutil.rmtree(child, ignore_errors=True) if child.is_dir() else remove_file(str(child))
            _rmtree()
            self.logline("✅ 卸载完成! 本窗口可关闭。" + ("" if keep else "\n(数据目录将在本窗口关闭后自动删除)"))
            self.root.protocol("WM_DELETE_WINDOW", lambda: None)
            if not keep:
                def last():
                    time.sleep(1.2)
                    shutil.rmtree(target, ignore_errors=True)
                    os._exit(0)
                threading.Thread(target=last, daemon=True).start()
            else:
                threading.Thread(target=lambda: (time.sleep(0.8), os._exit(0)), daemon=True).start()
        except Exception as exc:  # noqa: BLE001
            self.logline(f"❌ 卸载失败: {exc}")
            self.go.configure(state="normal")


def main() -> None:
    # DPI 感知: 高 DPI 屏幕上 tkinter 元素不再模糊/截断
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass

    uninstall = "/uninstall" in sys.argv or \
        Path(sys.executable).stem.lower() == "uninstall"
    root = tk.Tk()
    try:
        InstallerUI(root, uninstall)
        root.mainloop()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        messagebox.showerror("安装程序错误", traceback.format_exc()[-1000:])


if __name__ == "__main__":
    main()
