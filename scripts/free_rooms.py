"""空闲教室验证: 一次请求拿全校当天课表 → 计算此刻每间教室的占用."""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hellopinghe.config import Config

cfg = Config.load()

try:
    from edupage_api import Edupage

    ed = Edupage()
    original = ed.session.request

    def _request(method, url, **kwargs):  # noqa: ANN001, ANN003
        if not kwargs.get("timeout") or kwargs.get("timeout") < 40:
            kwargs["timeout"] = 40
        return original(method, url, **kwargs)

    ed.session.request = _request
    ed.login(cfg.edupage_username, os.environ["EP_PASS"], cfg.edupage_subdomain)

    # 1) 全校教室清单
    rooms = ed.get_classrooms()
    print(f"全校教室数: {len(rooms)}")
    print("样例:", ", ".join(f"{r.name}" for r in rooms[:15]))

    # 2) 一次请求: 今天全校所有课(含每节课的教室/老师/科目)
    today = date.today()
    tt = ed.get_my_timetable(today)
    lessons = [l for l in tt if not l.is_cancelled]
    print(f"今天全校课程卡: {len(lessons)} 张(已取消的已剔除)")

    # 支持模拟时刻: python free_rooms.py 10:00
    probe_times = [datetime.now().time()]
    if len(sys.argv) > 1:
        probe_times = [datetime.strptime(t, "%H:%M").time() for t in sys.argv[1:]]

    for now in probe_times:
        occupied: dict[str, str] = {}
        for l in lessons:
            if not l.start_time or not l.end_time:
                continue
            if l.start_time <= now < l.end_time:
                subject = l.subject.name if l.subject else "?"
                teacher = l.teachers[0].name if l.teachers else "-"
                span = f"{l.start_time.strftime('%H:%M')}-{l.end_time.strftime('%H:%M')}"
                for room in l.classrooms or []:
                    occupied.setdefault(room.name, f"{span} {subject} ({teacher})")

        print(f"\n时刻 {now.strftime('%H:%M')}: 占用 {len(occupied)} 间 / 空闲 {len(rooms) - len(occupied)} 间")
        print("占用明细(前 12 间):")
        for name in sorted(occupied)[:12]:
            print(f"  {name:<16} {occupied[name]}")
except Exception:  # noqa: BLE001
    import traceback

    traceback.print_exc()
