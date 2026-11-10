"""数据目录解析中枢.

- 环境变量 PHLL_DATA_DIR 有值: 用它(自动化测试/互通测试/高级用户改向; 与 PH Launcher
  的 PHL_DATA_DIR 对称, 正常使用时不设置就没有任何影响)
- 便携模式(安装器在应用目录写入 portable.flag): 所有数据存安装目录下的 data 子目录
- 普通运行(源码/未便携): 数据存 ~/.hellopinghe(与历史版本一致)

所有需要数据路径的模块一律 import 本模块, 禁止再硬编码 Path.home()。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FLAG_NAME = "portable.flag"
FRESH_FLAG = "fresh.flag"        # 测试环境: 存在则禁止旧数据迁移(数据彻底隔离)
LEGACY_HOME_DIR = Path.home() / ".hellopinghe"      # 历史数据目录(迁移源)
LEGACY_SCHOOLHUB_DIR = Path.home() / ".schoolhub"   # 更旧的 SchoolHub 目录
DATA_DIR_ENV = "PHLL_DATA_DIR"   # 显式指定数据目录(测试用; 两个软件的测试脚本靠它隔离)


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
    override = (os.environ.get(DATA_DIR_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    d = exe_dir()
    if d and (d / FLAG_NAME).exists():
        return d / "data"
    return LEGACY_HOME_DIR


def phl_shared_data_present(root: Path) -> bool:
    """已存在 PHL 的用户数据时，不把旧 Lite 目录偷偷并进这份共用数据。

    默认两个安装器都指向 ``PH Launcher/data``，但升级老 MSI 的机器可能同时有
    ``~/.hellopinghe`` 或旧 Lite 安装目录。把两套账号/日程按"缺失即复制"拼在一起
    会把过期的课表、令牌或会话混进 PHL 正在使用的目录；旧文件应原地保留，
    由用户在确认备份后决定是否迁移，安装器不会自动合并。
    """
    root = Path(root)
    if not ((root.parent / "PH Launcher.exe").is_file() or (root / "phl").is_dir()):
        return False
    if any((root / item).is_file() for item in
           ("settings.yaml", "Schedule", "Timetable", "School")):
        return True
    if (root / "phl" / "launcher.json").is_file() or (root / "phl" / "profile").exists():
        return True
    agent = root / "agent"
    return agent.is_dir() and any(agent.glob("*.json"))


def legacy_candidates() -> list[Path]:
    """迁移源候选: 存在且 ≠ 当前数据目录的旧数据目录."""
    out = []
    cur = data_dir()
    for d in (LEGACY_HOME_DIR, LEGACY_SCHOOLHUB_DIR):
        if d != cur and d not in out:
            out.append(d)
    return out
