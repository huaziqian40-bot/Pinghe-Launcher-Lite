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
import re
import threading
from datetime import datetime, timedelta
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


def _bare_task_id(value) -> str:
    """作业 id 取"ManageBac 自己的作业号"。

    PHL 内部用复合 id(``managebac:<课程号>:<作业号>``), Lite 用裸作业号; 共用文件里
    统一写裸号, 但读/合并时都要容错(老数据可能是复合形式), 否则同一条作业会留两份。
    """
    text = _clean(value, 120)
    match = re.match(r"^managebac:\d+:(\d+)$", text)
    return match.group(1) if match else text


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


def _merge_rows(existing, incoming, key_of, limit: int) -> list[dict]:
    """按 id 取并集: 已有的条目保留, 同 id 用新抓到的覆盖。"""
    merged: dict[str, dict] = {}
    for row in existing or []:
        if isinstance(row, dict):
            key = key_of(row)
            if key:
                merged[key] = row
    for row in incoming or []:
        if isinstance(row, dict):
            key = key_of(row)
            if key:
                merged[key] = row
    return list(merged.values())[:limit]


def merge_managebac(existing: dict | None, incoming: dict | None) -> dict | None:
    """两个程序都会写 managebac 段, 整段覆盖会互相抹掉对方的条目
    (一边 20 门课/7 份作业, 一边 13 门课/51 份作业)。这里按 MB 自己的 id 取并集:
    同一个数据文件夹意味着同一个账号, id 相同即同一条, 新抓到的字段更可信。
    """
    if not isinstance(existing, dict):
        return incoming or None
    if not isinstance(incoming, dict):
        return existing
    return {
        "fetched_at": _clean(incoming.get("fetched_at"), 40) or _clean(existing.get("fetched_at"), 40),
        "courses": _merge_rows(existing.get("courses"), incoming.get("courses"),
                               lambda row: _clean(row.get("id"), 32), 60),
        "tasks": _merge_rows(existing.get("tasks"), incoming.get("tasks"),
                             lambda row: _bare_task_id(row.get("id") or row.get("phl_id")), 600),
    }


def merge_edupage(existing: dict | None, incoming: dict | None) -> dict | None:
    """edupage 段只保留当前这一周: 不在同一周就整段替换, 同一周按课卡取并集。

    同一周按"所在周的周一"比较, 不比对字符串: 两个程序给的 `week_start` 不一定
    同一天(PHL 用 EduPage 自己的一周锚点, PLL 按天算课表时用的是当天的周一),
    字符串比较会把同一周当成"换了一周", 于是把对方写好的整周课表整段替换掉。
    """
    if not isinstance(existing, dict):
        return incoming or None
    if not isinstance(incoming, dict):
        return existing
    if _week_start_of(_clean(existing.get("week_start"), 20)) != _week_start_of(_clean(incoming.get("week_start"), 20)):
        return incoming
    merged = dict(incoming)
    if _clean(existing.get("week_start"), 20):
        merged["week_start"] = _clean(existing.get("week_start"), 20)  # 保留原来那一周的锚点
    merged["lessons"] = _merge_rows(
        existing.get("lessons"), incoming.get("lessons"),
        lambda row: "|".join([_clean(row.get("date"), 20), _clean(row.get("start"), 5),
                              _clean(row.get("subject")), _clean(row.get("group"), 80)]),
        2000,
    )
    if not (incoming.get("selected_groups") or []):
        merged["selected_groups"] = existing.get("selected_groups") or []
    return merged


