"""本地 SQLite 缓存(所有数据只落本机)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .managebac.parse import Deadline

from . import paths as _paths

DB_DIR = _paths.data_dir()
DB_PATH = DB_DIR / "hellopinghe.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (
    host      TEXT NOT NULL,
    class_id  TEXT NOT NULL,
    name      TEXT NOT NULL,
    updated   TEXT NOT NULL,
    PRIMARY KEY (host, class_id)
);
CREATE TABLE IF NOT EXISTS deadlines (
    host      TEXT NOT NULL,
    title     TEXT NOT NULL,
    course    TEXT,
    due_at    TEXT,
    status    TEXT,
    category  TEXT,
    updated   TEXT NOT NULL,
    PRIMARY KEY (host, title, course, due_at)
);
CREATE TABLE IF NOT EXISTS grade_snapshots (
    host      TEXT NOT NULL,
    course    TEXT NOT NULL,
    grade     TEXT,
    taken_at  TEXT NOT NULL,
    PRIMARY KEY (host, course, taken_at)
);
CREATE TABLE IF NOT EXISTS tasks_cache (
    host      TEXT NOT NULL,
    task_id   TEXT NOT NULL,
    class_id  TEXT NOT NULL,
    class_name TEXT,
    title     TEXT,
    due_at    TEXT,
    status    TEXT,
    past_due  INTEGER,
    can_submit INTEGER,
    updated   TEXT NOT NULL,
    PRIMARY KEY (host, task_id)
);
CREATE TABLE IF NOT EXISTS tasks_meta (
    host      TEXT PRIMARY KEY,
    updated   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    day       TEXT NOT NULL,
    time      TEXT DEFAULT '',
    title     TEXT NOT NULL,
    note      TEXT DEFAULT '',
    created   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ddl_dismissed (
    host      TEXT NOT NULL,
    dkey      TEXT NOT NULL,
    created   TEXT NOT NULL,
    PRIMARY KEY (host, dkey)
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: pywebview 的 js_api 每次调用可能在不同线程;
    # 服务层每次操作都取新连接, 这里再加保险
    conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
    conn.executescript(_SCHEMA)
    return conn


def save_classes(conn: sqlite3.Connection, host: str, classes: dict[str, str]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR REPLACE INTO classes(host, class_id, name, updated) VALUES (?,?,?,?)",
        [(host, cid, name, now) for cid, name in classes.items()],
    )
    conn.commit()


def save_deadlines(conn: sqlite3.Connection, host: str, items: Iterable[Deadline]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    rows = [
        (
            host, it.title, it.course,
            it.due_at.isoformat(timespec="seconds") if it.due_at else None,
            it.status, it.category, now,
        )
        for it in items
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO deadlines"
        "(host, title, course, due_at, status, category, updated) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def upcoming_deadlines(conn: sqlite3.Connection, host: str, days: int = 14) -> list[tuple]:
    cutoff = (datetime.now() + __import__("datetime").timedelta(days=days)).isoformat(
        timespec="seconds"
    )
    return conn.execute(
        "SELECT title, course, due_at, status, category FROM deadlines "
        "WHERE host=? AND due_at IS NOT NULL AND due_at<=? ORDER BY due_at",
        (host, cutoff),
    ).fetchall()


# ---------------------------------------------------------------- 任务缓存
def save_tasks_cache(conn: sqlite3.Connection, host: str, tasks: list) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO tasks_meta(host, updated) VALUES (?,?)", (host, now)
    )
    conn.execute("DELETE FROM tasks_cache WHERE host=?", (host,))
    conn.executemany(
        "INSERT OR REPLACE INTO tasks_cache"
        "(host, task_id, class_id, class_name, title, due_at, status, past_due,"
        " can_submit, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                host, t.task_id, t.class_id, t.class_name, t.title,
                t.due_at.isoformat(timespec="seconds") if t.due_at else None,
                t.status, int(t.past_due), int(t.can_submit), now,
            )
            for t in tasks
        ],
    )
    conn.commit()


def tasks_cache_age(conn: sqlite3.Connection, host: str):
    row = conn.execute(
        "SELECT updated FROM tasks_meta WHERE host=?", (host,)
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


def load_tasks_cache(conn: sqlite3.Connection, host: str) -> list[dict]:
    rows = conn.execute(
        "SELECT task_id, class_id, class_name, title, due_at, status, past_due,"
        " can_submit FROM tasks_cache WHERE host=? ORDER BY due_at",
        (host,),
    ).fetchall()
    return [
        {
            "task_id": r[0], "class_id": r[1], "class_name": r[2], "title": r[3],
            "due_at": r[4], "status": r[5], "past_due": bool(r[6]), "can_submit": bool(r[7]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------- 日程事件
def events_add(conn: sqlite3.Connection, day: str, time_: str, title: str, note: str) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO events(day, time, title, note, created) VALUES (?,?,?,?,?)",
        (day, time_, title, note, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def events_list(conn: sqlite3.Connection, day_from: str, day_to: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, day, time, title, note FROM events "
        "WHERE day>=? AND day<=? ORDER BY day, time, id",
        (day_from, day_to),
    ).fetchall()
    return [
        {"id": r[0], "day": r[1], "time": r[2], "title": r[3], "note": r[4]}
        for r in rows
    ]


def events_update(conn: sqlite3.Connection, event_id: int, day: str, time_: str,
                  title: str, note: str) -> None:
    conn.execute(
        "UPDATE events SET day=?, time=?, title=?, note=? WHERE id=?",
        (day, time_, title, note, event_id),
    )
    conn.commit()


def events_delete(conn: sqlite3.Connection, event_id: int) -> None:
    conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()


# ---------------------------------------------------------------- DDL 左滑删除
def ddl_dismiss(conn: sqlite3.Connection, host: str, dkey: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ddl_dismissed(host, dkey, created) VALUES (?,?,?)",
        (host, dkey, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def ddl_dismissed_keys(conn: sqlite3.Connection, host: str) -> set[str]:
    return {
        r[0] for r in conn.execute(
            "SELECT dkey FROM ddl_dismissed WHERE host=?", (host,)
        ).fetchall()
    }


def ddl_dismissed_rows(conn: sqlite3.Connection, host: str) -> list[dict]:
    """已移除的作业列表(设置页恢复用)."""
    return [
        {"key": r[0], "created": r[1]}
        for r in conn.execute(
            "SELECT dkey, created FROM ddl_dismissed WHERE host=? ORDER BY created DESC",
            (host,),
        ).fetchall()
    ]


def ddl_restore(conn: sqlite3.Connection, host: str, dkey: str) -> None:
    """恢复误移除的作业(从 dismissed 表里删掉标记)."""
    conn.execute(
        "DELETE FROM ddl_dismissed WHERE host=? AND dkey=?", (host, dkey)
    )
    conn.commit()
