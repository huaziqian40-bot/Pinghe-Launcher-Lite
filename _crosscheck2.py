# 交叉验证 v2: 构建两个人的选课 → personal() → 对比图片课表
import sys
from datetime import date, timedelta

sys.path.insert(0, r"D:\HPHL-dev")
from hellopinghe.config import Config
from hellopinghe.app.services import EdupageService

MON = date(2026, 9, 7)
DAYS = [MON + timedelta(days=i) for i in range(5)]
NAMES = ["周一", "周二", "周三", "周四", "周五"]

# 华子谦的选课(按图片课程 + 上面 options 列表的对应关系)
HUA_SEL = [
    {"subject": "Environmental Systems and Societies(G3)", "teacher": "Yan Xu", "group": "P"},
    {"subject": "Physics HL1", "teacher": "Jing Jiang", "group": "A"},
    {"subject": "Language & Literature", "teacher": "Tianwei Li", "group": "D"},
    {"subject": "Mathematics analysis and approaches", "teacher": "Xiaoyue Yan", "group": "D"},
    {"subject": "English B", "teacher": "Xiaotian Xu", "group": "L"},
    {"subject": "Computer Science HL", "teacher": "Anqi Wang", "group": "P"},
    {"subject": "Computer Science HL", "teacher": "Jia Sun", "group": "P"},
    {"subject": "TOK", "teacher": "Jiabin Xu", "group": "F"},
    {"subject": "Native Physics物理", "teacher": "Duanyan Xiang", "group": "G1"},
]

# 刘一诺的选课(按图片课程 + options)
NOR_SEL = [
    {"subject": "Environmental Systems and Societies(G3)", "teacher": "Yan Xu", "group": "P"},
    {"subject": "Literature", "teacher": "Rui Jin", "group": "H"},
    {"subject": "Psychology", "teacher": "Yinuo Su", "group": "F"},
    {"subject": "Mathematics applications and interpretation", "teacher": "Weiwei Xuan", "group": "Q"},
    {"subject": "Biology", "teacher": "Yingying Chen", "group": "J"},
    {"subject": "English A Literature", "teacher": "Joshua Kingdom Knight", "group": "A"},
    {"subject": "TOK", "teacher": "Jiabin Xu", "group": "F"},
]


def run(label, user, pwd, selection):
    print(f"\n{'=' * 30} {label} 个人课表 {'=' * 30}")
    cfg = Config()
    cfg.edupage_username = user
    cfg.edupage_subdomain = "pingheschool"
    cfg.selected_lessons = selection
    svc = EdupageService(cfg)
    svc.login(user, pwd, "pingheschool")
    for day, name in zip(DAYS, NAMES):
        rows = svc.personal(day)
        print(f"--- {name} ({len(rows)} 节) ---")
        for r in sorted(rows, key=lambda x: x["start"]):
            t = r.get("teacher") or "—"
            rm = r.get("room") or "—"
            g = f" [{r['group']}]" if r.get("group") else ""
            print(f"  {r['start']}-{r['end']}  {r['subject']}{g}  {t}  @{rm}")


if __name__ == "__main__":
    who = sys.argv[1] if len(sys.argv) > 1 else "both"
    if who in ("norine", "both"):
        run("刘一诺 Class3", "liuyinuo25@shphschool.com", "dvfnrdr23r", NOR_SEL)
    if who in ("hua", "both"):
        run("华子谦 Class9", "huaziqian25@shphschool.com", "liqian1982", HUA_SEL)
