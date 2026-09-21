# -*- coding: utf-8 -*-
"""国家理科合并：共用文件里"三张轮换卡 + 云端合并好的一条"并存时只显示一条。

用户 2026-09-21 报的现象：国家课程那一格显示成四张卡
（国家物理、国家化学、国家生物、国家理科）—— 因为共用课表是多个程序写入的并集。

    python -X utf8 scripts/test_native_science_merge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe.app.services import (  # noqa: E402
    NATIVE_SCIENCE_LABEL, merge_native_science)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("  OK", message)


def card(subject: str, start: str = "10:35", end: str = "11:15",
         teacher: str = "", room: str = "", group: str = "") -> dict:
    return {"date": "2026-09-21", "start": start, "end": end, "subject": subject,
            "teacher": teacher, "room": room, "group": group, "cancelled": False}


def main() -> int:
    print("① 三张轮换卡(同一时段) → 一条国家理科")
    merged = merge_native_science([
        card("国家物理", teacher="Xu", room="A212", group="G1"),
        card("国家化学", teacher="Jiang", room="A208", group="G2"),
        card("国家生物", teacher="Zhang", room="B101", group="G3"),
    ])
    check(len(merged) == 1, f"应只剩 1 条, 实际 {len(merged)}")
    check(merged[0]["subject"] == NATIVE_SCIENCE_LABEL, f"科目应是 {NATIVE_SCIENCE_LABEL}")
    check(merged[0]["group"] == "", "合并后不该带教学组（人人都有）")
    check(merged[0]["room"] == "A212 · A208 · B101", f"房间应合并去重: {merged[0]['room']}")
    check("等3位" in merged[0]["teacher"], f"多位老师压成「X 等N位」: {merged[0]['teacher']}")

    print("② 云端已经合并好的一条 + 本地三张原始卡 → 仍然只有一条（这是用户看到 4 张的那次）")
    merged = merge_native_science([
        card("国家物理", teacher="Xu", room="A212", group="G1"),
        card("国家化学", teacher="Jiang", room="A208", group="G2"),
        card("国家生物", teacher="Zhang", room="B101", group="G3"),
        card(NATIVE_SCIENCE_LABEL, teacher="理科组", room="A212"),
    ])
    check(len(merged) == 1, f"应只剩 1 条, 实际 {len(merged)} → " +
          str([c["subject"] for c in merged]))
    check(merged[0]["subject"] == NATIVE_SCIENCE_LABEL, "留下的应是国家理科")

    print("③ 只有云端那一条时原样保留")
    merged = merge_native_science([card(NATIVE_SCIENCE_LABEL, teacher="理科组", room="A212")])
    check(len(merged) == 1 and merged[0]["teacher"] == "理科组", "一条时不动它")

    print("④ 不同时段的国家理科各留一条（不跨时段合并）")
    merged = merge_native_science([
        card("国家物理", start="10:35", end="11:15"),
        card("国家化学", start="10:35", end="11:15"),
        card("国家物理", start="13:00", end="13:40"),
    ])
    check(len(merged) == 2, f"应 2 条（两个时段）, 实际 {len(merged)}")
    check([c["start"] for c in merged] == ["10:35", "13:00"], "时段各留一条")

    print("⑤ 非国家理科的卡片完全不碰（内容一致，只按时间排好序）")
    plain = [card("数学", teacher="Li", room="C1"), card("语文", start="09:00", end="09:40")]
    merged = merge_native_science(list(plain))
    check(len(merged) == 2, f"普通课数量不变, 实际 {len(merged)}")
    check(sorted(merged, key=lambda c: c["subject"]) == sorted(plain, key=lambda c: c["subject"]),
          "普通课内容一模一样")
    check([c["start"] for c in merged] == ["09:00", "10:35"], "输出按时间排序（稳定、可复现）")

    print("⑥ 国家政治/历史/地理**不是**国家理科（别误合并）")
    merged = merge_native_science([
        card("国家政治", teacher="A"), card("国家历史", teacher="B"), card("国家地理", teacher="C"),
    ])
    check(len(merged) == 3, f"三门独立科目应各留一条, 实际 {len(merged)}")

    print("⑦ 只有一条时不会被二次压缩（云端那条合并串原样保留）")
    merged = merge_native_science([
        card(NATIVE_SCIENCE_LABEL, teacher="Xu 等3位", room="A212 · A208 · B101"),
    ])
    check(merged[0]["teacher"] == "Xu 等3位", "只有一条时原样保留压缩串")

    print("⑧ 班级课表的卡片（带 groups/classes）：合并后不留单个组的痕迹")
    rows = [
        {**card("国家物理", teacher="Xu", room="A212"), "groups": "G1", "classes": ["G10A"]},
        {**card("国家化学", teacher="Jiang", room="A208"), "groups": "G2", "classes": ["G10B"]},
        {**card("国家生物", teacher="Zhang", room="B101"), "groups": "G3", "classes": ["G10A"]},
    ]
    merged = merge_native_science(rows)
    check(len(merged) == 1, f"班级课表也该合成 1 条, 实际 {len(merged)}")
    check(merged[0]["groups"] == "", "合并后不显示单个组号")
    check(merged[0]["classes"] == ["G10A", "G10B"], f"班级去重合并: {merged[0]['classes']}")

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())