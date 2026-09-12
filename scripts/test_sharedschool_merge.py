# -*- coding: utf-8 -*-
"""共用学校数据 data/School 的合并规则自检(不需要网络)。

    python scripts/test_sharedschool_merge.py

覆盖: managebac 按 id 取并集(两边都不能抹掉对方抓到的课/作业)、
edupage 同一周取并集/换周整段替换、空段与坏段不抛错、整段读写进文件后仍是并集。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe import sharedschool  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    # 1. managebac: 两边抓到的条目都要留下
    first = sharedschool.merge_managebac(None, {
        "fetched_at": "2026-09-11T11:00:00+08:00",
        "courses": [{"id": "11", "name": "Biology", "grade": "6"},
                    {"id": "12", "name": "Physics", "grade": "6"}],
        "tasks": [{"id": "t1", "course_id": "11", "title": "Lab", "status": "Pending"}],
    })
    merged = sharedschool.merge_managebac(first, {
        "fetched_at": "2026-09-11T11:05:00+08:00",
        "courses": [{"id": "11", "name": "Biology HL", "grade": "6"}],
        "tasks": [{"id": "t1", "course_id": "11", "title": "Lab report", "status": "Submitted"},
                  {"id": "t2", "course_id": "12", "title": "Essay", "status": "Pending"}],
    })
    check([c["id"] for c in merged["courses"]] == ["11", "12"], "Lite 没抓到的课不会被删")
    check(merged["courses"][0]["name"] == "Biology HL", "同 id 用新抓到的字段")
    check([t["id"] for t in merged["tasks"]] == ["t1", "t2"], "作业同样取并集")
    check(merged["fetched_at"] == "2026-09-11T11:05:00+08:00", "fetched_at 用新的一份")

    # 2. edupage: 同一周并集, 换周替换
    def lesson(date: str, subject: str, group: str) -> dict:
        return {"date": date, "start": "08:00", "end": "08:40", "subject": subject,
                "teacher": "T", "room": "R", "group": group, "cancelled": False}

    same_week = sharedschool.merge_edupage(
        {"week_start": "2026-09-07", "fetched_at": "a", "class_name": "G10",
         "lessons": [lesson("2026-09-07", "Math", "A")]},
        {"week_start": "2026-09-07", "fetched_at": "b", "class_name": "G10",
         "lessons": [lesson("2026-09-07", "Math", "A"), lesson("2026-09-08", "Physics", "B")]},
    )
    check(len(same_week["lessons"]) == 2, "同一周取并集, 重复课卡合并成一条")

    next_week = sharedschool.merge_edupage(
        {"week_start": "2026-09-07", "fetched_at": "a", "lessons": [lesson("2026-09-07", "Math", "A")]},
        {"week_start": "2026-09-14", "fetched_at": "b", "lessons": [lesson("2026-09-14", "Math", "A")]},
    )
    check(len(next_week["lessons"]) == 1 and next_week["week_start"] == "2026-09-14", "换周整段替换")

    # 1.5 空段不许删数据: 按天写时空的周末/跨周空写入不能清掉对方写好的整周课表
    full_week = {"week_start": "2026-09-07", "fetched_at": "a",
                 "lessons": [lesson("2026-09-07", "Math", "A"), lesson("2026-09-08", "Physics", "B")]}
    empty_same_week = sharedschool.merge_edupage(full_week, {"week_start": "2026-09-07", "lessons": []})
    check(len(empty_same_week["lessons"]) == 2, "同一周的空写入不该清掉课表")
    empty_next_week = sharedschool.merge_edupage(full_week, {"week_start": "2026-09-14", "lessons": []})
    check(len(empty_next_week["lessons"]) == 2, "跨周的空写入也不该清掉课表")
    real_next_week = sharedschool.merge_edupage(full_week, {"week_start": "2026-09-14",
                                                            "lessons": [lesson("2026-09-14", "New", "A")]})
    check(len(real_next_week["lessons"]) == 1, "真有课的一周才替换")

    # 2.5 作业 id 归一化: PHL 老数据写的是复合 id, 新版写裸作业号 -> 同一条只留一份
    legacy = {"fetched_at": "a",
              "courses": [{"id": "11", "name": "Biology"}],
              "tasks": [{"id": "managebac:11:22", "course_id": "11", "course": "Biology",
                         "title": "Lab", "due_at": "2026-09-20T23:59", "status": "Pending"}]}
    fresh = {"fetched_at": "b",
             "courses": [{"id": "11", "name": "Biology"}],
             "tasks": [{"id": "22", "course_id": "11", "course": "Biology",
                        "title": "Lab", "due_at": "2026-09-20T23:59", "status": "Submitted"}]}
    merged_tasks = sharedschool.merge_managebac(legacy, fresh)
    check(len(merged_tasks["tasks"]) == 1, f"复合 id 与裸 id 应合并成一条, 实际 {len(merged_tasks['tasks'])}")
    check(merged_tasks["tasks"][0]["id"] == "22", "留下的应是新抓到的裸作业号")

    # 3. 空段/坏段
    check(sharedschool.merge_managebac(None, None) is None, "两个空段 -> None")
    check(sharedschool.merge_edupage({}, {"week_start": "2026-09-07"})["week_start"] == "2026-09-07", "已有段坏/空时用新的")
    check(sharedschool.merge_edupage({}, None) == {}, "坏段原样返回")
    check(sharedschool.merge_managebac({"courses": "bad", "tasks": None},
                                       {"courses": [{"id": "1"}], "tasks": []})["courses"] == [{"id": "1"}],
          "坏 courses 不抛错")

    # 4. 真的写进文件: 两边先后 update 后仍是并集
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        sharedschool.update({"managebac": sharedschool.managebac_section(
            [{"class_id": "11", "class_name": "Biology"}, {"class_id": "12", "class_name": "Physics"}],
            [{"task_id": "t1", "class_id": "11", "class_name": "Biology", "title": "Lab"}])}, data)
        sharedschool.update({"managebac": sharedschool.managebac_section(
            [{"class_id": "11", "class_name": "Biology"}],
            [{"task_id": "t2", "class_id": "12", "class_name": "Physics", "title": "Essay"}])}, data)
        doc = sharedschool.read(data)
        check(len(doc["managebac"]["courses"]) == 2, "写进文件后课程仍是并集")
        check(len(doc["managebac"]["tasks"]) == 2, "写进文件后作业仍是并集")
        check(doc["kind"] == sharedschool.KIND and doc["app"] == sharedschool.APP, "kind/app 正确")

    print("sharedschool merge self-check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
