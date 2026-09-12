# -*- coding: utf-8 -*-
"""真实界面探针: 用真正的 Api + 真正的 ui/app.js 打开窗口, 注入 JS 看渲染结果.

    set PHLL_DATA_DIR=<数据目录>
    python -X utf8 scripts/probe_ui_real.py

与 _ui_test.py 的区别: 那个用 MockApi 测渲染, 这个接真实后端(会联网),
用来复现"数据明明有、界面却空着"这类前端问题(顺带抓 JS 报错)。
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import webview

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe.app.bridge import Api  # noqa: E402

UI = ROOT / "ui" / "index.html"

PROBE_JS = r"""
window.__probe_result = null;
window.__probeErrs = [];
window.addEventListener('error', (e) => window.__probeErrs.push(String(e.message)));
window.addEventListener('unhandledrejection', (e) => window.__probeErrs.push('reject: ' + String(e.reason)));
(async () => {
  const out = { hasRenderTimetable: typeof renderTimetable, hasShow: typeof show, hasCall: typeof call };
  const weekInfo = (d) => ({
    days: (d.week || []).length,
    perDay: (d.week || []).map((x) => (x.lessons || []).length),
    errors: (d.week || []).map((x) => x.error || "").filter(Boolean),
    firstDay: ((d.week || [])[0] || {}).day,
    sample: ((d.week || [])[0] || {}).lessons ? (d.week[0].lessons || []).slice(0, 3) : [],
  });

  // 0) 当前选课
  try {
    const st = await call('settings_get');
    out.selection = st.selected_lessons || [];
  } catch (e) { out.selectionError = String(e && e.message || e); }

  // 1) 选课页 -> 课表页(用户的实际流程: 改完选课再看课表)
  try { show('timetable'); } catch (e) { out.showError = String(e && e.message || e); }
  await new Promise((r) => setTimeout(r, 6000));
  try { out.weekBefore = weekInfo(await call('timetable_week', 0)); }
  catch (e) { out.weekBeforeError = String(e && e.message || e); }
  const wk = document.querySelector('#tt-week');
  out.domBefore = { range: (document.querySelector('#tt-range') || {}).textContent || '',
                    children: wk ? wk.children.length : -1,
                    text: ((wk && wk.innerText) || '').replace(/\s+/g, ' ').slice(0, 160) };

  // 2) 模拟"重新保存选课"(与向导里点保存同一条链路)
  try {
    const sel = JSON.stringify(out.selection || []);
    out.saved = await call('wizard_save_selection', sel);
  } catch (e) { out.saveError = String(e && e.message || e); }
  try { if (typeof Store !== 'undefined' && Store.drop) { Store.drop('tt|'); Store.drop('home'); } } catch (e) { out.dropError = String(e); }

  // 3) 保存之后再打开课表页
  try { show('home'); } catch (e) { /* noop */ }
  await new Promise((r) => setTimeout(r, 1500));
  try { show('timetable'); } catch (e) { out.showError2 = String(e && e.message || e); }
  await new Promise((r) => setTimeout(r, 8000));
  try { out.weekAfter = weekInfo(await call('timetable_week', 0)); }
  catch (e) { out.weekAfterError = String(e && e.message || e); }
  const wk2 = document.querySelector('#tt-week');
  out.domAfter = { range: (document.querySelector('#tt-range') || {}).textContent || '',
                   children: wk2 ? wk2.children.length : -1,
                   text: ((wk2 && wk2.innerText) || '').replace(/\s+/g, ' ').slice(0, 200) };

  // 4) 我的课程页
  try { show('courses'); } catch (e) { out.coursesShowError = String(e && e.message || e); }
  await new Promise((r) => setTimeout(r, 7000));
  try {
    const cc = await call('courses_data');
    out.courses = { classes: (cc.classes || []).length,
                    upcoming: (cc.tasks_upcoming || []).length,
                    past: (cc.tasks_past || []).length,
                    graded: Object.values(cc.grades || {}).filter(Boolean).length,
                    firstClass: (cc.classes || [])[0] || null };
  } catch (e) { out.coursesError = String(e && e.message || e); }
  out.coursesDom = {
    chips: document.querySelectorAll('#co-chips .chip').length,
    tasksText: ((document.querySelector('#co-tasks') || {}).innerText || '').replace(/\s+/g, ' ').slice(0, 200),
    classesText: ((document.querySelector('#co-classes') || {}).innerText || '').replace(/\s+/g, ' ').slice(0, 220),
  };

  // 5) 首页 DDL
  try { show('home'); } catch (e) { /* noop */ }
  await new Promise((r) => setTimeout(r, 5000));
  out.homeDdl = ((document.querySelector('#home-ddl') || {}).innerText || '').replace(/\s+/g, ' ').slice(0, 200);

  out.errs = window.__probeErrs;
  window.__probe_result = JSON.stringify(out);
})();
"""

result: dict = {}


def run(window) -> None:
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            if window.evaluate_js("!!window.pywebview && !!window.pywebview.api"):
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    time.sleep(3)
    try:
        window.evaluate_js(PROBE_JS)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"inject: {type(exc).__name__}: {exc}"
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            value = window.evaluate_js("window.__probe_result || null")
        except Exception:  # noqa: BLE001
            value = None
        if value:
            result["raw"] = value
            break
        time.sleep(0.5)
    try:
        window.destroy()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    api = Api()
    window = webview.create_window("PLL probe", str(UI), js_api=api, width=1400, height=900)
    webview.start(run, window, gui="edgechromium")
    payload = result.get("raw") or result.get("error") or "no result"
    # 同时写一份文件: PowerShell 传中文/引号给 node 会把 JSON 弄坏。
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(str(payload), encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
