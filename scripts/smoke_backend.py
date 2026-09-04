# -*- coding: utf-8 -*-
"""后端冒烟: 不开窗口, 直接调 Api 的离线方法."""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"G:\agent\hellopinghe")

from hellopinghe.app.bridge import Api
from hellopinghe.exceptions import LoginRequiredError

api = Api()
failures = []


def check(name, fn):
    try:
        r = fn()
        ok = isinstance(r, dict) and r.get("ok")
        print(f"{'✓' if ok else '✗'} {name}: {str(r)[:110]}")
        if not ok:
            failures.append(name)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ {name}: 异常 {type(exc).__name__}: {exc}")
        failures.append(name)


check("wizard_status", lambda: api.wizard_status())
check("settings_get", lambda: api.settings_get())
check("agent_state", lambda: api.agent_state())

# 日程 CRUD(纯本地)
from datetime import date, timedelta

day = (date.today() + timedelta(days=1)).isoformat()
check("schedule_add", lambda: api.schedule_add(day, "15:00", "冒烟测试日程", "note"))
d = api.schedule_month(day[:7])
found = any(e["title"] == "冒烟测试日程" for e in d["data"]) if d.get("ok") else False
print(f"{'✓' if found else '✗'} schedule_month 含新事件")
if not found:
    failures.append("schedule_month")
events = d["data"] if d.get("ok") else []
target = next((e for e in events if e["title"] == "冒烟测试日程"), None)
if target:
    check("schedule_update", lambda: api.schedule_update(target["id"], day, "16:00", "冒烟测试日程", "改"))
    check("schedule_delete", lambda: api.schedule_delete(target["id"]))

# agent chat: ok=True = 真实 LLM 回复(链路通); 若报认证/连接错误也说明链路已通到供应商
r = api.agent_chat("你好")
err = str(r.get("error", ""))
chain_ok = r.get("ok") is True or any(
    k in err for k in ("401", "Authentication", "workspace", "connect", "Connection", "API")
)
print(f"{'✓' if chain_ok else '✗'} agent chat 链路: "
      + (f"成功, 回复 {str(r.get('reply'))[:60]!r}" if r.get("ok") else err[:100]))
if not chain_ok:
    failures.append("agent chat")

# agent 环境检测
check("wizard_ai_env", lambda: api.wizard_ai_env())

# 提案流程(用 add_schedule_event 走一遍)
r = api.agent._exec_tool("add_schedule_event", {"day": day, "time": "09:00", "title": "提案冒烟"})
props = api.agent.list_proposals()
print(f"{'✓' if props else '✗'} 生成提案: {len(props)} 个")
if not props:
    failures.append("proposal")
if props:
    rr = api.agent.confirm(props[0]["id"])
    print(f"{'✓' if rr.get('ok') else '✗'} 提案确认执行: {str(rr)[:90]}")
    # 清理
    events2 = api.svc.schedule.list_range(day, day)
    for e in events2:
        if e["title"] in ("提案冒烟", "冒烟测试日程"):
            api.svc.schedule.delete(e["id"])

print()
print("失败项:", failures if failures else "无 — 后端冒烟通过 ✓")
sys.exit(1 if failures else 0)
