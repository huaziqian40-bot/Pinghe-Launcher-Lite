# -*- coding: utf-8 -*-
"""跨程序 Schedule 兼容自检：Lite 写 → PHL 读，PHL 写 → Lite 读。

    python -X utf8 scripts/test_schedule_compat.py

用 PH Launcher 仓库里的 ``electron/shared-schedule.cjs``（只读、不修改）做真实
解析/写入，验证两边的共享日程格式确实相同：Lite 写的条目 PHL 能直接当成自己的
日历事件（缺省 end 按 60 分钟补），PHL 写的条目 Lite 能读；lastId 高水位与未知字段
两边都保留。需要本机有 node 与 PHL 仓库；缺失时明确 skip。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PHL_SCRIPT = Path(r"D:\phl-dev\PH-Launcher\electron\shared-schedule.cjs")
NODE = shutil.which("node") or shutil.which("node.exe")

READ_JS = r"""
const m = require(process.argv[1]);
const { doc } = m.readSchedule(process.argv[2]);
const launcher = m.toLauncherEvents(doc, { durationMinutes: 60 });
console.log(JSON.stringify({ doc, launcher }));
"""

WRITE_JS = r"""
const m = require(process.argv[1]);
const result = m.upsertEvents(process.argv[2], [
  { day: '2026-11-11', time: '09:00', title: 'PHL 写的日程', note: '来自 PHL' },
], { app: 'PH Launcher' });
console.log(JSON.stringify({ doc: result.doc, added: result.added }));
"""


def node(script: str, schedule: Path) -> dict:
    result = subprocess.run([NODE, "-e", script, str(PHL_SCRIPT), str(schedule)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    if result.returncode != 0:
        raise AssertionError(result.stderr[-800:])
    return json.loads(result.stdout)


@unittest.skipUnless(PHL_SCRIPT.is_file() and NODE, "需要 node 与 PHL 仓库的 shared-schedule.cjs")
class ScheduleCompatTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {"PHLL_DATA_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        from hellopinghe import filestore as fs

        fs.ensure_root()
        self.schedule = self.root / "Schedule"

    def test_lite_write_phl_read(self):
        from hellopinghe import storage

        event_id = storage.events_add(None, "2026-11-10", "14:30", "Lite 写的日程", "备注")
        data = node(READ_JS, self.schedule)
        doc = data["doc"]
        self.assertEqual(len(doc["events"]), 1)
        event = doc["events"][0]
        self.assertEqual(event["id"], event_id)
        self.assertEqual(event["day"], "2026-11-10")
        self.assertEqual(event["time"], "14:30")
        self.assertEqual(event["title"], "Lite 写的日程")
        self.assertEqual(event["note"], "备注")
        self.assertGreaterEqual(int(doc.get("lastId") or 0), event_id)
        # PHL 的日历模型只需要 day/time/title/note；end 由它自己按 60 分钟补默认。
        launcher = data["launcher"]
        self.assertEqual(len(launcher), 1)
        self.assertEqual(launcher[0]["date"], "2026-11-10")
        self.assertEqual(launcher[0]["start"], "14:30")
        self.assertEqual(launcher[0]["end"], "15:30")
        self.assertEqual(launcher[0]["title"], "Lite 写的日程")

    def test_phl_write_lite_read(self):
        from hellopinghe import storage

        data = node(WRITE_JS, self.schedule)
        self.assertEqual(data["added"], 1)
        events = storage.events_list(None, "2026-11-01", "2026-12-01")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["day"], "2026-11-11")
        self.assertEqual(events[0]["time"], "09:00")
        self.assertEqual(events[0]["title"], "PHL 写的日程")
        self.assertEqual(events[0]["note"], "来自 PHL")
        # Lite 新增时不能复用 PHL 已经分配过的 id（lastId 高水位）。
        new_id = storage.events_add(None, "2026-11-12", "10:00", "Lite 再写一条", "")
        self.assertEqual(new_id, events[0]["id"] + 1)

    def test_unknown_fields_preserved_both_sides(self):
        from hellopinghe import storage

        storage.events_add(None, "2026-11-10", "08:00", "基础条目", "")
        doc = json.loads(self.schedule.read_text(encoding="utf-8"))
        doc["x-extra-top"] = {"keep": True}
        doc["events"][0]["x-extra-event"] = "keep"
        self.schedule.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        storage.events_add(None, "2026-11-10", "09:00", "第二条", "")
        after = json.loads(self.schedule.read_text(encoding="utf-8"))
        self.assertEqual(after["x-extra-top"], {"keep": True}, "Lite 必须保留顶层未知字段")
        self.assertEqual(after["events"][0]["x-extra-event"], "keep", "Lite 必须保留事件未知字段")
        # PHL 读同一份文件时也不会因为未知字段而报错；写回后 Lite 仍能读到两条。
        data = node(READ_JS, self.schedule)
        self.assertEqual(len(data["doc"]["events"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
