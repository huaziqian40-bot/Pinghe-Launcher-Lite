"""读取三样东西: Edupage 个人课表 + ManageBac 课程 + 全部作业."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hellopinghe.config import CONFIG_DIR, Config
from hellopinghe.managebac.client import ManageBacClient

cfg = Config.load()
DATA = Path(__file__).resolve().parents[1] / "data"

# ============================================================ Edupage 个人课表
print("== Edupage 个人课表(按学生过滤)==")
try:
    from edupage_api import Edupage
    from edupage_api.people import EduStudent, Gender

    ed = Edupage()
    original = ed.session.request

    def _request(method, url, **kwargs):  # noqa: ANN001, ANN003
        if not kwargs.get("timeout") or kwargs.get("timeout") < 40:
            kwargs["timeout"] = 40
        return original(method, url, **kwargs)

    ed.session.request = _request
    ed.login(cfg.edupage_username, os.environ["EP_PASS"], cfg.edupage_subdomain)

    uid = str(ed.get_user_id())
    numeric = int(re.sub(r"\D", "", uid) or 0)
    # 学生表 id 是负数(get_all_students 返回的是 EduStudentSkeleton, id 形如 -3772)
    person_id = -numeric
    me = EduStudent(person_id, "me", list(Gender)[0], None, 0, 0)
    print(f"user_id: {uid} → person_id: {person_id}")

    for offset in range(2):
        day = date.today() + timedelta(days=offset)
        tt = ed.get_timetable(me, day)
        lessons = list(tt) if tt else []
        print(f"\n-- {day} 周{'一二三四五六日'[day.weekday()]}: {len(lessons)} 节 --")
        for l in lessons:
            subject = l.subject.name if l.subject else "?"
            teacher = l.teachers[0].name if l.teachers else "-"
            room = l.classrooms[0].name if l.classrooms else "-"
            groups = ",".join(l.groups) if l.groups else ""
            start = l.start_time.strftime("%H:%M") if l.start_time else "??:??"
            end = l.end_time.strftime("%H:%M") if l.end_time else "??:??"
            mark = " [取消]" if l.is_cancelled else ""
            print(f"  {start}-{end}  {subject:<36} {teacher:<20} {room:<12} {groups}{mark}")
except Exception:  # noqa: BLE001
    import traceback

    traceback.print_exc()

# ============================================================ ManageBac 课程 + 作业
print("\n== ManageBac 课程 ==")
try:
    host = cfg.managebac_base_url.split("//")[-1]
    client = ManageBacClient(cfg.managebac_base_url)
    for name_, value in json.loads(
        (CONFIG_DIR / f"session_{host}.json").read_text(encoding="utf-8")
    )["cookies"].items():
        client.session.cookies.set(name_, value)

    classes = client.get_classes()
    for cid, name in classes.items():
        print(f"  {cid:>9}  {name}")

    print("\n== ManageBac 全部作业(按截止时间排序)==")
    tasks = client.get_all_tasks()
    now = None
    for t in tasks:
        due = t.due_at.strftime("%m-%d %H:%M") if t.due_at else "??"
        flag = "!" if (t.past_due or (t.status or "").lower() in ("pending", "not submitted")) else " "
        print(f" {flag} {due}  [{(t.status or '?'):<11}] {t.class_name[:28]:<30} {t.title}")
    pending = [t for t in tasks if (t.status or "").lower() == "pending"]
    print(f"\n共 {len(tasks)} 张卡, 其中 Pending {len(pending)} 张")
    upcoming = [t for t in tasks if t.due_at and not t.past_due]
    print(f"未截止 {len(upcoming)} 张:")
    for t in upcoming:
        print(f"   {t.due_at.strftime('%m-%d %H:%M')}  {t.class_name[:30]}: {t.title}  (可提交={t.can_submit})")
except Exception:  # noqa: BLE001
    import traceback

    traceback.print_exc()
