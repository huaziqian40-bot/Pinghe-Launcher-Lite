"""一次性冒烟测试: 用环境变量中的真实凭据验证 M1 全链路.

密码只经环境变量传入,本脚本与配置文件都不保存密码。

用法:
    $env:MB_URL="..."; $env:MB_EMAIL="..."; $env:MB_PASS="..."
    $env:EP_USER="..."; $env:EP_PASS="..."
    python scripts/smoke_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hellopinghe.managebac.client import ManageBacClient
from hellopinghe.managebac.parse import extract_overall_grade
from hellopinghe.config import CONFIG_DIR, Config
from hellopinghe import storage

DATA = Path(__file__).resolve().parents[1] / "data"
DATA.mkdir(exist_ok=True)


def section(title: str) -> None:
    print(f"\n===== {title} =====")


# ========================================================== ManageBac
client: ManageBacClient | None = None
classes: dict[str, str] = {}
try:
    section("ManageBac 登录(纯 HTTP)")
    client = ManageBacClient(os.environ["MB_URL"])
    client.login(os.environ["MB_EMAIL"], os.environ["MB_PASS"])
    print("✓ 登录成功")

    section("课程列表")
    classes = client.get_classes()
    for cid, name in classes.items():
        print(f"  {cid:>8}  {name}")
    print(f"共 {len(classes)} 门")

    section("DDL(未来 21 天)")
    items = client.get_deadlines(days_ahead=21)
    for it in items:
        due = it.due_at.strftime("%m-%d %H:%M") if it.due_at else "??"
        print(f"  [{it.category:^8}] {due}  {(it.course or '-'):<18} {it.title}  ({it.status or '?'})")
    print(f"共 {len(items)} 条")

    section("各科总评")
    if 0 < len(classes) <= 15:
        for cid, name in classes.items():
            try:
                resp = client._get(f"/student/classes/{cid}/units")
                print(f"  {name:<28} {extract_overall_grade(resp.text) or '(未出分)'}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:<28} (失败: {exc})")
            time.sleep(0.4)
    else:
        print("  (课程数异常,跳过)")

    host = client.base_url.split("//")[-1]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / f"session_{host}.json").write_text(
        json.dumps({"cookies": client.session.cookies.get_dict()}, indent=2), encoding="utf-8"
    )
    cfg = Config.load()
    cfg.managebac_base_url = client.base_url
    cfg.managebac_email = os.environ["MB_EMAIL"]
    cfg.save()
    conn = storage.connect()
    storage.save_classes(conn, host, classes)
    if items:
        storage.save_deadlines(conn, host, items)
    print(f"\n✓ 会话/配置/数据库已保存 (host={host}, 密码未保存)")
except Exception:
    traceback.print_exc()
    print("✗ ManageBac 环节失败")

# 调试 HTML 落地,便于本地修解析(不重新登录)
try:
    if client is not None and client.session.cookies:
        resp = client.session.get(
            client._url("/student/tasks_and_deadlines?view=upcoming"), timeout=30
        )
        (DATA / "tasks_upcoming.html").write_bytes(resp.content)
        if classes:
            first_id = next(iter(classes))
            resp2 = client.session.get(
                client._url(f"/student/classes/{first_id}/units"), timeout=30
            )
            (DATA / "units_sample.html").write_bytes(resp2.content)
        print("调试 HTML 已存至 hellopinghe/data/")
except Exception:  # noqa: BLE001
    pass

# ========================================================== Edupage
section("Edupage 登录")
try:
    from edupage_api import Edupage

    from hellopinghe import edupage as ep

    user, pwd = os.environ["EP_USER"], os.environ["EP_PASS"]
    account, used = None, None

    attempts = [
        ("auto(邮箱推断)", lambda: Edupage().login_auto(user, pwd)),
        ("shphschool", lambda: Edupage().login(user, pwd, "shphschool")),
        ("shph", lambda: Edupage().login(user, pwd, "shph")),
        ("用户名auto", lambda: Edupage().login_auto(user.split("@")[0], pwd)),
    ]
    for label, fn in attempts:
        try:
            res = fn()
            if hasattr(res, "finish"):  # TwoFactorLogin
                print(f"  [{label}] 触发两步验证,试下一个", flush=True)
                continue
            account, used = res, label
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] 失败: {type(exc).__module__}.{type(exc).__name__}: "
                  f"{str(exc)[:200]!r}", flush=True)

    if account is None:
        print("✗ Edupage 全部尝试失败")
    else:
        subdomain = getattr(account, "subdomain", "") or used
        print(f"✓ Edupage 登录成功 (方式: {used}, 子域名: {subdomain})")

        for offset in range(5):
            day = date.today() + timedelta(days=offset)
            try:
                lessons = ep.fetch_timetable(account, day)
            except Exception as exc:  # noqa: BLE001
                print(f"  == {day} 课表获取失败: {type(exc).__name__}: {str(exc)[:100]}")
                continue
            print(f"  == {day} ({len(lessons)} 节)")
            for l in lessons:
                mark = " [取消]" if l.cancelled else ""
                print(f"    {l.start}-{l.end}  {l.subject:<16} {l.teacher:<12} {l.classroom}{mark}")

        cfg = Config.load()
        cfg.edupage_username = user
        cfg.edupage_subdomain = str(subdomain)
        cfg.save()
        print("✓ Edupage 配置已保存(密码未保存)")
except Exception:
    traceback.print_exc()
    print("✗ Edupage 环节失败")
