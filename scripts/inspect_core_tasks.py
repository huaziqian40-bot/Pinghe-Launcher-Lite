"""检查 core_tasks 页面的真实 HTML 结构."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

for fid in (11517686, 11447455):
    html = open(rf"G:\agent\hellopinghe\data\core_tasks_{fid}.html", encoding="utf-8", errors="replace").read()
    print("=" * 30, fid, "len:", len(html))
    for key in ["fusion-card-item", "short-assignment", "date-badge", "due-date",
                "label-pending", "label-submitted", "label-late", "label-score",
                "not-submitted", "dropbox", "core_task"]:
        print(f"  {key:<18} x{html.count(key)}")

    links = re.findall(r'href="(/student/classes/\d+/core_tasks/\d+)"[^>]*>([^<]{2,100})<', html)
    print("  task links:")
    for href, text in links[:12]:
        print("   ", href, "|", text.strip())

    for m in list(re.finditer(r'<div class="(fusion-card-item[^"]*)"', html))[:5]:
        print("  card class:", m.group(1))

    i = html.find("date-badge")
    if i > 0:
        print("  date-badge context:", html[i - 120 : i + 320].replace("\n", " ")[:440])
    print()
