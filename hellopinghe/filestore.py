"""data/ 统一文件存储: 原子写 + 线程锁 + 旧数据搬迁.

用户数据的完整布局(格式规范见仓库根 ``DATA-FORMAT.md``)::

    data/
    ├── settings.yaml      配置 + 四平台凭据 + 选课(人可读可改)
    ├── Schedule           日程(可与 PH-Launcher 共用)
    ├── agent/             AI 会话记录(可与 PH-Launcher 共用)
    ├── phll/              HPHL 独享(缓存/偏好/镜像/会话, 全部关在这个文件夹里)
    │   ├── state.json         偏好与小状态(DDL 移除列表等)
    │   ├── edupage/           课表缓存
    │   ├── managebac/         作业缓存 + 登录会话
    │   ├── mail/              通讯录收割与自建联系人
    │   └── xinlv/             心履本地镜像与同步游标
    └── logs/              诊断日志(非用户数据, 可随时删)

约定:
- 写入一律"临时文件 + ``os.replace``"原子替换 —— 崩溃/断电不会留半个文件;
- 每个文件一把进程内锁(js_api 多线程并发安全); ``update_json`` 是
  "读-改-写整体加锁"的入口, 不要自己 load 完再 save;
- 读取失败(缺失/损坏)一律返回默认值, 绝不因数据文件问题阻塞启动;
- 根目录每次操作都重新向 ``paths.data_dir()`` 取(便携/测试环境切换立即生效)。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import paths

#: 顶层条目名
SETTINGS = "settings.yaml"
SCHEDULE = "Schedule"
PHLL_DIR = "phll"
AGENT_DIR = "agent"
LOG_DIR = "logs"
BACKUP_DIR = "_migrated_backup"
AUTO_BACKUP_DIR = "_backups"

#: phll/ 内部子目录
EDUPAGE_SUB = "edupage"
MANAGEBAC_SUB = "managebac"
MAIL_SUB = "mail"
XINLV_SUB = "xinlv"

_LOCK_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


# ---------------------------------------------------------------- 路径
def root() -> Path:
    """数据根目录(便携安装/测试环境切换后自动跟随)."""
    return paths.data_dir()


def ensure_root() -> Path:
    r = root()
    r.mkdir(parents=True, exist_ok=True)
    return r


def phll(*parts: str) -> Path:
    """``data/phll/<parts...>`` 的路径(不自动建目录)."""
    p = root() / PHLL_DIR
    for part in parts:
        p = p / part
    return p


def agent_dir() -> Path:
    return root() / AGENT_DIR


def log_dir() -> Path:
    return root() / LOG_DIR


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 锁
def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCK_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _LOCKS[key] = lk
        return lk


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- JSON
def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  (缺失/损坏都退回默认值)
        return default


def save_json(path: Path, data) -> None:
    path = Path(path)
    with _lock_for(path):
        _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def update_json(path: Path, mutate, default=None):
    """读-改-写(整体加锁). ``mutate(doc)`` 直接改 doc; 返回非 None 则替换文档."""
    path = Path(path)
    with _lock_for(path):
        doc = load_json(path, None)
        if doc is None:
            doc = default if default is not None else {}
        out = mutate(doc)
        if out is None:
            out = doc
        _atomic_write(path, json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        return out


# ---------------------------------------------------------------- YAML
def load_yaml(path: Path, default=None):
    import yaml

    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return default
    try:
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        return default
    return default if data is None else data


def save_yaml(path: Path, data) -> None:
    """保存 YAML(含凭据的文件顺带收紧权限)."""
    import yaml

    path = Path(path)
    with _lock_for(path):
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                              default_flow_style=False)
        _atomic_write(path, text)
        try:
            os.chmod(path, 0o600)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- settings.yaml 文档
def settings_path() -> Path:
    return root() / SETTINGS


def load_settings() -> dict:
    return load_yaml(settings_path(), {}) or {}


def update_settings(mutate) -> dict:
    """settings.yaml 的读-改-写(加锁); ``mutate(doc)`` 直接改并返回/返回 None."""
    path = settings_path()
    with _lock_for(path):
        doc = load_yaml(path, {}) or {}
        out = mutate(doc)
        if out is None:
            out = doc
        import yaml

        text = yaml.safe_dump(out, allow_unicode=True, sort_keys=False,
                              default_flow_style=False)
        _atomic_write(path, text)
        try:
            os.chmod(path, 0o600)
        except Exception:  # noqa: BLE001
            pass
        return out


# ---------------------------------------------------------------- 旧数据搬迁
def move_legacy(names: Iterable[str]) -> list[str]:
    """把已迁移完的旧文件挪进 ``_migrated_backup/``(保留, 不删除)."""
    r = root()
    backup = r / BACKUP_DIR
    moved: list[str] = []
    for name in names:
        src = r / name
        if not src.exists():
            continue
        try:
            backup.mkdir(parents=True, exist_ok=True)
            dest = backup / name
            if dest.exists():
                dest = backup / f"{name}.{int(datetime.now().timestamp())}"
            os.replace(src, dest)
            moved.append(name)
        except OSError:
            continue
    return moved


#: 旧版本散在 data/ 根下的文件名通配(迁移后统一挪进 _migrated_backup/)
LEGACY_GLOBS = (
    "config.json", "secrets.json", ".secret_key", ".keyring_migrated",
    "hellopinghe.db", "hellopinghe.db-wal", "hellopinghe.db-shm",
    "edupage_week_*.json", "edupage_personal_*.json",
    "session_*.json", "mail_contacts.json", "contacts_custom.json",
)


def legacy_files() -> list[Path]:
    r = root()
    out: list[Path] = []
    for pat in LEGACY_GLOBS:
        out.extend(sorted(p for p in r.glob(pat) if p.is_file()))
    return out


def move_all_legacy() -> list[str]:
    """把旧布局的散落文件(含 agent_sessions/ 目录)整体挪进备份目录."""
    r = root()
    names = [p.name for p in legacy_files()]
    if (r / "agent_sessions").is_dir():
        names.append("agent_sessions")
    return move_legacy(names)


# ---------------------------------------------------------------- 自动备份
def auto_backup(interval_hours: float = 20.0, keep: int = 14) -> Path | None:
    """把"小而珍贵、丢了就没了"的数据打包进 ``data/_backups/``.

    只备份不可再生的部分: ``settings.yaml`` / ``Schedule`` / ``agent/`` /
    ``phll/state.json`` / ``phll/xinlv/`` / ``phll/mail/`` —— 课表与作业缓存
    能重新抓取, 不进备份(否则每次要压几百 MB)。

    默认每 ~20 小时做一次, 保留最近 14 份; 可在 settings.yaml 里调::

        backup:
          enabled: true
          interval_hours: 20
          keep: 14

    返回本次生成的压缩包路径; 不需要备份时返回 None。**绝不删除任何用户数据**,
    清理只发生在 ``_backups/`` 内部。
    """
    import time
    import zipfile

    try:
        root_ = ensure_root()
        doc = load_settings() or {}
        conf = doc.get("backup") or {}
        if conf.get("enabled") is False:
            return None
        keep = int(conf.get("keep") or keep)
        interval = float(conf.get("interval_hours") or interval_hours)
        bdir = root_ / AUTO_BACKUP_DIR
        bdir.mkdir(parents=True, exist_ok=True)
        existing = sorted(bdir.glob("data-*.zip"))
        if existing:
            age_h = (time.time() - existing[-1].stat().st_mtime) / 3600
            if age_h < interval:
                return None

        targets: list[Path] = []
        for name in (SETTINGS, SCHEDULE):
            p = root_ / name
            if p.is_file():
                targets.append(p)
        for sub in (("phll", "state.json"), ("phll", "xinlv"), ("phll", "mail"), (AGENT_DIR,)):
            p = root_.joinpath(*sub)
            if p.is_file():
                targets.append(p)
            elif p.is_dir():
                targets.extend(x for x in p.rglob("*") if x.is_file())
        if not targets:
            return None

        out = bdir / f"data-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in targets:
                try:
                    z.write(p, p.relative_to(root_).as_posix())
                except OSError:
                    continue
        for old in sorted(bdir.glob("data-*.zip"))[:-max(1, keep)]:
            try:
                old.unlink()
            except OSError:
                pass
        return out
    except Exception:  # noqa: BLE001  (备份失败绝不阻塞启动)
        return None
