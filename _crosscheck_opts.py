# 交叉验证 step2: 列出两个班级的可选课选项, 与图片课程对照
import sys
from datetime import date

sys.path.insert(0, r"D:\HPHL-dev")
from hellopinghe.config import Config
from hellopinghe.app.services import EdupageService

for label, user, pwd in (
    ("刘一诺 Class3", "liuyinuo25@shphschool.com", "dvfnrdr23r"),
    ("华子谦 Class9", "huaziqian25@shphschool.com", "liqian1982"),
):
    print(f"\n{'=' * 30} {label} 可选课列表 {'=' * 30}")
    cfg = Config()
    cfg.edupage_username = user
    cfg.edupage_subdomain = "pingheschool"
    svc = EdupageService(cfg)
    svc.login(user, pwd, "pingheschool")
    opts = svc.subject_options()
    for fam_entry in opts:
        fam = fam_entry["subject"]
        for g in fam_entry["groups"]:
            times = " / ".join(f"{t['day']}{t['start']}" for t in g["times"][:3])
            rooms = ",".join(g["rooms"][:2])
            print(f"  {fam:42} 组:{g['group'] or '(无)':6} 老师:{g['teacher']:20} 教室:{rooms:16} {times}")
