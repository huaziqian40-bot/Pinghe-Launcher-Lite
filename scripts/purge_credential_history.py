# -*- coding: utf-8 -*-
"""从 git 历史里彻底删除含真实凭据的三个临时脚本。

⚠ 这是**破坏性操作**：会重写已公开的提交历史，之后必须 `git push --force`。
   现有克隆/派生会与远端分叉。所以脚本默认**只演练不执行**。

存在什么问题
------------
`_crosscheck.py` / `_crosscheck2.py` / `_crosscheck_opts.py` 里硬编码了两位同学的
真实邮箱与学校邮箱密码。它们被 6 个提交带进公开仓库（最早 daccc2f），
2026-09-21 的 03e969d 已取消跟踪（当前 HEAD 干净），**但历史提交里仍是明文**。

两条处置建议（都要做）
----------------------
1. **轮换口令**（真正的修复）：那两位同学到「个人中心 → 密码管理」改掉邮箱密码/授权码。
   历史一旦公开过，就必须假设它已经泄露；清理历史不能替代轮换。
2. 清理历史（本脚本）：防止后续被爬虫/克隆继续带走。

用法
----
    python -X utf8 scripts\\purge_credential_history.py            # 演练：只报告会做什么
    python -X utf8 scripts\\purge_credential_history.py --apply    # 真的重写历史（含备份）

执行 --apply 后还需要你手动做两件事（脚本会打印出来）：
    git push --force origin master:main
    并在 GitHub 上让 v1.2.0 的 tag 指向新的提交（或用新 tag 重发 release）
"""
from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys

ROOT = r"D:\phl-lite-dev"
BACKUP_DIR = r"D:\backup"
TARGETS = ["_crosscheck.py", "_crosscheck2.py", "_crosscheck_opts.py"]


def run(cmd: list[str], check=True) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if check and p.returncode != 0:
        print(f"  ✗ 命令失败: {' '.join(cmd)}\n{p.stderr[:500]}")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def survey() -> int:
    """报告：哪些提交里有这些文件、是否可达远程。"""
    print("=" * 72)
    print("受影响的历史提交（这些提交里含明文凭据）")
    print("=" * 72)
    total = 0
    for f in TARGETS:
        _, out = run(["git", "log", "--all", "--oneline", "--", f], check=False)
        lines = [l for l in out.strip().split("\n") if l.strip()]
        total += len(lines)
        print(f"\n{f}  —— {len(lines)} 个提交")
        for l in lines[:10]:
            print(f"    {l}")
        if len(lines) > 10:
            print(f"    …另有 {len(lines) - 10} 个")
    print(f"\n合计（含重复）{total} 条")
    _, tags = run(["git", "tag", "--points-at", "HEAD"], check=False)
    _, cur = run(["git", "log", "--oneline", "-1"], check=False)
    print(f"\n当前 HEAD: {cur.strip()}")
    print("注意：重写历史会改掉 HEAD 之前所有提交的 hash，v1.2.0 这类 tag 需要重新指向。")
    return total


def apply_purge() -> int:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bundle = os.path.join(BACKUP_DIR, f"phl-lite-dev-BEFORE-purge-{stamp}.bundle")

    print("\n[1/4] 备份整个仓库（含全部历史）")
    code, _ = run(["git", "bundle", "create", bundle, "--all"])
    if code != 0 or not os.path.exists(bundle):
        print("  ✗ 备份失败，中止")
        return 1
    print(f"  ✓ {bundle}  ({os.path.getsize(bundle)/1048576:.1f} MB)")

    print("\n[2/4] 记录清理前的 HEAD（万一要回退）")
    _, before = run(["git", "rev-parse", "HEAD"])
    before = before.strip()
    print(f"  清理前 HEAD = {before}")

    print("\n[3/4] 重写全部历史，删除目标文件")
    idx_filter = ("git rm --cached --ignore-unmatch " + " ".join(TARGETS))
    code, out = run(["git", "filter-branch", "--force", "--index-filter", idx_filter,
                     "--prune-empty", "--tag-name-filter", "cat", "--", "--all"])
    if code != 0:
        print("  ✗ 重写失败。用备份恢复到 " + bundle)
        return 1
    print("  ✓ 重写完成")

    print("\n[4/4] 在历史里复查这三个文件是否还在")
    still = 0
    for f in TARGETS:
        _, o = run(["git", "log", "--all", "--oneline", "--", f], check=False)
        n = len([l for l in o.strip().split("\n") if l.strip()])
        still += n
        print(f"  {f}: 仍出现在 {n} 个提交里" + ("  ✓" if n == 0 else "  ✗"))
    _, after = run(["git", "rev-parse", "HEAD"])
    print(f"\n  清理后 HEAD = {after.strip()}（原 {before[:8]}）")

    print("\n" + "=" * 72)
    print("接下来需要你手动执行（脚本不代做，因为会影响公开仓库）：")
    print("  1) git push --force origin master:main")
    print("  2) GitHub 上把 v1.2.0 之类的 tag 重新指向新提交，或删掉 release 重发")
    print("  3) 让那两位同学轮换邮箱密码/授权码 —— 这一步比清理历史更重要")
    print(f"\n回退办法（万一）：git reset --hard {before[:8]}，或从 {bundle} 恢复")
    return 0 if still == 0 else 1


def main() -> int:
    if "--apply" not in sys.argv:
        n = survey()
        print("\n" + "=" * 72)
        print("这是【演练】。真正执行请加 --apply（会先自动备份整个仓库）。")
        print("但请记住：**轮换口令才是修复，清理历史只是止损。**")
        return 0 if n else 0
    survey()
    return apply_purge()


if __name__ == "__main__":
    sys.exit(main())