def update(sections: dict, data_dir: Path | None = None) -> dict:
    """只替换传入的段, 其余段落与未知字段原样保留, 原子落盘。

    段内按 id 取并集(见 merge_managebac / merge_edupage), 免得两个程序互相覆盖。
    """
    target = _path(data_dir)
    with _lock:
        original = read(data_dir)
        # 浅拷贝: 后面替换段时不能把 original 里的旧段一起改掉,
        # 否则下面按 id 取并集时会拿"新段"和"新段"合并, 旧条目就丢了。
        doc = dict(original)
        if not doc:
            doc = {"version": 1, "kind": KIND, "app": APP, "updated_at": _now_iso(),
                   "edupage": None, "managebac": None, "mail": None}
        for section in ("edupage", "managebac", "mail"):
            if section in sections and sections[section] is not None:
                doc[section] = sections[section]
            elif section not in doc:
                doc[section] = None
        if isinstance(sections.get("managebac"), dict):
            doc["managebac"] = merge_managebac(original.get("managebac"), sections["managebac"])
        if isinstance(sections.get("edupage"), dict):
            doc["edupage"] = merge_edupage(original.get("edupage"), sections["edupage"])
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


def _week_start_of(day: str) -> str:
    """某一天所在那一周的周一(共享段用"周一日期"标识一周)。"""
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return day
    return (d - timedelta(days=d.weekday())).isoformat()


def edupage_section(days_by_date: dict[str, list[dict]], selected_groups: list[str] | None = None) -> dict:
    """按天课卡(与 personal() 同键) → 共享段。

    ``week_start`` 用**这一天的周一**, 不是这一天本身: PLL 是按天算课表的
    (`personal(day)`), 如果写成当天日期, 它每写一天都会被当成"换了一周",
    共享段里 PH Launcher 写好的整周课表就被整段替换掉了。
    """
    lessons = []
    for day in sorted((days_by_date or {}).keys()):
        for row in _lesson_rows(days_by_date[day]):
            lessons.append({**row, "date": row["date"] or day})
    first_day = min(days_by_date) if days_by_date else ""
    return {
        "week_start": _week_start_of(first_day) if first_day else "",
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


def managebac_tasks_for_pll(data_dir: Path | None = None) -> list[dict]:
    """共享 managebac 段的作业 → PLL 自己的作业字典形状。

    PH Launcher 写进去的字段是 ``id/course_id/course/title/due_at/due_text/status/score``,
    而 PLL 的界面按 ``task_id/class_id/class_name/.../past_due/can_submit`` 取值。
    不转换就直接喂给界面会 `KeyError: 'past_due'`, 整页"我的课程"报错。
    """
    section = read(data_dir).get("managebac") or {}
    now = datetime.now().astimezone()
    out: list[dict] = []
    for row in section.get("tasks") or []:
        if not isinstance(row, dict):
            continue
        title = _clean(row.get("title"))
        if not title:
            continue
        due_at = row.get("due_at")
        due_at = _clean(due_at, 40) if due_at else None
        past_due = False
        if due_at:
            try:
                parsed = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=now.tzinfo)
                past_due = parsed < now
            except ValueError:
                past_due = False
        out.append({
            "task_id": _bare_task_id(row.get("id") or row.get("phl_id")),
            "class_id": _clean(row.get("course_id"), 40),
            "class_name": _clean(row.get("course")),
            "title": title,
            "due_at": due_at,
            "status": _clean(row.get("status"), 80),
            "past_due": past_due,
            "can_submit": False,
        })
    out.sort(key=lambda item: item["due_at"] or "")
    return out


def mail_summary(data_dir: Path | None = None) -> dict:
    """另一个程序上次同步到的邮箱摘要(只有未读数与邮件头部, 没有正文)。

    本机没登录邮箱时用它兜底显示; 返回 {"unread": int, "items": [...], "fetched_at": str};
    没有共享数据时 unread=0、items 为空。items 里的 seen 是给界面用的取反字段。
    """
    section = read(data_dir).get("mail") or {}
    items = []
    for row in section.get("recent") or []:
        if not isinstance(row, dict):
            continue
        items.append({
            "uid": _clean(row.get("uid"), 20),
            "from": _clean(row.get("from"), 160),
            "subject": _clean(row.get("subject")),
            "date": _clean(row.get("date"), 60),
            "seen": not bool(row.get("unread")),
        })
    return {
        "unread": int(section.get("unread") or 0),
        "items": items,
        "fetched_at": _clean(section.get("fetched_at"), 40),
    }
