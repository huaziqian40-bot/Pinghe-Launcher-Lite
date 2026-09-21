# -*- coding: utf-8 -*-
"""开机画面探针：复现「进度条 100% 但不进主界面」。

    PHLL_DATA_DIR=<数据副本> python -X utf8 scripts/probe_splash.py [输出文件]

做法：用真实 Api + 真实 ui/index.html 打开窗口，让 boot() 自己跑开机画面，
然后每 0.5 秒采一次：进度条宽度 / splash 是否还在 / 有没有 JS 报错。
如果卡在 100%，就手动把 enterApp 会调的那几步逐条包进 try/catch 跑一遍，
把真正抛异常的那一步和堆栈抓出来。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from hellopinghe.app.bridge import Api  # noqa: E402

UI = ROOT / "ui" / "index.html"

# 尽早装上的错误陷阱（在 boot 的三条链跑完之前装上就来得及）
TRAP_JS = r"""
(function () {
  window.__splashErrs = window.__splashErrs || [];
  if (window.__splashTrap) return 'already';
  window.__splashTrap = true;
  window.addEventListener('error', function (e) {
    window.__splashErrs.push({kind: 'error', msg: String(e.message),
                              file: String(e.filename || ''), line: e.lineno, col: e.colno,
                              stack: e.error && e.error.stack ? String(e.error.stack).slice(0, 900) : ''});
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason;
    window.__splashErrs.push({kind: 'reject', msg: String(r && r.message || r),
                              stack: r && r.stack ? String(r.stack).slice(0, 900) : ''});
  });
  return 'armed';
})()
"""

# 采样开机画面状态
STATE_JS = r"""
(function () {
  var sp = document.getElementById('splash');
  var fill = document.getElementById('splash-fill');
  var home = document.getElementById('view-home');
  return JSON.stringify({
    splashExists: !!sp,
    splashHidden: sp ? sp.classList.contains('hidden') : null,
    splashOpacity: sp ? (sp.style.opacity || getComputedStyle(sp).opacity) : null,
    splashDisplay: sp ? getComputedStyle(sp).display : null,
    splashVis: sp ? getComputedStyle(sp).visibility : null,
    fillWidth: fill ? fill.style.width : null,
    booting: document.body.classList.contains('booting'),
    homeActive: home ? home.classList.contains('active') : null,
    errCount: (window.__splashErrs || []).length,
    enteredFlag: (typeof entered !== 'undefined') ? entered : 'no-var'
  });
})()
"""

# 卡住时：把 enterApp 的每一步单独跑一遍，看哪一步抛
STEP_JS = r"""
(function () {
  var out = {};
  function t(name, fn) {
    try { var v = fn(); out[name] = 'ok' + (v === undefined ? '' : ('(' + typeof v + ')')); }
    catch (e) { out[name] = 'THROW ' + (e && e.message) + ' @ ' + String(e && e.stack || '').split('\n').slice(0,3).join(' | '); }
  }
  t('typeof_show', function () { if (typeof show !== 'function') throw new Error('show 不是函数: ' + typeof show); });
  t('typeof_loadHome', function () { if (typeof loadHome === 'undefined') throw new Error('loadHome 未定义'); });
  t('typeof_paintDataStamp', function () { if (typeof paintDataStamp !== 'function') throw new Error('paintDataStamp 不是函数'); });
  t('call_paintDataStamp', function () { paintDataStamp(); });
  t('call_show_home', function () { show('home'); });
  t('typeof_toast', function () { if (typeof toast !== 'function') throw new Error('toast 不是函数'); });
  t('typeof_TITLES', function () { if (typeof TITLES === 'undefined') throw new Error('TITLES 未定义'); });
  out.errs = window.__splashErrs || [];
  return JSON.stringify(out);
})()
"""

result: dict = {}


def poll(window, label: str, seconds: float, out: dict) -> None:
    end = time.time() + seconds
    samples = []
    while time.time() < end:
        try:
            raw = window.evaluate_js(STATE_JS)
            st = json.loads(raw) if isinstance(raw, str) else {}
        except Exception as exc:  # noqa: BLE001
            st = {"eval_error": f"{type(exc).__name__}: {exc}"}
        samples.append(st)
        print(f"  [{label}] fill={st.get('fillWidth')} hidden={st.get('splashHidden')} "
              f"op={st.get('splashOpacity')} booting={st.get('booting')} "
              f"homeActive={st.get('homeActive')} errs={st.get('errCount')}", flush=True)
        if st.get("splashHidden") or st.get("splashOpacity") == "0":
            out["settled_at"] = label
            break
        time.sleep(1.0)
    out["samples"] = samples


def probe(window) -> None:
    out: dict = {}
    # 等 pywebview api 就绪，第一时间装错误陷阱
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if window.evaluate_js("!!(window.pywebview && window.pywebview.api)"):
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    try:
        out["trap"] = window.evaluate_js(TRAP_JS)
    except Exception as exc:  # noqa: BLE001
        out["trap_err"] = str(exc)

    poll(window, "开机画面 0-40s", 40, out)

    stuck = not out.get("settled_at")
    out["stuck"] = stuck
    if stuck:
        try:
            out["steps"] = json.loads(window.evaluate_js(STEP_JS))
        except Exception as exc:  # noqa: BLE001
            out["steps_err"] = str(exc)

    try:
        out["final_errs"] = json.loads(window.evaluate_js(
            "JSON.stringify(window.__splashErrs || [])"))
    except Exception as exc:  # noqa: BLE001
        out["final_errs"] = [str(exc)]

    result["data"] = out
    try:
        window.destroy()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    api = Api()
    window = webview.create_window("PLL splash probe", str(UI), js_api=api,
                                   width=1400, height=900,
                                   background_color="#102d25")
    webview.start(probe, window, gui="edgechromium")
    payload = json.dumps(result.get("data", {}), ensure_ascii=False, indent=2)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(payload, encoding="utf-8")
    print("\n===== 结论 =====")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
