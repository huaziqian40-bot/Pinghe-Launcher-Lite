# -*- coding: utf-8 -*-
"""Pinghe 系列安装登记（PHL 与 PHL Lite 共用的一份固定文件）。

两个程序可能被用户装到任意目录，但「另一个程序装在哪」只有一个权威答案：
一个固定位置、两个程序都会读写的 INI。安装器据此把默认安装目录设为对方的目录；
应用启动时更新自己的条目（版本/路径/数据根），用户手动移动目录后也能自愈。

位置（每用户、无需管理员）::

    Windows  %LOCALAPPDATA%\\PingheSeries\\installations.ini
    macOS    ~/Library/Application Support/PingheSeries/installations.ini

格式（NSIS 的 ReadINIStr/WriteINIStr 能直接读，Python/Node 也容易解析）::

    [phl]
    installed=1
    path=C:\\Users\\me\\Programs\\PH Launcher
    data_root=C:\\Users\\me\\Programs\\PH Launcher\\data
    version=2.0.13
    updated_at=2026-11-10T18:20:00+08:00

    [phl-lite]
    installed=1
    path=C:\\Users\\me\\Programs\\PH Launcher
    data_root=C:\\Users\\me\\Programs\\PH Launcher\\data
    version=1.2.9
    updated_at=...

约定：
- 节存在且 ``installed`` 为 1/true/yes（或省略）表示「已安装」；卸载时删除整节。
- ``path`` 是安装目录（exe/.app 所在目录），``data_root`` 是该程序实际使用的数据根。
- 文件损坏/不可读时读取返回空，绝不抛错；写入永远保留其他程序的节。
- 测试用 ``PHLL_INSTALL_REGISTRY`` 指向临时文件，绝不碰真实用户登记。
"""
from __future__ import annotations

import configparser
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

#: 本程序在登记文件里的节名；对方固定是 PHL。
APP_NAME = "phl-lite"
OTHER_NAME = "phl"
DISPLAY_NAMES = {"phl": "PH Launcher", "phl-lite": "Pinghe Launcher Lite"}

#: 测试/自动化用：覆盖登记文件路径（绝不影响真实安装登记）。
REGISTRY_ENV = "PHLL_INSTALL_REGISTRY"
#: 源码运行默认不写真实登记；置为 1 可强制记录（调试用）。
RECORD_ENV = "PHLL_RECORD_INSTALL"

_ABSENT = object()


def registry_path() -> Path:
    override = (os.environ.get(REGISTRY_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PingheSeries" / "installations.ini"
    if os.name == "nt":
        base = (os.environ.get("LOCALAPPDATA") or "").strip() or str(Path.home() / "AppData" / "Local")
        return Path(base) / "PingheSeries" / "installations.ini"
    base = (os.environ.get("XDG_CONFIG_HOME") or "").strip() or str(Path.home() / ".config")
    return Path(base) / "PingheSeries" / "installations.ini"


def _parser() -> configparser.RawConfigParser:
    # interpolation=None：路径里的 % 不能被当成插值；strict=False 容忍重复键。
    return configparser.RawConfigParser(interpolation=None, strict=False)


def _read_parser() -> configparser.RawConfigParser:
    parser = _parser()
    try:
        parser.read(registry_path(), encoding="utf-8")
    except (OSError, configparser.Error, UnicodeDecodeError):
        return _parser()
    return parser


def read() -> dict[str, dict[str, str]]:
    """整份登记；文件缺失/损坏时返回空 dict（读路径绝不抛错）。"""
    parser = _read_parser()
    return {section: {key: (value or "").strip() for key, value in parser.items(section)}
            for section in parser.sections()}


def _installed(section: dict[str, str]) -> bool:
    flag = (section.get("installed") or "").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _absolute(value: str) -> str:
    """规范化成绝对路径；相对路径/空/非法返回空串（登记只接受绝对路径）。"""
    text = (value or "").strip()
    if not text:
        return ""
    try:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            return ""
        return str(candidate)
    except (OSError, ValueError):
        return ""


def installation(name: str) -> dict | None:
    """某个程序的安装信息；未安装/路径非法时返回 None。"""
    section = read().get(name)
    if not section or not _installed(section):
        return None
    path = _absolute(section.get("path", ""))
    if not path:
        return None
    info = {
        "name": name,
        "display_name": DISPLAY_NAMES.get(name, name),
        "path": path,
        "data_root": _absolute(section.get("data_root", "")) or str(Path(path) / "data"),
        "version": section.get("version", ""),
        "updated_at": section.get("updated_at", ""),
    }
    return info


def self_installation() -> dict | None:
    return installation(APP_NAME)


def other_installation() -> dict | None:
    """对方程序（PHL）的安装信息，没有则 None。"""
    return installation(OTHER_NAME)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_atomic(parser: configparser.RawConfigParser) -> Path:
    target = registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".installations-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            parser.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return target


def record(name: str, path: str, *, data_root: str = "", version: str = "") -> Path:
    """写入/更新一个程序的条目（保留另一个程序的节）。返回登记文件路径。"""
    if name not in DISPLAY_NAMES:
        raise ValueError(f"未知的程序名: {name}")
    install_path = _absolute(path)
    if not install_path:
        raise ValueError("安装目录不能为空")
    root = _absolute(data_root) or str(Path(install_path) / "data")
    parser = _read_parser()
    if not parser.has_section(name):
        parser.add_section(name)
    parser.set(name, "installed", "1")
    parser.set(name, "path", install_path)
    parser.set(name, "data_root", root)
    if version:
        parser.set(name, "version", str(version))
    parser.set(name, "updated_at", _now())
    return _write_atomic(parser)


def remove(name: str) -> bool:
    """删除某个程序的条目；返回是否真的删了。"""
    parser = _read_parser()
    if not parser.has_section(name):
        return False
    parser.remove_section(name)
    _write_atomic(parser)
    return True


def record_self(*, version: str = "", data_root: str | Path | None = None,
                install_dir: str | Path | None = None) -> Path | None:
    """记录本程序（安装/升级/首次启动）。源码运行默认不写真实登记。"""
    if not getattr(sys, "frozen", False) and os.environ.get(RECORD_ENV) != "1":
        return None
    if install_dir is not None:
        path = Path(install_dir)
    elif getattr(sys, "frozen", False):
        path = Path(sys.executable).resolve().parent
    else:
        path = Path(__file__).resolve().parent.parent
    if data_root is None:
        from . import paths

        data_root = paths.data_dir()
    return record(APP_NAME, str(path), data_root=str(data_root), version=version)


def update_self_data_root(data_root: str | Path) -> Path | None:
    """数据根变化时只更新 data_root（路径/版本保持）。"""
    info = self_installation()
    if not info:
        return None
    return record(APP_NAME, info["path"], data_root=str(data_root), version=info["version"])
