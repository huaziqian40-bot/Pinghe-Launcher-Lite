# -*- coding: utf-8 -*-
"""Hello Pinghe! Launcher 启动器: 双击运行或命令行 python run_hellopinghe.py

如果窗口闪退，请双击 '启动 Hello Pinghe.bat' 查看错误信息。
"""
import sys
import os

# 确保当前目录在 path 中，这样双击时能找到 hellopinghe 包
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# 确保控制台输出使用 UTF-8，避免 Windows 下中文乱码/崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 延迟导入，让 path 设置先生效
def run():
    from hellopinghe.app.__main__ import main
    main()

if __name__ == "__main__":
    run()
