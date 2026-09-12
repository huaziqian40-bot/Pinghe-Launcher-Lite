# -*- coding: utf-8 -*-
"""个人课表过滤自检: 共用课表(全班可见) → 只留"我选的课".

    python scripts/test_personal_filter.py [data_dir]

真实数据验证: 读共用 data/School 的 edupage 段(另一个程序同步回来的全班课卡),
用 EdupageService.card_selected 过滤, 打印每天剩余节数。
默认数据目录 D:\\phl-dev\\joint-testenv\\data(可用参数覆盖); 只读, 不写任何文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe import paths, sharedschool  # noqa: E402
from hellopinghe.app.services import EdupageService  # noqa: E402


class _Stub:
    """只提供 card_selected 需要的东西(选课列表), 不碰任何服务。"""

    def __init__(self, selected: list[dict]) -> None:
        self.cfg = type("Cfg", (), {"selected_lessons": selected})()


def main() -> int:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"D:\phl-dev\joint-testenv\data")
    doc = sharedschool.read(data_dir)
    section = doc.get("edupage") or {}
    lessons = section.get("lessons") or []
    print(f"共用 School: {data_dir / 'School'}")
    print(f"edupage 周: {section.get('week_start')!r}  全班课卡: {len(lessons)} 节")

    settings = data_dir / "settings.yaml"
    if not settings.exists():
        print(f"没有 {settings}, 无法读到共用选课")
        return 1
    import yaml

    selected = (yaml.safe_load(settings.read_text(encoding="utf-8")) or {}).get("lessons") or []
    print(f"共用选课(settings.yaml lessons): {len(selected)} 条")

    service = _Stub(selected)
    by_day: dict[str, list[dict]] = {}
    for card in lessons:
        by_day.setdefault(str(card.get("date")), []).append(card)

    kept_total = 0
    for day in sorted(by_day):
        kept = [c for c in by_day[day] if EdupageService.card_selected(service, c)]
        kept_total += len(kept)
        sample = ", ".join(f"{c.get('start')} {c.get('subject')}[{c.get('group') or '-'}]" for c in kept[:3])
        print(f"  {day}: {len(by_day[day])} → {len(kept)} 节   {sample}")

    if not lessons:
        print("共用文件里还没有课表, 跳过")
        return 0
    if selected and kept_total == 0:
        raise AssertionError("有选课却一节课都没留下, 过滤规则不对")
    # 共用文件里可能是"整班课表"(过滤后会变少), 也可能是对方写好的"个人课表"
    # (本来就全是自己选的, 过滤后不会变少) —— 两种情况都要能通过。
    dropped = len(lessons) - kept_total
    if dropped == 0:
        print("这份数据里的课卡本来就都命中选课(对方写的就是过滤后的课表), 不需要变少")
    else:
        print(f"过滤掉了 {dropped} 节不属于自己选的课")
    print(f"个人课表过滤自检 OK: {len(lessons)} → {kept_total} 节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
