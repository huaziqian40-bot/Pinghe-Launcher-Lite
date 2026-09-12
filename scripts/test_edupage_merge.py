# -*- coding: utf-8 -*-
"""验证 PLL 的 School 合并: 对方写的整周课表不会被"按天写"覆盖掉.

    python -X utf8 scripts/test_edupage_merge.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe import sharedschool  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        # 1) 对方(PHL)写了整周 30 张卡, week_start 是周一
        big = [{"date": "2026-09-07", "start": "08:00", "end": "08:40",
                "subject": f"课程{i}", "teacher": "T", "room": "R", "group": "A" if i % 2 else "",
                "cancelled": False} for i in range(30)]
        sharedschool.update({"edupage": {"week_start": "2026-09-07", "fetched_at": "x",
                                         "class_name": "", "lessons": big,
                                         "selected_groups": []}}, data)
        after_first = sharedschool.read(data)["edupage"]
        check(len(after_first["lessons"]) == 30, f"首次写入应有 30 张: {len(after_first['lessons'])}")

        # 2) PLL 按天写一天(week_start 是当天的周一) -> 应当合并, 不是替换
        sharedschool.update({"edupage": sharedschool.edupage_section({"2026-09-09": [
            {"date": "2026-09-09", "start": "09:00", "end": "09:40", "subject": "PLL 的课", "group": "P"},
        ]})}, data)
        merged = sharedschool.read(data)["edupage"]
        check(len(merged["lessons"]) == 31, f"按天写后应合并成 31 张, 实际 {len(merged['lessons'])}")
        check(merged["week_start"] == "2026-09-07", f"周锚点应保留 2026-09-07, 实际 {merged['week_start']}")

        # 3) 换成另一周 -> 整段替换
        sharedschool.update({"edupage": sharedschool.edupage_section({"2026-09-16": [
            {"date": "2026-09-16", "start": "09:00", "end": "09:40", "subject": "下周的课", "group": "P"},
        ]})}, data)
        next_week = sharedschool.read(data)["edupage"]
        check(len(next_week["lessons"]) == 1, f"换周应只剩 1 张, 实际 {len(next_week['lessons'])}")
        check(next_week["week_start"] == "2026-09-14", f"周锚点应为 2026-09-14, 实际 {next_week['week_start']}")

    print("School edupage 合并自检 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
