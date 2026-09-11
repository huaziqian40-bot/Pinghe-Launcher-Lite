# -*- coding: utf-8 -*-
"""共用学校数据 data/School 的读写(PLL 侧).

与 PH Launcher 的 electron/shared-school.cjs 同一格式:
    { version, kind: "pinghe-school", app, updated_at,
      edupage:   { week_start, fetched_at, class_name, lessons[], selected_groups[] },
      managebac: { fetched_at, courses[], tasks[] },
      mail:      { fetched_at, unread, recent[] } }

规则: 容错读取(缺失/损坏=没有共享数据)、只替换自己负责的段、原子写入、
时间戳带本地时区偏移。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

KIND = "pinghe-school"
APP = "Pinghe Launcher Lite"
_lock = threading.Lock()


def _path(data_dir: Path | None = None) -> Path:
    if data_dir is None:
        from . import paths

        data_dir = paths.data_dir()
    return Path(data_dir) / "School"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clean(value, limit: int = 200) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def read(data_dir: Path | None = None) -> dict:
    """容错读取; 拿不到就返回空文档(绝不抛错)。"""
    try:
        raw = _path(data_dir).read_text(encoding="utf-8")
        doc = json.loads(raw)
        if isinstance(doc, dict) and doc.get("kind") == KIND:
            return doc
    except Exception:  # noqa: BLE001
        pass
    return {}


def update(sections: dict, data_dir: Path | None = None) -> dict:
    """只替换传入的段, 其余段落与未知字段原样保留, 原子落盘。"""
    target = _path(data_dir)
    with _lock:
        doc = read(data_dir)
        if not doc:
            doc = {"version": 1, "kind": KIND, "app": APP, "updated_at": _now_iso(),
                   "edupage": None, "managebac": None, "mail": None}
        for section in ("edupage", "managebac", "mail"):
            if section in sections and sections[section] is not None:
                doc[section] = sections[section]
            elif section not in doc:
                doc[section] = None
        doc["version"] = 1
        doc["kind"] = KIND
        doc["app"] = APP
        doc["updated_at"] = _now_iso()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    return doc


def _lesson_rows(lessons: list[dict]) -> list[dict]:
    rows = []
    for lesson in lessons or []:
        if not isinstance(lesson, dict):
            continue
        subject = _clean(lesson.get("subject"))
        if not subject:
            continue
        rows.append({
            "date": _clean(lesson.get("date"), 20),
            "start": _clean(lesson.get("start"), 5),
            "end": _clean(lesson.get("end"), 5),
            "subject": subject,
            "teacher": _clean(lesson.get("teacher"), 100),
            "room": _clean(lesson.get("room"), 60),
            "group": _clean(lesson.get("group"), 80),
            "cancelled": bool(lesson.get("cancelled")),
        })
    return rows


def edupage_section(days_by_date: dict[str, list[dict]], selected_groups: list[str] | None = None) -> dict:
    """按天课卡(与 personal() 同键) → 共享段。"""
    lessons = []
    for day in sorted((days_by_date or {}).keys()):
        for row in _lesson_rows(days_by_date[day]):
            lessons.append({**row, "date": row["date"] or day})
    return {
        "week_start": min(days_by_date) if days_by_date else "",
        "fetched_at": _now_iso(),
        "class_name": "",
        "lessons": lessons,
        "selected_groups": [_clean(item, 160) for item in (selected_groups or []) if _clean(item)],
    }


def edupage_days(data_dir: Path | None = None) -> dict[str, list[dict]]:
    """共享段 → PLL 自己的按天课卡形状(读不到就空)。"""
    section = read(data_dir).get("edupage") or {}
    days: dict[str, list[dict]] = {}
    for row in section.get("lessons") or []:
        day = _clean(row.get("date"), 20)
        if not day:
            continue
        days.setdefault(day, []).append({
            "start": _clean(row.get("start"), 5),
            "end": _clean(row.get("end"), 5),
            "subject": _clean(row.get("subject")),
            "teacher": _clean(row.get("teacher"), 100),
            "room": _clean(row.get("room"), 60),
            "group": _clean(row.get("group"), 80),
            "cancelled": bool(row.get("cancelled")),
            "curriculum": "",
        })
    for day in days:
        days[day].sort(key=lambda item: (item["start"], item["subject"]))
    return days


def managebac_section(courses: list[dict], tasks: list[dict]) -> dict:
    return {
        "fetched_at": _now_iso(),
        "courses": [
            {"id": _clean(c.get("class_id") or c.get("id"), 32), "name": _clean(c.get("class_name") or c.get("name")),
             "grade": _clean(c.get("grade"), 80)}
            for c in (courses or []) if isinstance(c, dict)
        ][:60],
        "tasks": [
            {"id": _clean(t.get("task_id") or t.get("id"), 120),
             "course_id": _clean(t.get("class_id") or t.get("course_id"), 32),
             "course": _clean(t.get("class_name") or t.get("course")),
             "title": _clean(t.get("title")),
             "due_at": _clean(t.get("due_at"), 40),
             "due_text": _clean(t.get("due_text"), 160),
             "status": _clean(t.get("status"), 80),
             "score": _clean(t.get("score"), 80)}
            for t in (tasks or []) if isinstance(t, dict)
        ][:600],
    }


def mail_section(unread: int, recent: list[dict] | None = None) -> dict:
    return {
        "fetched_at": _now_iso(),
        "unread": int(unread or 0),
        "recent": [
            {"uid": _clean(m.get("uid"), 20), "from": _clean(m.get("from"), 160),
             "subject": _clean(m.get("subject")), "date": _clean(m.get("date"), 60),
             "unread": bool(m.get("unread"))}
            for m in (recent or []) if isinstance(m, dict)
        ][:30],
    }
