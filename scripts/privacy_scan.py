# -*- coding: utf-8 -*-
"""提交前隐私扫描：在"将要提交"的文件里找真实凭据 / 个人信息。

只输出命中位置与**脱敏后的**证据，绝不打印命中内容的完整原文。
用法：python -X utf8 scripts\\privacy_scan.py
退出码：0 = 可以提交；1 = 有「必须为零」的命中，先处理。

**这个文件本身会入库，所以绝不能把任何真实机密写在这里。**
需要按"已知值"精确匹配时，用下面两种方式之一提供（都不入库）：
    set PHIX_SCAN_SECRETS=值1,值2
    或写 .secrets-scan.txt（每行一个，已在 .gitignore 里）
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: (名字, 正则, 是否属于"必须零命中")
PATTERNS = [
    ("真实邮箱(平和域)", r"[\w.+-]+@(?:shphschool\.com|shph\.managebac\.cn|qiye\.163\.com)", True),
    ("任意邮箱", r"[\w.+-]+@[\w-]+\.[a-z]{2,}", False),
    ("口令字段(明文赋值)", r"(?:password|passwd|authcode|secret|token|api[_-]?key)\s*[:=]\s*[\"'][^\"'\s]{6,}[\"']", True),
    ("私钥块", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", True),
    ("内网 IP", r"\b192\.168\.\d{1,3}\.\d{1,3}\b", False),
    ("本机用户名", r"\b(?:huaziqian|huazixian|Norine2010|hzq)\b", False),
    ("学号形态", r"\b\d{8,12}\b", False),
    ("GitHub token 形态", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", True),
    ("OpenAI/Anthropic key", r"\b(?:sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9\-_]{20,})\b", True),
    ("32 位 hex（可能是令牌）", r"\b[0-9a-f]{32}\b", False),
]

#: 明确白名单：这些命中已人工确认为「不是凭据」，不算问题。
#: 加白名单必须写清理由 —— 这个表本身就是审计记录。
ALLOW = [
    # 向导里的输入示例，不是真实地址（用 name25@ 这种通用写法）
    ("ui/index.html", r"name25@shphschool\.com"),
    # Ollama 的 api_key 是约定值：本地服务不校验，写什么都可以（官方文档即 ollama）
    ("hellopinghe/config.py", r'api_key\s*=\s*["\']ollama["\']'),
]

#: 不扫的文件/目录（构建产物、依赖、二进制，以及**本文件自己** ——
#: 本文件通篇都是"用来识别的模式串"，扫自己只会产生自指噪声）
SKIP_DIRS = {".git", "build", "dist", "node_modules", "__pycache__", "testenv",
             "PH-Launcher", "_cleanup", "deliver", "tools"}
SKIP_FILES = {"scripts/privacy_scan.py"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".ico", ".dmg", ".exe", ".msi", ".zip",
            ".wixobj", ".wixpdb", ".pyc", ".db", ".sqlite3", ".woff", ".woff2"}


def known_secrets() -> list[str]:
    """本机私有的「已知机密」清单 —— 只从环境变量 / 被忽略的文件读，绝不硬编码。"""
    vals = [v.strip() for v in os.environ.get("PHIX_SCAN_SECRETS", "").split(",") if v.strip()]
    p = os.path.join(ROOT, ".secrets-scan.txt")
    if os.path.isfile(p):
        try:
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    vals.append(line)
        except OSError:
            pass
    return vals


def mask(s: str) -> str:
    """把疑似凭据脱敏：保留前 3 后 2，中间打码。"""
    s = s.strip()
    if len(s) <= 6:
        return s[0] + "*" * (len(s) - 1) if s else ""
    return s[:3] + "*" * min(12, len(s) - 5) + s[-2:]


def candidate_files():
    """git 视角下"会被提交"的文件：已跟踪 + 未忽略的未跟踪。"""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8").stdout.split("\n")
    un = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ROOT,
                        capture_output=True, text=True, encoding="utf-8").stdout.split("\n")
    seen = set()
    for rel in out + un:
        rel = rel.strip()
        if not rel or rel in seen:
            continue
        seen.add(rel)
        yield rel


def is_allowed(rel: str, text: str) -> bool:
    return any(f == rel and re.search(pat, text) for f, pat in ALLOW)


def main() -> int:
    secrets = known_secrets()
    patterns = list(PATTERNS)
    if secrets:
        # 值只存在于内存/环境里，不落在源码中
        patterns.append(("已知机密（本机提供）", "|".join(re.escape(s) for s in secrets), True))

    hits = {name: [] for name, _, _ in patterns}
    scanned = 0
    for rel in candidate_files():
        if rel in SKIP_FILES:
            continue
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        if any(part in SKIP_DIRS for part in rel.split("/")[:-1]):
            continue
        if os.path.splitext(p)[1].lower() in SKIP_EXT:
            continue
        try:
            text = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        scanned += 1
        allowed = is_allowed(rel, text)
        for name, pat, _ in patterns:
            for m in re.finditer(pat, text):
                if allowed and any(re.search(p2, m.group(0)) for _, p2 in ALLOW):
                    continue
                line_no = text.count("\n", 0, m.start()) + 1
                hits[name].append((rel, line_no, mask(m.group(0))))

    print(f"扫描了 {scanned} 个将被提交的文本文件")
    if secrets:
        print(f"（另外用了 {len(secrets)} 条本机提供的『已知机密』做精确匹配）")
    print("=" * 70)
    fatal = 0
    for name, _pat, must_be_zero in patterns:
        hs = hits[name]
        flag = "必须为零" if must_be_zero else "仅供参考"
        print(f"\n[{name}] ({flag}) 命中 {len(hs)}")
        for rel, ln, m in hs[:12]:
            print(f"    {rel}:{ln}  →  {m}")
        if len(hs) > 12:
            print(f"    …另有 {len(hs) - 12} 处")
        if must_be_zero and hs:
            fatal += len(hs)
    print("\n" + "=" * 70)
    if fatal:
        print(f"❌ 有 {fatal} 处「必须为零」的命中 —— 不允许提交，先处理")
    else:
        print("✅ 「必须为零」的类别全部零命中，可以提交")
    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
