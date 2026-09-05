# -*- coding: utf-8 -*-
"""pywebview 娓叉煋閾捐矾鍐掔儫娴嬭瘯: 寮瑰嚭涓€涓?2 绉掔殑娴嬭瘯绐楀彛."""
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import webview

html = """<!doctype html><html><head><meta charset="utf-8"><style>
body{background:#fbfaf6;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;
     font-family:'Segoe UI Variable Text','Segoe UI','PingFang SC','Microsoft YaHei UI',sans-serif}
.card{background:#173f33;color:#fbfaf6;padding:28px 44px;border-radius:16px;
      box-shadow:0 18px 50px rgba(26,50,40,.1);text-align:center}
.small{color:#c5a05a;margin-top:8px;font-size:13px}
</style></head><body><div class="card"><h2>pywebview 娓叉煋姝ｅ父</h2>
<div class="small">Hello Pinghe! Launcher UI 閾捐矾 OK 路 绐楀彛鍗冲皢鑷姩鍏抽棴</div></div></body></html>"""


def close_later():
    time.sleep(6.0)
    try:
        webview.windows[0].destroy()
    except Exception:
        pass


window = webview.create_window("Hello Pinghe! Launcher 娓叉煋娴嬭瘯", html=html, width=440, height=280)
threading.Thread(target=close_later, daemon=True).start()
webview.start()
print("WINDOW_OK")

