"""数据目录解析中枢.

- 便携模式(安装器在应用目录写入 portable.flag): 所有数据存安装目录下的 data 子目录
- 普通运行(源码/未便携): 数据存 ~/.hellopinghe(与历史版本一致)

所有需要数据路径的模块一律 import 本模块, 禁止再硬编码 Path.home()。
"""
from __future__ import annotations

import sys
from pathlib import Path

FLAG_NAME = "portable.flag"
FRESH_FLAG = "fresh.flag"        # 测试环境: 存在则禁止旧数据迁移(数据彻底隔离)
LEGACY_HOME_DIR = Path.home() / ".hellopinghe"      # 历史数据目录(迁移源)
LEGACY_SCHOOLHUB_DIR = Path.home() / ".schoolhub"   # 更旧的 SchoolHub 目录


def exe_dir() -> Path | None:
    """冻结(exe)运行时的应用所在目录; 源码运行返回 None."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def is_portable() -> bool:
    d = exe_dir()
    return bool(d and (d / FLAG_NAME).exists())


def is_fresh() -> bool:
    """测试/演示环境标记: 有 fresh.flag 时跳过旧数据迁移.

    否则便携模式首次启动会把 ~/.hellopinghe 的真实个人数据整个搬进
    测试目录 —— 测试环境要求与真实数据完全隔离, 见 _migrate_legacy().
    """
    d = exe_dir()
    return bool(d and (d / FRESH_FLAG).exists())


def data_dir() -> Path:
    """当前生效的数据目录(导入时即确定; portable.flag 由安装器预先写入)."""
    d = exe_dir()
    if d and (d / FLAG_NAME).exists():
        return d / "data"
    return LEGACY_HOME_DIR


def legacy_candidates() -> list[Path]:
    """迁移源候选: 存在且 ≠ 当前数据目录的旧数据目录."""
    out = []
    cur = data_dir()
    for d in (LEGACY_HOME_DIR, LEGACY_SCHOOLHUB_DIR):
        if d != cur and d not in out:
            out.append(d)
    return out
