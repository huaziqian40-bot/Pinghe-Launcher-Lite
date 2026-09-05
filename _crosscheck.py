# 真实数据交叉验证: 分别用两个账号拉取下周一~周五的个人课表
import sys
from datetime import date, timedelta

sys.path.insert(0, r"D:\HPHL-dev")
from hellopinghe.config import Config
from hellopinghe.app.services import EdupageService

MON = date(2026, 9, 7)   # 下周一(图片是每周循环课表)
DAYS = [MON + timedelta(days=i) for i in range(5)]
NAMES = ["周一", "周二", "周三", "周四", "周五"]


def run(label, user, pwd):
    print(f"\n{'=' * 30} {label} ({user}) {'=' * 30}")
    cfg = Config()
    cfg.edupage_username = user
    cfg.edupage_subdomain = "pingheschool"
    svc = EdupageService(cfg)
    svc.login(user, pwd, "pingheschool")
    cid = svc.my_class_id()
    print(f"班级 id: {cid}")
    for day, name in zip(DAYS, NAMES):
        try:
            rows = svc.personal(day)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: ERROR {exc}")
            continue
        print(f"--- {name} ({day}) {len(rows)} 节 ---")
        for r in sorted(rows, key=lambda x: x["start"]):
            t = r.get("teacher") or "—"
            rm = r.get("room") or "—"
            g = f" [{r['group']}]" if r.get("group") else ""
            print(f"  {r['start']}-{r['end']}  {r['subject']}{g}  {t}  @{rm}"
                  + ("  (取消)" if r.get("cancelled") else ""))
    return svc


if __name__ == "__main__":
    who = sys.argv[1] if len(sys.argv) > 1 else "both"
    if who in ("norine", "both"):
        run("刘一诺 Class3", "liuyinuo25@shphschool.com", "dvfnrdr23r")
    if who in ("hua", "both"):
        run("华子谦 Class9", "huaziqian25@shphschool.com", "liqian1982")
