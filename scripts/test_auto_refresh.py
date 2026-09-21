# -*- coding: utf-8 -*-
"""自动刷新自检：界面上不该再有"刷新/同步"按钮，后台必须有自动刷新的那一套。

用户 2026-09-21：「右上角那个刷新按钮和窗口控件重叠了，直接删掉好了，
软件自动刷新，自动更新同步数据，逻辑和 phl 一样」。

    python -X utf8 scripts/test_auto_refresh.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

HTML = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "ui" / "styles.css").read_text(encoding="utf-8")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("  OK", message)


def main() -> int:
    print("① 手动刷新/同步入口都该没了")
    for element_id, label in (
        ("btn-refresh", "顶栏的「↻ 刷新」"),
        ("co-refresh", "课程页的「↻ 同步 ManageBac」"),
        ("xl-sync-now", "心履页的「↻ 同步」"),
    ):
        check(f'id="{element_id}"' not in HTML, f"{label} 已从界面删除")
        check(element_id not in JS, f"{label} 的事件绑定也没了")

    print("② 顶栏不再放任何按钮（右上角是窗口控件的位置）")
    topbar = re.search(r'<div class="topbar[^"]*">(.*?)</div>', HTML, re.S)
    check(bool(topbar), "找得到顶栏")
    check("<button" not in (topbar.group(1) if topbar else ""),
          f"顶栏里没有按钮：{(topbar.group(1)[:60] if topbar else '')!r}")

    print("③ 后台自动刷新那一套必须在")
    for probe, label in (
        (r"const\s+AUTO_EVERY\s*=", "每页各自的刷新节拍 AUTO_EVERY"),
        (r"setInterval\(\(\)\s*=>\s*autoRefreshCurrent\(false\)", "定时轮询"),
        (r'window\.addEventListener\("focus"', "窗口拿到焦点立刻查一次"),
        (r'visibilitychange', "从托盘恢复时查一次"),
        (r"document\.hidden\)\s*return", "收进托盘时不打扰"),
        (r"const AUTO_LOADERS", "温和版加载器（不重置正在填的表单）"),
        (r"xinlv:\s*\(\)\s*=>\s*call\(\"xinlv_status\"\)", "心履走静默同步"),
    ):
        check(re.search(probe, JS) is not None, label)

    print("④ 角落那行不起眼的小字")
    check('id="data-stamp"' in HTML, "侧栏底部有 #data-stamp")
    check("function paintDataStamp" in JS, "paintDataStamp() 会写它")
    check("当前数据：" in JS, "文案是「当前数据：…」")
    check(".data-stamp{" in CSS, "样式里给它留了位置")
    check(re.search(r"show\(view\)[\s\S]{0,400}paintDataStamp\(\);", JS) is not None,
          "切页时也会刷新那行小字")

    print("\n全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
