"""本地数据存储(全部落在 ``data/`` 下, 格式规范见仓库根 ``DATA-FORMAT.md``).

分工:
- **日程** → ``data/Schedule``(纯 JSON, 可与 PH-Launcher 共用)
- **HPHL 独享状态与缓存** → ``data/phll/``(DDL 移除列表、作业缓存、
  通讯录、心屐镜像、课表缓存、ManageBac 会话)

对外函数签名与旧的 SQLite 版完全一致(第一个参数 ``conn`` 仅为兼容保留,
现在不再使用) —— 上层服务/桥接层无需改动。首次启动会把旧的
``hellopinghe.db`` 里的数据搬进新文件, 并把旧的散落文件挪进
``data/_migrated_backup/``(保留不删)。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from . import filestore as fs
from .managebac.parse import Deadline

#: 兼容旧调用(旧版是 SQLite 连接工厂); 现在所有数据都在文件里
_DB_DIR_NAME = "hellopinghe.db"


def connect(db_path: Path | None = None):  # noqa: ARG001  (签名兼容)
    """兼容入口: 新版不再用数据库, 返回一个占位句柄."""
    return _HANDLE


class _Handle:
    """旧代码里 conn 只是被传递, 新版忽略它."""

    def __repr__(self) -> str:  # pragma: no cover
        return "<filestore handle>"


_HANDLE = _Handle()


# ================================================================ 通用文档
def _schedule_path() -> Path:
    return fs.root() / fs.SCHEDULE


def _state_path() -> Path:
    return fs.phll("state.json")


def _mb_path(name: str) -> Path:
    return fs.phll(fs.MANAGEBAC_SUB, name)


def _mail_path(name: str) -> Path:
    return fs.phll(fs.MAIL_SUB, name)


def _xinlv_path(name: str) -> Path:
    return fs.phll(fs.XINLV_SUB, name)


def _new_doc(kind: str, **extra) -> dict:
    doc = {"version": 1, "kind": kind, "app": "Pinghe Launcher Lite",
           "updated_at": fs.now_iso()}
    doc.update(extra)
    return doc


# ================================================================ 日程(Schedule)
def events_add(conn, day: str, time_: str, title: str, note: str) -> int:
    new_id = {"v": 1}

    def mutate(doc):
        if "events" not in doc:
            doc.update(_new_doc("pinghe-schedule", events=[]))
        ids = [int(e.get("id") or 0) for e in doc["events"]]
        new_id["v"] = (max(ids) + 1) if ids else 1
        doc["events"].append({
            "id": new_id["v"], "day": day, "time": time_ or "",
            "title": title, "note": note or "", "created": fs.now_iso(),
        })
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_schedule_path(), mutate, default=_new_doc("pinghe-schedule", events=[]))
    return new_id["v"]


def events_list(conn, day_from: str, day_to: str) -> list[dict]:
    doc = fs.load_json(_schedule_path(), {}) or {}
    out = [
        {"id": int(e.get("id") or 0), "day": e.get("day", ""),
         "time": e.get("time", ""), "title": e.get("title", ""),
         "note": e.get("note", "")}
        for e in (doc.get("events") or [])
        if day_from <= (e.get("day") or "") <= day_to
    ]
    out.sort(key=lambda e: (e["day"], e["time"], e["id"]))
    return out


def events_update(conn, event_id: int, day: str, time_: str, title: str, note: str) -> None:
    def mutate(doc):
        for e in doc.get("events") or []:
            if int(e.get("id") or 0) == int(event_id):
                e.update({"day": day, "time": time_ or "", "title": title,
                          "note": note or ""})
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_schedule_path(), mutate)


def events_delete(conn, event_id: int) -> None:
    def mutate(doc):
        doc["events"] = [e for e in (doc.get("events") or [])
                         if int(e.get("id") or 0) != int(event_id)]
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_schedule_path(), mutate)


# ================================================================ DDL 左滑移除
def ddl_dismiss(conn, host: str, dkey: str) -> None:
    def mutate(doc):
        bucket = doc.setdefault("ddl_dismissed", {}).setdefault(host, [])
        if not any(it.get("key") == dkey for it in bucket):
            bucket.append({"key": dkey, "created": fs.now_iso()})
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_state_path(), mutate, default=_new_doc("phll-state"))


def ddl_dismissed_keys(conn, host: str) -> set[str]:
    doc = fs.load_json(_state_path(), {}) or {}
    return {it.get("key") for it in (doc.get("ddl_dismissed", {}).get(host) or [])
            if it.get("key")}


def ddl_dismissed_rows(conn, host: str) -> list[dict]:
    doc = fs.load_json(_state_path(), {}) or {}
    rows = [{"key": it.get("key"), "created": it.get("created", "")}
            for it in (doc.get("ddl_dismissed", {}).get(host) or []) if it.get("key")]
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows


def ddl_restore(conn, host: str, dkey: str) -> None:
    def mutate(doc):
        bucket = doc.get("ddl_dismissed", {}).get(host) or []
        doc.setdefault("ddl_dismissed", {})[host] = [
            it for it in bucket if it.get("key") != dkey]
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_state_path(), mutate)


# ================================================================ 作业缓存
def save_tasks_cache(conn, host: str, tasks: list) -> None:
    items = [{
        "task_id": t.task_id, "class_id": t.class_id, "class_name": t.class_name,
        "title": t.title,
        "due_at": t.due_at.isoformat(timespec="seconds") if t.due_at else None,
        "status": t.status, "past_due": bool(t.past_due),
        "can_submit": bool(t.can_submit),
    } for t in tasks]

    def mutate(doc):
        if "hosts" not in doc:
            doc.update(_new_doc("phll-managebac-tasks", hosts={}))
        doc["hosts"][host] = {"updated": fs.now_iso(), "items": items}
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_mb_path("tasks.json"), mutate,
                   default=_new_doc("phll-managebac-tasks", hosts={}))


def tasks_cache_age(conn, host: str):
    doc = fs.load_json(_mb_path("tasks.json"), {}) or {}
    updated = ((doc.get("hosts") or {}).get(host) or {}).get("updated")
    if not updated:
        return None
    try:
        return datetime.fromisoformat(updated)
    except ValueError:
        return None


def load_tasks_cache(conn, host: str) -> list[dict]:
    doc = fs.load_json(_mb_path("tasks.json"), {}) or {}
    items = ((doc.get("hosts") or {}).get(host) or {}).get("items") or []
    out = [{
        "task_id": it.get("task_id", ""), "class_id": it.get("class_id", ""),
        "class_name": it.get("class_name", ""), "title": it.get("title", ""),
        "due_at": it.get("due_at"), "status": it.get("status"),
        "past_due": bool(it.get("past_due")), "can_submit": bool(it.get("can_submit")),
    } for it in items]
    out.sort(key=lambda t: t["due_at"] or "")
    return out


def save_classes(conn, host: str, classes: dict[str, str]) -> None:
    def mutate(doc):
        if "hosts" not in doc:
            doc.update(_new_doc("phll-managebac-classes", hosts={}))
        doc["hosts"][host] = {"updated": fs.now_iso(), "items": dict(classes)}
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_mb_path("classes.json"), mutate,
                   default=_new_doc("phll-managebac-classes", hosts={}))


def save_deadlines(conn, host: str, items: Iterable[Deadline]) -> int:
    rows = [{
        "title": it.title, "course": it.course,
        "due_at": it.due_at.isoformat(timespec="seconds") if it.due_at else None,
        "status": it.status, "category": it.category,
    } for it in items]

    def mutate(doc):
        if "hosts" not in doc:
            doc.update(_new_doc("phll-managebac-deadlines", hosts={}))
        doc["hosts"][host] = {"updated": fs.now_iso(), "items": rows}
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_mb_path("deadlines.json"), mutate,
                   default=_new_doc("phll-managebac-deadlines", hosts={}))
    return len(rows)


def upcoming_deadlines(conn, host: str, days: int = 14) -> list[tuple]:
    doc = fs.load_json(_mb_path("deadlines.json"), {}) or {}
    items = ((doc.get("hosts") or {}).get(host) or {}).get("items") or []
    cutoff = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    rows = [(it.get("title"), it.get("course"), it.get("due_at"),
             it.get("status"), it.get("category")) for it in items
            if it.get("due_at") and it["due_at"] <= cutoff]
    rows.sort(key=lambda r: r[2] or "")
    return rows


# ================================================================ 心履(心情记录)
_XL_COLS = ("uuid", "date", "at", "mood", "note", "intensity_level",
            "intensity_percent", "deleted", "created_at", "updated_at", "dirty")


def _xl_entries(doc: dict) -> list[dict]:
    return doc.setdefault("entries", [])


def _xl_norm(e: dict) -> dict:
    return {
        "uuid": e["uuid"], "date": e["date"], "at": e.get("at"),
        "mood": e["mood"], "note": e.get("note") or "",
        "intensity_level": int(e.get("intensity_level") or 2),
        "intensity_percent": int(e.get("intensity_percent") or 50),
        "deleted": bool(e.get("deleted")),
        "created_at": e.get("created_at"), "updated_at": e["updated_at"],
        "dirty": bool(e.get("dirty")),
    }


def xinlv_state_get(conn, key: str, default: str = "") -> str:
    doc = fs.load_json(_xinlv_path("state.json"), {}) or {}
    val = (doc.get("state") or {}).get(key)
    return default if val is None else str(val)


def xinlv_state_set(conn, key: str, value: str) -> None:
    def mutate(doc):
        if "state" not in doc:
            doc.update(_new_doc("phll-xinlv-state", state={}))
        doc["state"][key] = value
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_xinlv_path("state.json"), mutate,
                   default=_new_doc("phll-xinlv-state", state={}))


def xinlv_upsert(conn, e: dict, dirty: bool) -> None:
    row = _xl_norm(dict(e, dirty=dirty))

    def mutate(doc):
        entries = _xl_entries(doc)
        for i, old in enumerate(entries):
            if old.get("uuid") == row["uuid"]:
                entries[i] = row
                break
        else:
            entries.append(row)
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_xinlv_path("entries.json"), mutate,
                   default=_new_doc("phll-xinlv-entries", entries=[]))


def xinlv_get(conn, uuid: str) -> dict | None:
    doc = fs.load_json(_xinlv_path("entries.json"), {}) or {}
    for e in doc.get("entries") or []:
        if e.get("uuid") == uuid:
            return _xl_norm(e)
    return None


def xinlv_mark_clean(conn, uuids: Iterable[str]) -> None:
    ids = set(uuids)
    if not ids:
        return

    def mutate(doc):
        for e in doc.get("entries") or []:
            if e.get("uuid") in ids:
                e["dirty"] = False
        doc["updated_at"] = fs.now_iso()

    fs.update_json(_xinlv_path("entries.json"), mutate)


def xinlv_dirty(conn) -> list[dict]:
    doc = fs.load_json(_xinlv_path("entries.json"), {}) or {}
    rows = [_xl_norm(e) for e in doc.get("entries") or [] if e.get("dirty")]
    rows.sort(key=lambda r: r.get("updated_at") or "")
    return rows


def xinlv_month(conn, month: str) -> list[dict]:
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        return []
    doc = fs.load_json(_xinlv_path("entries.json"), {}) or {}
    rows = [_xl_norm(e) for e in doc.get("entries") or []
            if not e.get("deleted") and str(e.get("date") or "").startswith(month)]
    rows.sort(key=lambda r: (r["date"], r.get("at") or ""))
    return rows


def xinlv_recent(conn, limit: int = 60) -> list[dict]:
    doc = fs.load_json(_xinlv_path("entries.json"), {}) or {}
    rows = [_xl_norm(e) for e in doc.get("entries") or [] if not e.get("deleted")]
    rows.sort(key=lambda r: (r["date"], r.get("at") or ""), reverse=True)
    return rows[: int(limit)]


def xinlv_pending_count(conn) -> int:
    doc = fs.load_json(_xinlv_path("entries.json"), {}) or {}
    return sum(1 for e in doc.get("entries") or [] if e.get("dirty"))


def xinlv_count_on(conn, day: str) -> int:
    """某天未删除的记录条数(首页/状态栏用)."""
    doc = fs.load_json(_xinlv_path("entries.json"), {}) or {}
    return sum(1 for e in doc.get("entries") or []
               if not e.get("deleted") and e.get("date") == day)


# ================================================================ 旧数据迁移
def migrate_legacy() -> dict:
    """把旧布局搬进新布局(幂等; 旧文件挪进 ``_migrated_backup/``).

    1. ``hellopinghe.db``(SQLite) → ``Schedule`` + ``phll/...``
    2. data/ 根下散落的课表缓存 → ``phll/edupage/``
    3. ``session_*.json`` → ``phll/managebac/``; 通讯录 → ``phll/mail/``
    """
    report = {"sqlite": False, "caches": 0, "session": 0, "mail": 0, "backup": []}
    root = fs.root()
    db = root / _DB_DIR_NAME

    if db.exists():
        report["sqlite"] = _import_sqlite(db)

    # 散落缓存归位(文件名保持, 只是换目录)
    for pat, dest in (("edupage_week_*.json", fs.phll(fs.EDUPAGE_SUB)),
                      ("edupage_personal_*.json", fs.phll(fs.EDUPAGE_SUB)),
                      ("session_*.json", fs.phll(fs.MANAGEBAC_SUB)),
                      ("mail_contacts.json", fs.phll(fs.MAIL_SUB)),
                      ("contacts_custom.json", fs.phll(fs.MAIL_SUB))):
        for src in sorted(root.glob(pat)):
            try:
                dest.mkdir(parents=True, exist_ok=True)
                target = dest / src.name
                if target.exists():
                    continue
                src.replace(target)
                if "edupage" in pat:
                    report["caches"] += 1
                elif "session" in pat:
                    report["session"] += 1
                else:
                    report["mail"] += 1
            except OSError:
                continue

    report["backup"] = fs.move_all_legacy()
    return report


def _import_sqlite(db: Path) -> bool:
    """读旧库并写入新文件(逐表容错; 读不动的表直接跳过)."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except Exception:  # noqa: BLE001
        return False

    def table(name: str) -> list[tuple]:
        try:
            return conn.execute(f"SELECT * FROM {name}").fetchall()
        except Exception:  # noqa: BLE001
            return []

    def cols(name: str) -> list[str]:
        try:
            return [r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()]
        except Exception:  # noqa: BLE001
            return []

    try:
        # 日程
        ev_cols = cols("events")
        if ev_cols:
            rows = table("events")
            if rows and not (fs.root() / fs.SCHEDULE).exists():
                events = []
                for r in rows:
                    d = dict(zip(ev_cols, r))
                    events.append({
                        "id": int(d.get("id") or 0), "day": d.get("day", ""),
                        "time": d.get("time") or "", "title": d.get("title", ""),
                        "note": d.get("note") or "",
                        "created": str(d.get("created") or fs.now_iso()),
                    })
                fs.save_json(fs.root() / fs.SCHEDULE,
                             _new_doc("pinghe-schedule", events=events))

        # DDL 移除
        dd_cols = cols("ddl_dismissed")
        if dd_cols:
            rows = table("ddl_dismissed")
            if rows:
                state = fs.load_json(_state_path(), None) or _new_doc("phll-state")
                bucket = state.setdefault("ddl_dismissed", {})
                for r in rows:
                    d = dict(zip(dd_cols, r))
                    host = d.get("host") or ""
                    bucket.setdefault(host, []).append(
                        {"key": d.get("dkey"), "created": str(d.get("created") or "")})
                fs.save_json(_state_path(), state)

        # 作业缓存
        tc_cols = cols("tasks_cache")
        if tc_cols:
            rows = table("tasks_cache")
            meta = {dict(zip(cols("tasks_meta"), r)).get("host"): dict(zip(cols("tasks_meta"), r)).get("updated")
                    for r in table("tasks_meta")} if cols("tasks_meta") else {}
            if rows:
                doc = fs.load_json(_mb_path("tasks.json"), None) or _new_doc("phll-managebac-tasks", hosts={})
                for r in rows:
                    d = dict(zip(tc_cols, r))
                    host = d.get("host") or ""
                    slot = doc["hosts"].setdefault(host, {"updated": meta.get(host) or fs.now_iso(), "items": []})
                    slot["items"].append({
                        "task_id": d.get("task_id", ""), "class_id": d.get("class_id", ""),
                        "class_name": d.get("class_name", ""), "title": d.get("title", ""),
                        "due_at": d.get("due_at"), "status": d.get("status"),
                        "past_due": bool(d.get("past_due")), "can_submit": bool(d.get("can_submit")),
                    })
                fs.save_json(_mb_path("tasks.json"), doc)

        # 课程名与 DDL 快照
        cl_cols = cols("classes")
        if cl_cols and table("classes"):
            doc = fs.load_json(_mb_path("classes.json"), None) or _new_doc("phll-managebac-classes", hosts={})
            for r in table("classes"):
                d = dict(zip(cl_cols, r))
                doc["hosts"].setdefault(d.get("host") or "", {"updated": str(d.get("updated") or ""), "items": {}})
                doc["hosts"][d.get("host") or ""]["items"][d.get("class_id", "")] = d.get("name", "")
            fs.save_json(_mb_path("classes.json"), doc)

        dl_cols = cols("deadlines")
        if dl_cols and table("deadlines"):
            doc = fs.load_json(_mb_path("deadlines.json"), None) or _new_doc("phll-managebac-deadlines", hosts={})
            for r in table("deadlines"):
                d = dict(zip(dl_cols, r))
                slot = doc["hosts"].setdefault(d.get("host") or "", {"updated": str(d.get("updated") or ""), "items": []})
                slot["items"].append({"title": d.get("title", ""), "course": d.get("course"),
                                      "due_at": d.get("due_at"), "status": d.get("status"),
                                      "category": d.get("category")})
            fs.save_json(_mb_path("deadlines.json"), doc)

        # 心履
        xe_cols = cols("xinlv_entries")
        if xe_cols:
            rows = table("xinlv_entries")
            if rows:
                doc = fs.load_json(_xinlv_path("entries.json"), None) or _new_doc("phll-xinlv-entries", entries=[])
                for r in rows:
                    d = dict(zip(xe_cols, r))
                    doc["entries"].append(_xl_norm({
                        "uuid": d.get("uuid"), "date": d.get("date"), "at": d.get("at"),
                        "mood": d.get("mood"), "note": d.get("note"),
                        "intensity_level": d.get("intensity_level"),
                        "intensity_percent": d.get("intensity_percent"),
                        "deleted": bool(d.get("deleted")), "created_at": d.get("created_at"),
                        "updated_at": d.get("updated_at"), "dirty": bool(d.get("dirty")),
                    }))
                fs.save_json(_xinlv_path("entries.json"), doc)

        xs_cols = cols("xinlv_state")
        if xs_cols:
            rows = table("xinlv_state")
            if rows:
                doc = fs.load_json(_xinlv_path("state.json"), None) or _new_doc("phll-xinlv-state", state={})
                for r in rows:
                    d = dict(zip(xs_cols, r))
                    doc["state"][str(d.get("key"))] = d.get("value")
                fs.save_json(_xinlv_path("state.json"), doc)
        return True
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
