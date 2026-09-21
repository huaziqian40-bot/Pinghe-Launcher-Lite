# -*- coding: utf-8 -*-
"""同步写入验证：直接测出过错的那一步（原子写 data/Schedule 与 data/School），
再跑一轮真同步看 errors 是否清空。

    PHLL_DATA_DIR=<数据目录> python -X utf8 scripts/probe_sync.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from hellopinghe import filestore as fs  # noqa: E402
from hellopinghe import paths  # noqa: E402

data = paths.data_dir()
print(f"数据目录: {data}")
print("=" * 70)

fail = 0
for name in ("Schedule", "School", "Timetable"):
    p = Path(data) / name
    if not p.exists():
        print(f"  {name:<10} 不存在（跳过）")
        continue
    if p.is_dir():
        print(f"  {name:<10} ✗ 是目录 —— 这就是 WinError 5 的根源")
        fail += 1
        continue
    # 复现失败的那一步：_atomic_write 内部就是 临时文件 + os.replace
    before = p.read_bytes()
    try:
        doc = json.loads(before.decode("utf-8"))
        fs.save_json(p, doc)          # 原样写回，内容不变
        after = p.read_bytes()
        same = after == before
        print(f"  {name:<10} ✓ 原子写成功（内容{'未变' if same else '变了!'}，{len(after)} 字节）")
        if not same:
            fail += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  {name:<10} ✗ 写入失败 {type(exc).__name__}: {exc}")
        fail += 1

# 残留的临时分片也会让同步一直报错
leftovers = sorted(Path(data).glob(".tmp-*"))
if leftovers:
    print(f"\n  发现 {len(leftovers)} 个残留临时文件，清理：")
    for f in leftovers:
        try:
            f.unlink()
            print(f"    删除 {f.name}")
        except OSError as exc:
            print(f"    删不掉 {f.name}: {exc}")
            fail += 1
else:
    print("\n  无残留临时文件 ✓")

print("=" * 70)
print("写入层结论:", "全部正常 ✓" if fail == 0 else f"仍有 {fail} 项问题 ✗")

# ---------- 端到端：跑一轮真同步 ----------
print("\n----- 跑一轮真同步 -----")
try:
    from hellopinghe.app.bridge import Api  # noqa: E402
    api = Api()
    res = api.phix_sync(force=True, prefer="merge")
    if not res.get("ok", True) and res.get("error"):
        print("  同步接口返回错误:", json.dumps(res.get("error"), ensure_ascii=False)[:400])
    rep = (res.get("report") or {})
    print(f"  ok={rep.get('ok')}  pulled={len(rep.get('pulled') or [])} "
          f"pushed={len(rep.get('pushed') or [])} conflicts={rep.get('conflicts')}")
    errs = rep.get("errors") or []
    if errs:
        print(f"  ✗ {len(errs)} 项出错:")
        for e in errs:
            print("     -", str(e)[:300])
    else:
        print("  ✓ errors 为空，同步不再报错")
    print("  summary:", res.get("summary"))
except Exception as exc:  # noqa: BLE001
    print(f"  同步调用异常: {type(exc).__name__}: {exc}")
