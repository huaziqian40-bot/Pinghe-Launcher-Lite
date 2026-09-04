# -*- coding: utf-8 -*-
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
src = open(r"C:\Users\aaaab\AppData\Roaming\npm\node_modules\@deepseek-ai\dsh\lib\bin.js",
           encoding="utf-8", errors="replace").read()
print("bin.js 长度:", len(src))
for kw in ["command(", "option(", "headless", "--print", "serve", "mcp", "output-format", "session", "web"]:
    print(f"  {kw:<15} x{len(re.findall(re.escape(kw), src))}")
cmds = re.findall(r'command\(\s*["\']([^"\']+)', src)
print("子命令:", cmds[:20])
