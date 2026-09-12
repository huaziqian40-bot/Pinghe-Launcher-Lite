# -*- coding: utf-8 -*-
"""最小检查: 只调"我的课程"需要的那条链(避免触发 ManageBac 的登录限流).

    set PHLL_DATA_DIR=<数据目录>
    python -X utf8 scripts/check_courses.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe import paths  # noqa: E402
from hellopinghe.app.bridge import Api  # noqa: E402


def main() -> int:
    print(f"数据目录: {paths.data_dir()}")
    api = Api()
    result = api.courses_data()
    if not result.get("ok"):
        print("courses_data 失败:", result.get("error"))
        return 1
    data = result["data"]
    grades = data.get("grades") or {}
    graded = {k: v for k, v in grades.items() if v}
    print(json.dumps({
        "课程数": len(data.get("classes") or []),
        "未到期作业": len(data.get("tasks_upcoming") or []),
        "已过期作业": len(data.get("tasks_past") or []),
        "有总评的课程": len(graded),
        "总评样例": dict(list(graded.items())[:3]),
        "前两门课": (data.get("classes") or [])[:2],
        "前两条未到期": [(t["title"], t["due_at"], t["class_name"]) for t in (data.get("tasks_upcoming") or [])[:2]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
