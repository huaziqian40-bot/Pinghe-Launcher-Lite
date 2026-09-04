"""第二轮验证: 修复后的 classes 解析 + Edupage 超时补丁重试."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hellopinghe.config import CONFIG_DIR, Config
from hellopinghe.managebac.client import ManageBacClient
from hellopinghe.managebac.parse import extract_classes, extract_overall_grade
from hellopinghe import storage

DATA = Path(__file__).resolve().parents[1] / "data"

cfg = Config.load()
host = cfg.managebac_base_url.split("//")[-1]

# ============================================================ ManageBac
print("== ManageBac: 用保存的会话重新解析 classes ==")
client = ManageBacClient(cfg.managebac_base_url)
sess_file = CONFIG_DIR / f"session_{host}.json"
for name, value in json.loads(sess_file.read_text(encoding="utf-8"))["cookies"].items():
    client.session.cookies.set(name, value)

resp = client._get("/student/classes/my")
(DATA / "classes.html").write_bytes(resp.content)
classes = extract_classes(resp.text)
for cid, name in classes.items():
    print(f"  {cid:>9}  {name}")
print(f"共 {len(classes)} 门")
storage.save_classes(storage.connect(), host, classes)

print("\n== 各科总评 ==")
for cid, name in classes.items():
    try:
        resp = client._get(f"/student/classes/{cid}/units")
        print(f"  {name[:34]:<36} {extract_overall_grade(resp.text) or '(未出分)'}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {name[:34]:<36} (失败: {exc})")
    time.sleep(0.4)

# ============================================================ Edupage
print("\n== Edupage(超时补丁 + pingheschool)==")
from edupage_api import Edupage  # noqa: E402

from hellopinghe import edupage as ep  # noqa: E402


def patch_timeout(session, seconds: float = 40.0) -> None:
    """edupage-api 内部请求超时 5s, 对国内访问太短, 整体加到 40s."""
    original = session.request

    def request(method, url, **kwargs):  # noqa: ANN001, ANN003
        timeout = kwargs.get("timeout")
        if not timeout or timeout < seconds:
            kwargs["timeout"] = seconds
        return original(method, url, **kwargs)

    session.request = request


user, pwd = os.environ["EP_USER"], os.environ["EP_PASS"]
account, used = None, None
attempts = [
    ("pingheschool+完整邮箱", "pingheschool", user),
    ("pingheschool+用户名", "pingheschool", user.split("@")[0]),
    ("auto推断", "", user),
]
for label, sub, ident in attempts:
    try:
        ed = Edupage()
        patch_timeout(ed.session)
        res = (
            ed.login(ident, pwd, sub)
            if sub
            else ed.login_auto(ident, pwd)
        )
        if hasattr(res, "finish"):
            print(f"  [{label}] 触发两步验证", flush=True)
            continue
        account, used = ed, label
        break
    except Exception as exc:  # noqa: BLE001
        print(f"  [{label}] 失败: {type(exc).__module__}.{type(exc).__name__}: "
              f"{str(exc)[:180]!r}", flush=True)

if account is None:
    print("✗ Edupage 仍然失败")
else:
    subdomain = str(getattr(account, "subdomain", "") or "pingheschool")
    print(f"✓ 登录成功 (方式: {used}, 子域名: {subdomain})")
    for offset in range(3):
        day = date.today() + timedelta(days=offset)
        try:
            lessons = ep.fetch_timetable(account, day)
        except Exception as exc:  # noqa: BLE001
            print(f"  == {day} 课表失败: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        print(f"  == {day} ({len(lessons)} 节)")
        for l in lessons:
            mark = " [取消]" if l.cancelled else ""
            print(f"    {l.start}-{l.end}  {l.subject:<16} {l.teacher:<14} {l.classroom}{mark}")
    cfg = Config.load()
    cfg.edupage_username = user
    cfg.edupage_subdomain = subdomain
    cfg.save()
    print("✓ Edupage 配置已保存(密码未保存)")
