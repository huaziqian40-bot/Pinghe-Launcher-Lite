# -*- coding: utf-8 -*-
"""诊断: 用 PLL 自己的后端代码复现四条问题(在数据副本上跑, 不动真实测试环境).

    set PHLL_DATA_DIR=<副本目录>
    python -X utf8 scripts/diagnose.py

依次调用界面实际用的那些桥接方法, 打印结果摘要与错误, 便于定位:
1. 个人课表(选课之后)  timetable_week(0)
2. 首页 DDL            home_data()
3. 我的课程 DDL/总评   courses_data()
4. 账号是否被读到       settings_get() / connect_*
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe import paths, storage  # noqa: E402
from hellopinghe.app.bridge import Api  # noqa: E402
from hellopinghe.config import Config  # noqa: E402


def brief(value, limit: int = 400) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit] + ("…" if len(text) > limit else "")


def main() -> int:
    print(f"数据目录: {paths.data_dir()}")
    cfg = Config.load()
    print(f"账号: edupage={cfg.edupage_username!r} managebac={cfg.managebac_email!r} mail={cfg.mail_email!r} xinlv={cfg.xinlv_username!r}")
    print(f"选课: {len(cfg.selected_lessons)} 条 -> {json.dumps(cfg.selected_lessons, ensure_ascii=False)[:200]}")

    api = Api()

    def call(name: str, fn) -> None:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            print(f"\n### {name}  异常 {type(exc).__name__}: {exc}")
            return
        ok = result.get("ok") if isinstance(result, dict) else None
        print(f"\n### {name}  ok={ok}")
        data = result.get("data") if isinstance(result, dict) else result
        if isinstance(result, dict) and result.get("error"):
            print(f"    error: {brief(result['error'])}")
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, list):
                    print(f"    {key}: list[{len(value)}] {brief(value[:2], 200)}")
                else:
                    print(f"    {key}: {brief(value, 200)}")
        elif data is not None:
            print(f"    {brief(data)}")

    print("\n================ 连接三件套 ================")
    call("connect_edupage", api.connect_edupage)
    call("connect_managebac", api.connect_managebac)
    call("connect_mail", api.connect_mail)

    print("\n================ 我的课表(本周) ================")
    call("timetable_week(0)", lambda: api.timetable_week(0))

    print("\n================ 首页 ================")
    call("home_data", api.home_data)

    print("\n================ 我的课程 ================")
    call("courses_data", api.courses_data)

    print("\n================ 设置(账号/选课) ================")
    call("settings_get", api.settings_get)

    print("\n================ 选课向导数据 ================")
    call("wizard_subject_options", api.wizard_subject_options)

    print("\n================ 底层: 作业缓存与共享数据 ================")
    conn = None
    host = cfg.managebac_base_url.split("//")[-1]
    cached = storage.load_tasks_cache(conn, host)
    print(f"本机 MB 作业缓存: {len(cached)} 条; 前两条 {brief(cached[:2], 300)}")
    print(f"任务缓存时间: {storage.tasks_cache_age(conn, host)}")
    api2 = api
    try:
        svc_tasks = api2.svc.courses.all_tasks()
        print(f"all_tasks(): {len(svc_tasks)} 条")
    except Exception as exc:  # noqa: BLE001
        print(f"all_tasks() 异常: {type(exc).__name__}: {exc}")
    try:
        classes = api2.svc.courses.classes()
        print(f"classes(): {len(classes)} 个 -> {brief(classes, 200)}")
    except Exception as exc:  # noqa: BLE001
        print(f"classes() 异常: {type(exc).__name__}: {exc}")
    try:
        grades = api2.svc.courses.grades()
        graded = {k: v for k, v in (grades or {}).items() if v}
        print(f"grades(): {len(grades or {})} 个, 其中有分数的 {len(graded)} 个 -> {brief(graded, 200)}")
    except Exception as exc:  # noqa: BLE001
        print(f"grades() 异常: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
