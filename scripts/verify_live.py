# -*- coding: utf-8 -*-
"""真实验证: Edupage 整周拉取/选课完整性 + ManageBac + 邮箱原始错误诊断."""
import sys
import time
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"G:\agent\hellopinghe")

from hellopinghe.config import Config
from hellopinghe.app.services import Services

cfg = Config.load()
svc = Services(cfg)

print("== 1. Edupage 登录 + 整周课表 ==")
try:
    t0 = time.time()
    monday = svc.edupage.week_monday(date.today())
    plans = svc.edupage.week_plans(monday, days=6)
    days_with = {k: len(v) for k, v in plans.items() if v}
    print(f"✓ 登录成功, 整周拉取耗时 {time.time()-t0:.1f}s")
    print(f"  有课的日期: {days_with}")
    total = sum(days_with.values())
    print(f"  全周课卡总数: {total}")

    print("\n== 2. 选课选项跨周聚合 ==")
    t0 = time.time()
    opts = svc.edupage.subject_options()
    subjects = [o["subject"] for o in opts]
    print(f"✓ 聚合耗时 {time.time()-t0:.1f}s, 科目数 {len(subjects)}")
    hit = [s for s in subjects if "English B SL" in s]
    print(f"  English B SL: {'✓ 在列表里 → ' + str([o for o in opts if o['subject']=='English B SL']) if hit else '✗ 仍然缺失!'}")
    print("  全部科目:", " | ".join(subjects[:40]))
except Exception as exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"✗ Edupage 环节失败: {exc}")

print("\n== 3. ManageBac 会话 ==")
try:
    classes = svc.courses.classes()
    print(f"✓ 会话有效, 课程 {len(classes)} 门")
except Exception as exc:  # noqa: BLE001
    print(f"✗ {type(exc).__name__}: {str(exc)[:160]}")

print("\n== 4. 邮箱原始错误诊断 ==")
from hellopinghe.app.services import secret_get

pw = secret_get(f"mail:{cfg.mail_email}") if cfg.mail_email else None
if not pw:
    print(f"(未保存邮箱凭据: mail_email={cfg.mail_email!r})")
else:
    try:
        n = svc.mail.unread_count()
        print(f"✓ 登录成功, 未读 {n} 封")
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 服务器原始报错: {exc}")
