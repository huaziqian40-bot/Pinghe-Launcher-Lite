# -*- coding: utf-8 -*-
"""构建 Lite 的 NSIS assisted 安装包：python scripts/build_windows.py [--dry-run]。

先运行现有 PyInstaller 单文件构建，得到 dist/PingheLauncherLite.exe；本脚本只封装该 exe，
不运行旧 tkinter 安装程序，也不自动卸载旧 MSI 或执行真正的用户安装/卸载。
"""
from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

# PowerShell 5.1/CI 的默认代码页可能是 GBK；发布脚本固定 UTF-8，避免测试或日志把
# 成功构建误判成 UnicodeDecodeError。
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "installer" / "PingheLauncherLite.nsi"
FROZEN_EXE = ROOT / "dist" / "PingheLauncherLite.exe"
DIST = ROOT / "dist"
SEMVER = re.compile(r"\d+\.\d+\.\d+\Z")


def literal_version(path: Path, name: str) -> str:
    """只读 AST 常量，不 import 旧 installer.py（它会依赖 tkinter/winreg）。"""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    values = [node.value.value for node in tree.body
              if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == name
                      for target in node.targets)
              and isinstance(node.value, ast.Constant)
              and isinstance(node.value.value, str)]
    if len(values) != 1 or not SEMVER.fullmatch(values[0]):
        raise ValueError(f"{path}: 期望唯一的 {name} = '主.次.补丁' 字符串")
    return values[0]


def checked_version(root: Path = ROOT) -> str:
    installer = literal_version(root / "installer" / "installer.py", "VERSION")
    updater = literal_version(root / "hellopinghe" / "updater.py", "APP_VERSION")
    if installer != updater:
        raise ValueError(f"双版本号不一致：installer.py={installer}, updater.py={updater}；停止发布")
    return installer


def find_makensis() -> Path:
    """优先显式指定；再查 PATH、electron-builder 的版本缓存、标准 NSIS 目录。"""
    explicit = os.environ.get("MAKENSIS_EXE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(f"MAKENSIS_EXE 指向不存在的文件：{path}")
    found = shutil.which("makensis") or shutil.which("makensis.exe")
    if found:
        return Path(found).resolve()
    cache = Path(os.environ.get("LOCALAPPDATA", "")) / "electron-builder" / "Cache"
    # electron-builder 缓存的目录名包含哈希尾缀，不能硬编码某次解压的随机目录。
    if cache.is_dir():
        for version_dir in sorted(cache.glob("nsis-*"), reverse=True):
            for path in sorted(version_dir.glob("*/makensis.exe")):
                if path.is_file():
                    return path.resolve()
            for path in sorted(version_dir.glob("*/Bin/makensis.exe")):
                if path.is_file():
                    return path.resolve()
    for location in (Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
                     Path(r"C:\Program Files\NSIS\makensis.exe")):
        if location.is_file():
            return location
    raise FileNotFoundError("未找到 makensis.exe；请设置 MAKENSIS_EXE 或安装 NSIS")


def output_path(version: str, dist: Path = DIST) -> Path:
    """历史官网 1.2.7 只产候选包；既有正式包不论版本均绝不覆盖。"""
    official = dist / f"PingheLauncherLite-{version}-Setup.exe"
    if version != "1.2.7" and not official.exists():
        return official
    candidate = dist / f"PingheLauncherLite-{version}-Setup-candidate.exe"
    index = 2
    while candidate.exists():
        candidate = dist / f"PingheLauncherLite-{version}-Setup-candidate-{index}.exe"
        index += 1
    return candidate


def build_command(makensis: Path, version: str, source: Path, destination: Path,
                  script: Path = SCRIPT) -> list[str]:
    # /D 参数须先于脚本，Python subprocess 负责正确转义带空格的 Windows 路径。
    return [str(makensis), "/V2", f"/DVERSION={version}",
            f"/DAPP_EXE_SOURCE={source}", f"/DOUTPUT_FILE={destination}", str(script)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证版本并构建 Lite 单用户 NSIS 安装包")
    parser.add_argument("--dry-run", action="store_true", help="只检查输入并打印构建命令，不创建文件")
    args = parser.parse_args(argv)
    try:
        version = checked_version()
        if not SCRIPT.is_file():
            raise FileNotFoundError(f"NSIS 脚本不存在：{SCRIPT}")
        if not FROZEN_EXE.is_file():
            raise FileNotFoundError(f"先用 PyInstaller 的 PingheLauncherLite.spec 构建单文件：{FROZEN_EXE}")
        compiler = find_makensis()
        destination = output_path(version)
        command = build_command(compiler, version, FROZEN_EXE, destination)
        print(f"版本：{version}\n单文件源：{FROZEN_EXE}\nNSIS：{compiler}\n输出：{destination}")
        print("命令：", subprocess.list2cmdline(command) if os.name == "nt" else repr(command))
        if args.dry_run:
            return 0
        # 禁止使用自动覆盖已存在的包，避免误覆盖官网或上一轮候选文件。
        if destination.exists():
            raise FileExistsError(destination)
        DIST.mkdir(parents=True, exist_ok=True)
        subprocess.run(command, cwd=ROOT, check=True)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("makensis 报告完成但未生成有效安装包")
        print(f"构建完成：{destination} ({destination.stat().st_size} 字节)")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"构建失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
