# -*- coding: utf-8 -*-
"""互通测试 · Pinghe Launcher Lite 侧.

与 PH Launcher 侧的 _probe/interop-node.cjs 交替运行, 用两个程序的**真实代码**
验证它们读写同一份 data/(Schedule / agent / School / Timetable / settings.yaml)。

    set PHLL_DATA_DIR=<dataDir>
    python -X utf8 scripts/interop_check.py check   # 读 PHL 写的东西
    python -X utf8 scripts/interop_check.py write   # 写 PLL 的东西(含未知字段保留测试)

数据目录由 PHLL_DATA_DIR 指定(hellopinghe/paths.py 支持), 不会碰到真实数据目录。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe import filestore as fs, paths, shareddata, sharedschool, storage  # noqa: E402
from hellopinghe.app.agent import AgentEngine  # noqa: E402
from hellopinghe.config import Config  # noqa: E402


class _StubServices:
    """AgentEngine 的会话读写不需要 services, 传个占位。"""


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    root = paths.data_dir()
    report: dict = {"mode": mode, "dir": str(root), "checks": []}

    def check(name: str, ok: bool, detail="") -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})

    if mode == "check":
        events = storage.events_list(None, "2026-09-01", "2026-09-30")
        titles = [e["title"] for e in events]
        check("PHL 写的日程事件 PLL 读得到", "PHL→PLL 日程互通" in titles, " | ".join(titles))
        raw = fs.load_json(root / fs.SCHEDULE, {}) or {}
        check("日程 kind 正确", raw.get("kind") == "pinghe-schedule", raw.get("kind"))
        check("日程未知字段仍在(由 PLL 读)", raw.get("future_field_schedule") == "keep-me", raw.get("future_field_schedule"))

        engine = AgentEngine(Config.load(), _StubServices())
        listed = [s["id"] for s in engine.list_sessions()]
        check("PHL 写的会话 PLL 列得出来", "phl-interop-1" in listed, " | ".join(listed[:5]))
        if "phl-interop-1" in listed:
            loaded = engine.load_session("phl-interop-1")
            check("PHL 会话内容能读成对话", len(loaded["history"]) == 2, json.dumps(loaded["history"], ensure_ascii=False)[:120])

        doc = sharedschool.read()
        check("PHL 写的邮箱摘要 PLL 读得到", (doc.get("mail") or {}).get("unread") == 7, (doc.get("mail") or {}).get("unread"))
        days = sharedschool.edupage_days()
        check("PHL 写进 School 的课表 PLL 读得到", "2026-09-15" in days, " | ".join(sorted(days))[:120])
        compat = shareddata.read_timetable_days()
        check("PHL 写进 Timetable 的课表 PLL 也读得到(兜底来源)", "2026-09-20" in compat, " | ".join(sorted(compat))[:120])
        tt = fs.load_json(root / "Timetable", {}) or {}
        check("共用课表未知字段仍在(由 PLL 读)", tt.get("future_field_timetable") == "keep-me", tt.get("future_field_timetable"))

        cfg = Config.load()
        check("共用选课 PLL 读得到", len(cfg.selected_lessons or []) >= 7, len(cfg.selected_lessons or []))
        check("共用账号 PLL 读得到", cfg.edupage_username == "interop-user", cfg.edupage_username)

    elif mode == "write":
        # 写入之前先看清 PHL 留下的状态: 它删过事件、只留下 lastId 高水位。
        before = fs.load_json(root / fs.SCHEDULE, {}) or {}
        before_high = storage._schedule_high_water(before)
        before_max = max([int(e.get("id") or 0) for e in (before.get("events") or [])] or [0])

        event_id = storage.events_add(None, "2026-09-21", "09:00", "PLL→PHL 日程互通", "来自 PLL")
        report["event_id"] = event_id
        check("删除过的 id 没有被复用(新 id 大于 PHL 的高水位)",
              int(event_id) > before_high,
              f"新 id={event_id}，PHL 高水位 lastId={before_high}，现存最大 id={before_max}")

        engine = AgentEngine(Config.load(), _StubServices())
        engine.history = [{"role": "user", "content": "从 PLL 写的"}, {"role": "assistant", "content": "好的"}]
        engine.session_id = "pll-interop-1"
        saved = engine.save_session()
        report["session"] = saved
        check("PLL 会话写出来了", saved == "pll-interop-1", saved)

        sharedschool.update({"mail": sharedschool.mail_section(3, [
            {"uid": "9100", "from": "PLL <pll@example.com>", "subject": "PLL 摘要", "date": "09-21 09:00", "unread": True},
        ])})
        shareddata.write_timetable_days({"2026-09-21": [
            {"start": "09:00", "end": "09:40", "subject": "PLL 互通测试课", "teacher": "T", "room": "R",
             "group": "", "cancelled": False, "curriculum": ""},
        ]})

        # PLL 是按天算课表的: 写一天不能把对方写好的整周课表替换掉
        # (PHL 写的这周从 2026-09-14 开始, 我们写同一周里的 09-16)。
        sharedschool.update({"edupage": sharedschool.edupage_section({"2026-09-16": [
            {"date": "2026-09-16", "start": "09:00", "end": "09:40", "subject": "PLL 按天写的课", "group": ""},
        ]})})
        after = sharedschool.read().get("edupage") or {}
        kept = [r for r in (after.get("lessons") or []) if r.get("date") == "2026-09-15"]
        check("按天写课表不会抹掉对方的整周课表", bool(kept),
              f"week_start={after.get('week_start')} lessons={len(after.get('lessons') or [])}")

        # 触发一次整份 settings.yaml 重写: 验证 PHL 的账号/选课/未知段落都还在。
        cfg = Config.load()
        cfg.save()
        check("PLL 保存设置后仍能读到自己的选课", len(cfg.selected_lessons or []) >= 7, len(cfg.selected_lessons or []))

    else:
        print(json.dumps({"error": f"unknown mode {mode}"}))
        return 2

    failed = [c for c in report["checks"] if not c["ok"]]
    report["failed"] = len(failed)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
