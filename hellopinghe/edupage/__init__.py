"""Edupage 连接器: 薄封装 edupage-api(GPL-3.0, 作为 pip 依赖使用).

课表是本连接器的核心职责 —— ManageBac 没有课表,课程表数据 100% 来自 Edupage。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

from ..exceptions import PingheError


@dataclass
class LessonView:
    """统一课表条目(供 UI / agent / 提醒器消费)."""

    period: int | None
    start: str          # "08:00"
    end: str            # "08:45"
    subject: str
    teacher: str
    classroom: str
    groups: str
    cancelled: bool


def login(username: str, password: str, subdomain: str = ""):
    """登录 Edupage, 返回 edupage_api.Edupage 实例.

    subdomain 留空时 edupage-api 的 login_auto 会从用户名邮箱自动推断。
    """
    try:
        from edupage_api import Edupage
    except ImportError as exc:  # pragma: no cover
        raise PingheError("edupage-api 未安装: pip install edupage-api") from exc

    edupage = Edupage()
    if subdomain:
        edupage.login(username, password, subdomain)
    else:
        edupage.login_auto(username, password)
    return edupage


def fetch_timetable(edupage, day: _dt.date | None = None) -> list[LessonView]:
    """抓取某天(默认今天)的本人课表并转成统一视图."""
    day = day or _dt.date.today()
    timetable = edupage.get_my_timetable(day)

    lessons: list[LessonView] = []
    for lesson in timetable:
        lessons.append(LessonView(
            period=getattr(lesson, "period", None),
            start=str(lesson.start_time.strftime("%H:%M")) if lesson.start_time else "",
            end=str(lesson.end_time.strftime("%H:%M")) if lesson.end_time else "",
            subject=lesson.subject.name if lesson.subject else "(无科目)",
            teacher=(lesson.teachers[0].name if lesson.teachers else "") or "",
            classroom=(lesson.classrooms[0].name if getattr(lesson, "classrooms", None) else "") or "",
            groups=", ".join(lesson.groups) if getattr(lesson, "groups", None) else "",
            cancelled=bool(getattr(lesson, "is_cancelled", False)),
        ))
    lessons.sort(key=lambda l: (l.start, l.period or 0))
    return lessons
