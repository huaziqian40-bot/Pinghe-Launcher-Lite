"""静态检查：`ui/app.js` 里 `$("#xxx")` 引用的 id 是否都真的存在于 `ui/index.html`。

`$("#不存在的id")` 在浏览器里只会返回 null，不会报错 —— 但后续
`.textContent = ...` 就炸了，而且只有用户点到那一步才会暴露。
纯静态扫描能在打开窗口之前就抓到（历史事故：`#phix-status` vs `#phix-status-line`）。

跑法：`python scripts/check_ui_ids.py`
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")

ids = set(re.findall(r'id="([^"]+)"', html))
# JS 里用模板字符串动态拼出来的 id（`<button id="phix-cf-local">…`）也算"存在"
ids |= set(re.findall(r'id="([A-Za-z0-9_\-]+)"', js))
refs = set(re.findall(r'\$\("#([A-Za-z0-9_\-]+)"\)', js))
# 也统计 on("#xxx", ...) / $$("#xxx ...") 这类
refs |= set(re.findall(r'(?:on|qs|byId)\("#([A-Za-z0-9_\-]+)"', js))

# 动态拼接的 id（`$("#" + id)`）不在静态扫描范围内
dynamic = set(re.findall(r'\["([a-z\-]+-modal)"\]', js))

missing = sorted(r for r in refs if r not in ids and r not in dynamic)
unused = sorted(i for i in ids if i not in refs)

print(f"HTML id 共 {len(ids)} 个，JS 引用 {len(refs)} 个")
if unused:
    print(f"（提示）HTML 里有、JS 没直接引用的 id {len(unused)} 个，正常：{', '.join(unused[:12])}"
          + (" …" if len(unused) > 12 else ""))
if missing:
    print(f"✗ JS 引用了 {len(missing)} 个 HTML 里不存在的 id：")
    for m in missing:
        print("   · #" + m)
    sys.exit(1)
print("✓ JS 引用的 id 全部存在")
