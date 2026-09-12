# -*- coding: utf-8 -*-
"""兜底来源选择自检: 共用课表里"带教学组的那一份"优先, 否则个人课表会变成整班课表.

    python -X utf8 scripts/test_shared_day_source.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe.app.services import EdupageService  # noqa: E402

DAY = "2026-09-14"


class _Stub:
    """只需 _shared_day_cards 用到的环境(不连网)。"""

    cfg = type("Cfg", (), {"selected_lessons": []})()

    _shared_day_cards = EdupageService._shared_day_cards


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def card(subject: str, group: str, with_date: bool = False) -> dict:
    row = {"subject": subject, "teacher": "T", "room": "R", "start": "08:00", "end": "08:40",
           "group": group, "cancelled": False}
    if with_date:            # School 的 edupage 段每张卡自带日期
        row["date"] = DAY
    return row


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        # 情况一: School 没有这一天, Timetable 有(且带组)
        write(data / "School", {"version": 1, "kind": "pinghe-school", "edupage": None})
        write(data / "Timetable", {"version": 1, "kind": "pinghe-timetable",
                                   "days": {DAY: [card("数学", "A"), card("班会", "")]}})
        import os

        os.environ["PHLL_DATA_DIR"] = str(data)
        stub = _Stub()
        picked = stub._shared_day_cards(DAY)
        assert [c["subject"] for c in picked] == ["数学", "班会"], picked

        # 情况二: School 有带组的课卡, Timetable 全是丢组的旧数据 → 必须选 School
        write(data / "School", {"version": 1, "kind": "pinghe-school",
                                "edupage": {"week_start": "2026-09-14", "lessons": [card("我的课", "P", with_date=True)]}})
        write(data / "Timetable", {"version": 1, "kind": "pinghe-timetable",
                                   "days": {DAY: [card("整班课1", ""), card("整班课2", "")]}})
        picked = stub._shared_day_cards(DAY)
        assert [c["subject"] for c in picked] == ["我的课"], picked

        # 情况三: 两份都没有教学组 → 有数据就用(并留日志线索), 不抛错
        write(data / "School", {"version": 1, "kind": "pinghe-school", "edupage": None})
        write(data / "Timetable", {"version": 1, "kind": "pinghe-timetable",
                                   "days": {DAY: [card("整班课", "")]}})
        picked = stub._shared_day_cards(DAY)
        assert [c["subject"] for c in picked] == ["整班课"], picked

        # 情况四: 都没有这一天 → 空列表
        assert stub._shared_day_cards("2026-09-20") == []

    print("共用课表兜底来源自检 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
