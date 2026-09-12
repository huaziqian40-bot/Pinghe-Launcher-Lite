# -*- coding: utf-8 -*-
"""诊断 ManageBac 这一条链: 会话有没有失效、课程/作业/总评到底拿没拿到.

    set PHLL_DATA_DIR=<数据目录>
    python -X utf8 scripts/diagnose_mb.py

只做只读请求; 登录只尝试一次(失败不重试, 避免锁号); 不会提交任何作业。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe import filestore as fs, paths  # noqa: E402
from hellopinghe.config import Config  # noqa: E402
from hellopinghe.managebac.client import NO_CLASSES_MARKER, ManageBacClient  # noqa: E402
from hellopinghe.secrets import get as secret_get  # noqa: E402
from hellopinghe import storage  # noqa: E402


def main() -> int:
    cfg = Config.load()
    host = cfg.managebac_base_url.split("//")[-1]
    print(f"数据目录: {paths.data_dir()}")
    print(f"base_url: {cfg.managebac_base_url}  email: {cfg.managebac_email!r}")
    password = secret_get(f"managebac:{cfg.managebac_base_url}")
    print(f"密码来源(settings.yaml 的 accounts.managebac.password): {'有' if password else '没有'}")

    session_file = fs.phll(fs.MANAGEBAC_SUB, f"session_{host}.json")
    print(f"会话文件: {session_file} 存在={session_file.exists()}")

    client = ManageBacClient(cfg.managebac_base_url)
    if session_file.exists():
        import json

        cookies = json.loads(session_file.read_text(encoding="utf-8")).get("cookies") or {}
        print(f"载入会话 cookie: {len(cookies)} 个")
        for key, value in cookies.items():
            client.session.cookies.set(key, value)

    print("\n--- 1) 现有会话是否还有效 ---")
    try:
        logged = client.is_logged_in()
        print(f"is_logged_in() = {logged}")
    except Exception as exc:  # noqa: BLE001
        logged = False
        print(f"is_logged_in() 异常: {type(exc).__name__}: {exc}")

    if not logged:
        print("\n--- 2) 用账号密码登录(只尝试一次) ---")
        if not password:
            print("没有密码, 无法登录 —— 这就是拿不到数据的原因")
            return 1
        try:
            client.login(cfg.managebac_email, password)
            print(f"login() 成功, is_logged_in() = {client.is_logged_in()}")
        except Exception as exc:  # noqa: BLE001
            print(f"login() 失败: {type(exc).__name__}: {exc}")
            return 1

    print("\n--- 3) 课程页结构(看有没有换版) ---")
    resp = client._get("/student/classes/my?page=1")
    text = resp.text
    print(f"HTTP {resp.status_code}  长度 {len(text)}  NO_CLASSES_MARKER 出现={NO_CLASSES_MARKER in text}")
    from hellopinghe.managebac.parse import extract_classes

    classes = extract_classes(text)
    print(f"extract_classes() 解析出 {len(classes)} 个: {list(classes.items())[:5]}")
    for needle in ("no-classes", "classes-list", "class-card", "my-classes", "fusion-card-item"):
        if needle in text:
            print(f"  页面里出现标记: {needle}")

    print("\n--- 4) get_classes() ---")
    print(f"{len(client.get_classes())} 个课程")

    print("\n--- 5) get_overall_grades() ---")
    try:
        grades = client.get_overall_grades()
        graded = {k: v for k, v in (grades or {}).items() if v}
        print(f"{len(grades or {})} 个课程, 其中有分数 {len(graded)} 个: {list(graded.items())[:5]}")
    except Exception as exc:  # noqa: BLE001
        print(f"异常: {type(exc).__name__}: {exc}")

    print("\n--- 6) get_all_tasks() ---")
    try:
        tasks = client.get_all_tasks()
        print(f"{len(tasks)} 条作业; 前两条: {[(t.title, str(t.due_at), t.class_name) for t in tasks[:2]]}")
    except Exception as exc:  # noqa: BLE001
        print(f"异常: {type(exc).__name__}: {exc}")

    print("\n--- 7) 本机任务缓存 ---")
    age = storage.tasks_cache_age(None, host)
    cached = storage.load_tasks_cache(None, host)
    print(f"缓存时间 {age}, 条数 {len(cached)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
