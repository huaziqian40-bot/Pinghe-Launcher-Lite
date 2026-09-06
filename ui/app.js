/* Hello Pinghe! Launcher 前端逻辑: 路由 + 各视图加载 + 向导 + Agent */
"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function call(name, ...args) {
  const r = await window.pywebview.api[name](...args);
  if (!r || r.ok !== true) {
    throw new Error((r && (r.error || r.detail)) || "调用失败");
  }
  return r.data !== undefined ? r.data : r;
}

/* ================= 本地缓存层(秒开) =================
 * stale-while-revalidate: 有缓存先渲染(页面永远不空),
 * 过期/无缓存时后台拉新数据, 拉到后无感更新。
 * 启动连接页会把各页面数据预载进这里 —— 见 runSplash()。
 */
const Store = {
  get(k) {
    try { return JSON.parse(localStorage.getItem("sh_" + k)); } catch { return null; }
  },
  set(k, v) {
    try { localStorage.setItem("sh_" + k, JSON.stringify({ t: Date.now(), v })); } catch { /* 满 */ }
  },
  drop(prefix) {
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith("sh_" + prefix))
        .forEach((k) => localStorage.removeItem(k));
    } catch { /* ignore */ }
  },
};
const TTL = { home: 60e3, tt: 120e3, gt: 300e3, courses: 180e3, mail: 45e3 };

/* 本地时区的 YYYY-MM-DD(不用 toISOString, 避免 UTC 偏移导致"今天"差一天) */
function isoOf(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/* 学校作息(课表按此对齐): P1-P10 + Lunch + 晚自习 */
const PERIODS = [
  { name: "P1", start: "08:00", end: "08:40" },
  { name: "P2", start: "08:45", end: "09:25" },
  { name: "P3", start: "09:35", end: "10:15" },
  { name: "P4", start: "10:20", end: "11:00" },
  { name: "P5", start: "11:05", end: "11:55" },
  { name: "Lunch", start: "12:00", end: "12:40", rest: true },
  { name: "P6", start: "12:45", end: "13:25" },
  { name: "P7", start: "13:30", end: "14:10" },
  { name: "P8", start: "14:15", end: "14:55" },
  { name: "P9", start: "15:00", end: "15:40" },
  { name: "P10", start: "15:45", end: "16:25" },
  { name: "晚自习", start: "18:00", end: "20:30", rest: true },
];

/* 课的开始时间落在哪个节次; 不在任何时段返回 -1(归入"课外") */
function periodOf(start) {
  if (!start) return -1;
  for (let i = 0; i < PERIODS.length; i++) {
    if (start >= PERIODS[i].start && start < PERIODS[i].end) return i;
  }
  return -1;
}

/* 通用 SWR: 缓存新→只渲染缓存; 缓存旧→渲染旧 + 后台刷新; 无缓存→骨架屏 + 等待 */
function swr(key, ttl, fetcher, render, skeleton, isCurrent) {
  return async () => {
    const c = Store.get(key);
    if (c) render(c.v);
    else if (skeleton) skeleton();
    if (c && Date.now() - c.t < ttl) return c.v;   // 缓存足够新: 零等待
    const d = await fetcher();
    Store.set(key, d);
    if (!isCurrent || isCurrent()) render(d);      // 用户已切走就不重绘
    return d;
  };
}
/* 预载: 只拉数据进缓存, 不渲染 */
const preload = (key, fetcher) => async () => {
  const d = await fetcher();
  Store.set(key, d);
  return d;
};

function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), ms);
}

const badge = (text, cls = "") =>
  `<span class="badge ${cls}">${esc(text || "")}</span>`;

/* ================= 路由 ================= */
const TITLES = {
  home: "首页", timetable: "我的课表", schedule: "我的日程",
  gradett: "班级课表", courses: "我的课程", mail: "平和邮箱",
  agent: "Agent 助手", settings: "设置",
};
let currentView = "home";
let ttOffset = 0;
let mailMode = 0;

function show(view) {
  currentView = view;
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.go === view));
  $("#view-title").textContent = TITLES[view] || view;
  const loaders = {
    home: loadHome, timetable: loadTimetable, schedule: loadSchedule,
    gradett: loadGradett, courses: loadCourses, mail: loadMail,
    agent: loadAgent, settings: loadSettings,
  };
  (loaders[view] || (() => {}))().catch((e) => toast(e.message));
}

$("#nav").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-go]");
  if (btn) show(btn.dataset.go);
});
/* 首页卡片快捷跳转: 点卡片直接进对应页面 */
document.addEventListener("click", (e) => {
  const card = e.target.closest(".go-card[data-go]");
  if (card && currentView === "home") show(card.dataset.go);
});
$("#btn-refresh").addEventListener("click", () => show(currentView));
/* 点左上角软件名/logo 回首页 */
$("#logo").addEventListener("click", () => show("home"));

/* ================= 首页 ================= */
function lessonLine(l) {
  return `<div class="item"><span class="dim">${esc(l.start)}-${esc(l.end)}</span>
    <span class="grow"><b>${esc(l.subject)}</b>${l.cancelled ? " <s>(已取消)</s>" : ""}
    <span class="dim">${esc(l.room)} ${esc(l.teacher)}</span></span></div>`;
}

/* 可左滑删除的列表项(首页 DDL 与我的课程共用):
 * 按住向左拖超过 70px 触发 onSwipe, 项滑出并移除; 不足则弹回。 */
function swipeableItemEl(className, innerHtml, onSwipe) {
  const el = document.createElement("div");
  el.className = className;
  el.innerHTML = innerHtml;
  let startX = 0, dx = 0, dragging = false;
  el.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    startX = e.clientX; dx = 0; dragging = true;
    el.classList.add("swiping");
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    dx = e.clientX - startX;
    if (dx < 0) {
      el.style.transform = `translateX(${Math.max(dx, -90)}px)`;
      el.classList.toggle("reveal", dx < -30);
    }
  });
  el.addEventListener("pointerup", async () => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove("swiping");
    if (dx < -70) {
      el._swipedAt = Date.now();
      try { await onSwipe(); } catch (e) { /* 静默 */ }
      el.style.transform = "translateX(-110%)";
      setTimeout(() => el.remove(), 160);
    } else {
      el.style.transform = "";
      el.classList.remove("reveal");
    }
  });
  el.addEventListener("pointercancel", () => {
    dragging = false;
    el.style.transform = "";
    el.classList.remove("reveal");
  });
  return el;
}

function ddlItemEl(it) {
  return swipeableItemEl(
    `item ddl-item ${it.urgent ? "urgent" : ""}`,
    `<span class="dim">${esc((it.due_at || "").slice(5, 16))}</span>
    <span class="grow">${esc(it.title)}<span class="dim"> · ${esc(it.course)}</span></span>
    ${badge(it.status || it.category, it.status === "Pending" ? "red" : "")}`,
    async () => { await call("ddl_dismiss", it.key); Store.drop("home"); },
  );
}

function renderHome(d) {
  $("#home-date").textContent = `${d.now} ${d.weekday}`;
  const cur = d.current_lesson;
  $("#home-current").innerHTML = cur
    ? `${esc(cur.subject)}<small>${esc(cur.start)}-${esc(cur.end)} · ${esc(cur.room)} · ${esc(cur.teacher)}</small>`
    : `<span class="muted">此刻没有课</span>`;
  const nxt = d.next_lesson;
  $("#home-next").innerHTML = nxt
    ? `${esc(nxt.subject)}<small>${nxt.day && nxt.day !== d.now ? esc(nxt.day_label) + " · " : ""}${esc(nxt.start)} 开始${nxt.room ? " · " + esc(nxt.room) : ""}${nxt.teacher ? " · " + esc(nxt.teacher) : ""}</small>`
    : `<span class="muted">最近没有课</span>`;
  $("#home-unread").textContent = d.unread_mail ?? "–";
  $("#home-lessons").innerHTML = (d.today_lessons || []).map(lessonLine).join("") ||
    `<div class="empty">${esc(d.timetable_error || "今天没有课")}</div>`;
  $("#home-events").innerHTML = (d.today_events || []).map(
    (e) => `<div class="item"><span class="dim">${esc(e.time || "全天")}</span>
      <span class="grow">${esc(e.title)}</span></div>`).join("") ||
    `<div class="empty">今天没有日程</div>`;
  const ddlBox = $("#home-ddl");
  ddlBox.innerHTML = "";
  (d.ddl || []).forEach((it) => ddlBox.appendChild(ddlItemEl(it)));
  if (!(d.ddl || []).length) {
    ddlBox.innerHTML = `<div class="empty">${esc(d.ddl_error || "±14 天内没有 DDL(最近 2 天与 2 天内的会加粗, 左滑可删除)")}</div>`;
  }
}
const loadHome = swr("home", TTL.home,
  () => call("home_data"), renderHome,
  () => {
    renderHome({ now: "…", weekday: "", today_lessons: [], today_events: [], ddl: [] });
  },
  () => currentView === "home");

setInterval(() => {
  const el = $("#home-clock");
  if (el && currentView === "home") {
    el.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  }
}, 500);

/* ================= 我的课表 ================= */
/* 每个教学组一个自己的颜色: 按 (科目族, 组号) 稳定散到调色板上 */
const TT_PALETTE = [
  ["#e3f2e3", "#1d6b3c"], ["#e3edf7", "#1d4f7c"], ["#fdeee3", "#a04d12"],
  ["#f3e8f7", "#6b2d8c"], ["#fde8ef", "#a01d55"], ["#e0f2f1", "#00695c"],
  ["#fff7dc", "#8a6d00"], ["#e8eaf6", "#303f9f"], ["#e0f7fa", "#006978"],
  ["#f9ebeb", "#8c1d1d"], ["#eef6e3", "#4a7c1d"], ["#efe3f2", "#6b1d7c"],
  ["#e3f6f0", "#0b6b5d"], ["#fbe9e0", "#8c3d1d"], ["#e9e9f2", "#3d3d8c"],
  ["#f2f0e3", "#6b641d"],
];
function ttColor(l) {
  const k = `${subjFamily(l.subject)}|${l.group || ""}`;
  let h = 0;
  for (let i = 0; i < k.length; i++) h = (h * 31 + k.charCodeAt(i)) >>> 0;
  const [bg, fg] = TT_PALETTE[h % TT_PALETTE.length];
  return `--tbg:${bg};--tfg:${fg}`;
}
/* 高亮/变灰标记(本机 localStorage, 不进配置文件) */
let ttMarks = Store.get("ttmarks") || {};
const ttMarkKey = (l, day) => `${day}|${l.start}|${subjFamily(l.subject)}|${l.group || ""}`;

/* 一张课卡; ti = 在 ttFlat 里的下标(点击/右键时取回完整信息)。
   place: 作为格子内容时留空, 连堂合并块给 "grid-column:X;grid-row:R / span N" */
function ttLessonHtml(l, ti, withTime, place) {
  const mark = l._mark ? ` tt-${l._mark}` : "";
  const style = `${ttColor(l)}${place ? ";" + place : ""}`;
  return `<div class="tt-lesson${mark}${l.cancelled ? " cancelled" : ""}"
    data-ti="${ti}" style="${style}">
    ${withTime ? `<span class="rm">${esc(l.start)}${l.end ? "–" + esc(l.end) : ""}</span>` : ""}
    <b>${esc(l.subject)}</b>
    <span class="rm">${esc(l.room)}${l.teacher ? " · " + esc(l.teacher) : ""}</span>
  </div>`;
}
/* 连堂判定: 同科目族+同老师+同组+同教室才算"连续两节一样的课" */
function ttSameKey(l) {
  return `${subjFamily(l.subject)}|${l.teacher}|${l.group || ""}|${l.room}|${l.cancelled ? 1 : 0}`;
}

let ttFlat = [];       // 课卡扁平表, 渲染时构建, 点击/右键按下标取
let ttWeekData = null; // 当前渲染的周数据(弹卡里"全部上课时间"从这里取)

/* 按节次对齐的周课表: 行=时段(P1-P10/Lunch/晚自习), 列=周一到周日。
   全部格子显式定位(grid-row/grid-column); 格子里的课 flex:1 平分格高;
   同一天连续两节一样的课合并成一块跨两行(盖住后续空格)。 */
function renderTimetable(d) {
  $("#tt-range").textContent = `${d.week[0].day} ~ ${d.week[6].day}`;
  const today = isoOf(new Date());
  ttWeekData = d;
  ttFlat = [];

  const byDay = d.week.map((day) => {
    const cells = PERIODS.map(() => []);
    const other = [];
    (day.lessons || []).forEach((l) => {
      const ent = { ...l, _day: day.day, _label: day.label,
                    _mark: ttMarks[ttMarkKey(l, day.day)] || "" };
      const pi = periodOf(l.start);
      if (pi >= 0) cells[pi].push(ent);
      else other.push(ent);
    });
    /* 连堂预扫描: 该天某时段只有一节、下一时段也只有同一节课 → 合并 */
    const spanStart = {}, consumed = new Set();
    for (let pi = 0; pi < PERIODS.length; pi++) {
      if (cells[pi].length !== 1 || consumed.has(pi)) continue;
      let n = 1;
      while (pi + n < PERIODS.length && cells[pi + n].length === 1 &&
             ttSameKey(cells[pi][0]) === ttSameKey(cells[pi + n][0])) n++;
      if (n > 1) {
        spanStart[pi] = n;
        for (let k = 1; k < n; k++) consumed.add(pi + k);
      }
    }
    return { cells, other, day, spanStart, consumed };
  });

  let html = `<div class="tt-head tt-corner" style="grid-row:1;grid-column:1"></div>`;
  html += byDay.map(({ day }, di) =>
    `<div class="tt-head ${day.day === today ? "today" : ""}" style="grid-row:1;grid-column:${di + 2}">${esc(day.label)}</div>`).join("");

  const spans = [];   // 连堂合并块最后画, 盖在被跨过的空格上
  let r = 2;
  PERIODS.forEach((p, pi) => {
    const busy = byDay.some((b) => b.cells[pi].length);
    const timeCell = `<div class="tt-time ${p.rest ? "rest" : ""}" style="grid-row:${r};grid-column:1"><b>${esc(p.name)}</b>
      <span>${esc(p.start)}</span></div>`;
    /* Lunch/晚自习整周没课时渲染成一条横幅, 不占七列 */
    if (p.rest && !busy) {
      html += timeCell +
        `<div class="tt-restbar" style="grid-row:${r};grid-column:2/-1">${esc(p.name)} ${esc(p.start)} – ${esc(p.end)}</div>`;
      r++;
      return;
    }
    html += timeCell;
    byDay.forEach((b, di) => {
      const ls = b.cells[pi];
      let inner = "";
      if (b.spanStart[pi]) {
        const ti = ttFlat.length;
        ttFlat.push(ls[0]);
        spans.push(ttLessonHtml(ls[0], ti, false,
          `grid-column:${di + 2};grid-row:${r} / span ${b.spanStart[pi]}`));
      } else if (!b.consumed.has(pi)) {
        inner = ls.map((l) => {
          const ti = ttFlat.length;
          ttFlat.push(l);
          return ttLessonHtml(l, ti, false);
        }).join("");
      }
      html += `<div class="tt-cell ${p.rest ? "rest" : ""}" style="grid-row:${r};grid-column:${di + 2}">${inner}</div>`;
    });
    r++;
  });

  /* 不在任何时段的课(如临时调课)归到"课外"一行 */
  if (byDay.some((b) => b.other.length)) {
    html += `<div class="tt-time" style="grid-row:${r};grid-column:1"><b>课外</b></div>`;
    byDay.forEach((b, di) => {
      const items = b.other.map((l) => {
        const ti = ttFlat.length;
        ttFlat.push(l);
        return ttLessonHtml(l, ti, true);
      });
      html += `<div class="tt-cell" style="grid-row:${r};grid-column:${di + 2}">${items.join("")}</div>`;
    });
  }

  $("#tt-week").innerHTML = html + spans.join("");
  updateNowLine();
}
/* 当前时间指示线: 一根横线贯穿整张周课表, 落在"现在"对应的节次行内
   (行内按时间比例插值)。只在看本周(ttOffset=0)且时间在校内时段时显示。 */
function updateNowLine(nowMins) {
  const wk = $("#tt-week");
  const old = document.getElementById("tt-nowline");
  if (old) old.remove();
  if (currentView !== "timetable" || ttOffset !== 0) return;
  if (!wk.querySelector(".tt-time")) return;   /* 骨架屏/空态不放线 */
  const t = nowMins ?? (() => {
    const n = new Date();
    return n.getHours() * 60 + n.getMinutes() + n.getSeconds() / 60;
  })();
  const toMin = (s) => { const [h, m] = String(s).split(":").map(Number); return h * 60 + m; };
  if (t < toMin(PERIODS[0].start) || t > toMin(PERIODS[PERIODS.length - 1].end)) return;
  const pi = PERIODS.findIndex((p) => t < toMin(p.end));
  if (pi < 0) return;
  const p = PERIODS[pi];
  const f = Math.min(1, Math.max(0, (t - toMin(p.start)) / (toMin(p.end) - toMin(p.start))));
  /* 行号 = 时段下标 + 2(第 1 行是星期表头); 用 offsetTop(布局坐标,
     不受 zoom/viewport 缩放影响)而不是 getBoundingClientRect */
  const cell = [...wk.querySelectorAll(".tt-time")]
    .find((el) => el.style.gridRow === String(pi + 2));
  if (!cell) return;
  const y = cell.offsetTop + cell.offsetHeight * f;
  const line = document.createElement("div");
  line.id = "tt-nowline";
  line.style.top = `${y}px`;
  wk.appendChild(line);
}
setInterval(() => { if (currentView === "timetable") updateNowLine(); }, 30 * 1000);
function loadTimetable() {
  /* 页面上已有一幅真课表时切周, 保留旧画面(压暗+转圈徽标), 不闪空白;
     首次进入才用骨架屏。 */
  const wk = $("#tt-week");
  const keep = !!wk.querySelector(".tt-lesson");
  const skeleton = () => {
    if (keep) { $("#tt-wait").hidden = false; wk.classList.add("tt-wait"); }
    else wk.innerHTML = `<div class="tt-skeleton">${'<div class="skel-card"></div>'.repeat(7)}</div>`;
  };
  return swr(`tt|${ttOffset}`, TTL.tt,
    () => call("timetable_week", ttOffset), renderTimetable,
    skeleton, () => currentView === "timetable")()
    .catch((e) => { if (!keep) throw e; toast(e.message); })  // 旧画面还在: 只提示, 不清屏
    .finally(() => {
      $("#tt-wait").hidden = true;
      wk.classList.remove("tt-wait");
      ttPrefetch();
    });
}
/* 预取相邻周进本地缓存: 之后点上一周/下一周直接秒开 */
const ttPrefetched = new Set();
function ttPrefetch() {
  [-1, 1].forEach((o) => {
    const k = `tt|${ttOffset + o}`;
    if (ttPrefetched.has(k) || Store.get(k)) return;
    ttPrefetched.add(k);
    call("timetable_week", ttOffset + o).then((d) => Store.set(k, d)).catch(() => {});
  });
}
$("#tt-prev").onclick = () => { ttOffset--; loadTimetable().catch((e) => toast(e.message)); };
$("#tt-next").onclick = () => { ttOffset++; loadTimetable().catch((e) => toast(e.message)); };
$("#tt-this").onclick = () => { ttOffset = 0; loadTimetable().catch((e) => toast(e.message)); };

/* ---- 点击课卡 → 弹卡: 标题/老师/教室/全部上课时间 + 取消选课/退出 ---- */
let ttModalL = null;
let selLoaded = false;
async function ensureSelections() {
  /* 选课列表按需拉一次(弹卡里的"取消选课"要用; 设置页加载后跳过) */
  if (selLoaded || selectedLessonsCache.length) return;
  try {
    const d = await call("settings_get");
    selectedLessonsCache = d.selected_lessons || [];
    selLoaded = true;
  } catch { /* 拉不到就按空处理, 取消时会提示去设置里改 */ }
}
function openTtModal(l) {
  if (!l) return;
  ttModalL = l;
  ensureSelections();
  $("#ttm-title").textContent = l.subject;
  $("#ttm-label").textContent = l._label || "";
  $("#ttm-teacher").textContent = l.teacher || "(未指定老师)";
  $("#ttm-room").textContent = l.room || "—";
  $("#ttm-group").textContent = l.group ? `组${l.group}` : "全班必修";
  /* 本周内同一教学组的全部上课时间 */
  const fam = subjFamily(l.subject);
  const times = [];
  ((ttWeekData && ttWeekData.week) || []).forEach((day) =>
    (day.lessons || []).forEach((x) => {
      if (subjFamily(x.subject) === fam && (x.group || "") === (l.group || ""))
        times.push(`${day.label} ${x.start}–${x.end}`);
    }));
  $("#ttm-times").innerHTML = times.length
    ? [...new Set(times)].map((t) => `<div class="item"><span>${esc(t)}</span></div>`).join("")
    : `<div class="muted">本周仅此一次</div>`;
  $("#ttm-del").classList.toggle("hidden", !l.group);   // 全班必修课没有"取消选课"
  $("#tt-modal").classList.remove("hidden");
}
$("#ttm-close").onclick = () => $("#tt-modal").classList.add("hidden");
$("#ttm-del").onclick = async () => {
  const l = ttModalL;
  if (!l) return;
  await ensureSelections();
  const fam = subjFamily(l.subject);
  const kept = selectedLessonsCache.filter((s) =>
    !(subjFamily(s.subject) === fam && (s.group || "") === (l.group || "") &&
      (!s.teacher || s.teacher === l.teacher)));
  if (kept.length === selectedLessonsCache.length) {
    toast("没有找到对应的选课记录, 请到设置里重新选择");
    return;
  }
  try {
    await call("wizard_save_selection", JSON.stringify(kept));
    selectedLessonsCache = kept;
    Store.drop("tt|"); Store.drop("home");
    $("#tt-modal").classList.add("hidden");
    toast(`已取消 ${l.subject}${l.group ? " 组" + l.group : ""}`);
    loadTimetable().catch(() => {});
  } catch (e) { toast(e.message); }
};

/* ---- 右键课卡 → 菜单: 添加到日程 / 高亮 / 变灰 ---- */
let ttMenuL = null;
$("#tt-week").addEventListener("click", (e) => {
  const el = e.target.closest(".tt-lesson");
  if (el) openTtModal(ttFlat[+el.dataset.ti]);
});
$("#tt-week").addEventListener("contextmenu", (e) => {
  const el = e.target.closest(".tt-lesson");
  if (!el) return;
  e.preventDefault();
  openTtMenu(ttFlat[+el.dataset.ti], e.clientX, e.clientY);
});
function openTtMenu(l, x, y) {
  if (!l) return;
  ttMenuL = l;
  const mark = ttMarks[ttMarkKey(l, l._day)] || "";
  $("#tt-menu-hl").textContent = mark === "hl" ? "★ 取消高亮" : "★ 高亮这节课";
  $("#tt-menu-gray").textContent = mark === "gray" ? "取消变灰" : "变灰(弱化显示)";
  const m = $("#tt-menu");
  m.classList.remove("hidden");
  m.style.left = Math.min(x, window.innerWidth - m.offsetWidth - 8) + "px";
  m.style.top = Math.min(y, window.innerHeight - m.offsetHeight - 8) + "px";
}
function ttSetMark(mark) {
  const l = ttMenuL;
  $("#tt-menu").classList.add("hidden");
  if (!l) return;
  const k = ttMarkKey(l, l._day);
  if (ttMarks[k] === mark) delete ttMarks[k];
  else ttMarks[k] = mark;
  Store.set("ttmarks", ttMarks);
  loadTimetable().catch(() => {});
}
$("#tt-menu-add").onclick = async () => {
  const l = ttMenuL;
  $("#tt-menu").classList.add("hidden");
  if (!l) return;
  try {
    await call("schedule_add", l._day, l.start,
      l.subject + (l.group ? " (组" + l.group + ")" : ""),
      `${l.start}–${l.end}${l.room ? " · " + l.room : ""}${l.teacher ? " · " + l.teacher : ""}`);
    toast("已加入我的日程");
    Store.drop("sch|");
  } catch (e) { toast(e.message); }
};
$("#tt-menu-hl").onclick = () => ttSetMark("hl");
$("#tt-menu-gray").onclick = () => ttSetMark("gray");
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    $("#tt-modal").classList.add("hidden");
    $("#tt-menu").classList.add("hidden");
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#tt-menu")) $("#tt-menu").classList.add("hidden");
});

/* ================= 我的日程: 周 / 月 / 年 三种视图 =================
 * 点任意一天 → 弹出当天安排的卡片, 卡片里可直接添加/删除。
 * 数据来自本地 SQLite(schedule_range), 切视图毫秒级。
 */
let schView = "month";
let schAnchor = new Date();
let schEvents = [];
let schModalDay = null;
const WD = ["一", "二", "三", "四", "五", "六", "日"];

function schRange(view, anchor) {
  const y = anchor.getFullYear(), m = anchor.getMonth();
  if (view === "week") {
    const mon = new Date(y, m, anchor.getDate() - ((anchor.getDay() + 6) % 7));
    const sun = new Date(y, m, mon.getDate() + 6);
    return [isoOf(mon), isoOf(sun)];
  }
  if (view === "month") {
    return [`${y}-${String(m + 1).padStart(2, "0")}-01`,
      `${y}-${String(m + 1).padStart(2, "0")}-${new Date(y, m + 1, 0).getDate()}`];
  }
  return [`${y}-01-01`, `${y}-12-31`];
}

function schLabelText(view, anchor) {
  const [from, to] = schRange(view, anchor);
  if (view === "week") return `${from} ~ ${to.slice(5)}`;
  if (view === "month") return `${anchor.getFullYear()} 年 ${anchor.getMonth() + 1} 月`;
  return `${anchor.getFullYear()} 年`;
}

function groupByDay(events) {
  const map = new Map();
  (events || []).forEach((e) => {
    if (!map.has(e.day)) map.set(e.day, []);
    map.get(e.day).push(e);
  });
  return map;
}

const schEvHtml = (e) => `
  <div class="item" data-eid="${e.id}">
    <span class="dim">${esc(e.time || "全天")}</span>
    <span class="grow">${esc(e.title)}${e.note ? `<span class="dim"> · ${esc(e.note)}</span>` : ""}</span>
    <button class="danger" data-del="${e.id}">删除</button>
  </div>`;

function renderSchWeek() {
  const [from] = schRange("week", schAnchor);
  const byDay = groupByDay(schEvents);
  const today = isoOf(new Date());
  $("#sch-week").innerHTML = Array.from({ length: 7 }, (_, i) => {
    const dt = new Date(from + "T00:00:00");
    dt.setDate(dt.getDate() + i);
    const iso = isoOf(dt);
    const evs = byDay.get(iso) || [];
    return `<div class="sch-day ${iso === today ? "today" : ""}" data-day="${iso}">
      <h4>周${WD[i]} <span class="muted">${iso.slice(5)}</span></h4>
      ${evs.map(schEvHtml).join("") || `<div class="empty">无安排</div>`}
    </div>`;
  }).join("");
}

function renderSchMonth() {
  const y = schAnchor.getFullYear(), m = schAnchor.getMonth();
  const byDay = groupByDay(schEvents);
  const today = isoOf(new Date());
  const offset = (new Date(y, m, 1).getDay() + 6) % 7;   // 周一开头
  const nDays = new Date(y, m + 1, 0).getDate();
  let cells = Array(offset).fill(`<div class="cal-cell dim"></div>`);
  for (let d = 1; d <= nDays; d++) {
    const iso = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const evs = byDay.get(iso) || [];
    cells.push(`<div class="cal-cell ${iso === today ? "today" : ""}" data-day="${iso}">
      <span class="cal-num">${d}</span>
      ${evs.slice(0, 2).map((e) =>
        `<span class="cal-ev">${esc(e.time || "全天")} ${esc(e.title)}</span>`).join("")}
      ${evs.length > 2 ? `<span class="cal-more">还有 ${evs.length - 2} 项…</span>` : ""}
    </div>`);
  }
  $("#sch-month").innerHTML =
    WD.map((w) => `<div class="cal-head">周${w}</div>`).join("") + cells.join("");
}

function renderSchYear() {
  const y = schAnchor.getFullYear();
  const byDay = groupByDay(schEvents);
  const today = isoOf(new Date());
  $("#sch-year").innerHTML = Array.from({ length: 12 }, (_, m) => {
    const offset = (new Date(y, m, 1).getDay() + 6) % 7;
    const nDays = new Date(y, m + 1, 0).getDate();
    const cells = Array(offset).fill(`<i></i>`);
    for (let d = 1; d <= nDays; d++) {
      const iso = `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      const cls = byDay.has(iso) ? "has" : "";
      cells.push(`<i class="${cls} ${iso === today ? "today" : ""}" data-day="${iso}">${d}</i>`);
    }
    return `<div class="cal-mini"><b>${m + 1} 月</b>
      <div class="mini-grid">${cells.join("")}</div></div>`;
  }).join("");
}

async function loadSchedule() {
  ["week", "month", "year"].forEach((v) => {
    $(`#sch-${v}`).classList.toggle("hidden", v !== schView);
  });
  $$("#sch-views .chip").forEach((c) => c.classList.toggle("on", c.dataset.v === schView));
  $("#sch-label").textContent = schLabelText(schView, schAnchor);
  const [from, to] = schRange(schView, schAnchor);
  try {
    const d = await call("schedule_range", from, to);
    schEvents = d.events || [];
  } catch (e) {
    toast(e.message);
    schEvents = [];
  }
  if (currentView !== "schedule") return;
  if (schView === "week") renderSchWeek();
  else if (schView === "month") renderSchMonth();
  else renderSchYear();
  if (schModalDay) fillSchModal();
}

function schShift(delta) {
  const a = schAnchor;
  if (schView === "week") a.setDate(a.getDate() + delta * 7);
  else if (schView === "month") a.setMonth(a.getMonth() + delta);
  else a.setFullYear(a.getFullYear() + delta);
  loadSchedule().catch((e) => toast(e.message));
}

/* ---- 日程弹卡 ---- */
function fillSchModal() {
  if (!schModalDay) return;
  const wd = new Date(schModalDay + "T00:00:00").getDay();
  $("#schm-title").textContent =
    `${schModalDay} 周${WD[(wd + 6) % 7]} · 当天安排`;
  const evs = (schEvents || []).filter((e) => e.day === schModalDay);
  $("#schm-list").innerHTML = evs.map(schEvHtml).join("") ||
    `<div class="empty">这一天还没有安排, 下方可直接添加</div>`;
  bindSchDelete($("#schm-list"));
}
function openSchModal(dayIso) {
  schModalDay = dayIso;
  fillSchModal();
  $("#sch-modal").classList.remove("hidden");
}
function closeSchModal() {
  $("#sch-modal").classList.add("hidden");
  schModalDay = null;
}
/* 删除按钮走事件委托(周视图/弹卡两处共用) */
function bindSchDelete(container) {
  container.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await call("schedule_delete", b.dataset.del);
        toast("已删除");
        await loadSchedule();
      } catch (e) { toast(e.message); }
    };
  });
}

$("#sch-prev").onclick = () => schShift(-1);
$("#sch-next").onclick = () => schShift(1);
$("#sch-today").onclick = () => { schAnchor = new Date(); loadSchedule().catch((e) => toast(e.message)); };
$("#sch-views").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip || chip.dataset.v === schView) return;
  schView = chip.dataset.v;
  loadSchedule().catch((err) => toast(err.message));
});
$("#sch-add-btn").onclick = () => openSchModal(isoOf(new Date()));

/* 点击周视图/月视图/年视图里的任意一天 → 弹卡 */
["sch-week", "sch-month", "sch-year"].forEach((id) => {
  $("#" + id).addEventListener("click", (e) => {
    const cell = e.target.closest("[data-day]");
    if (cell) openSchModal(cell.dataset.day);
  });
});
$("#schm-close").onclick = closeSchModal;
$("#sch-modal").addEventListener("click", (e) => {
  if (e.target === $("#sch-modal")) closeSchModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#sch-modal").classList.contains("hidden")) closeSchModal();
  if (!$("#ml-modal").classList.contains("hidden")) { acClose(); $("#ml-modal").classList.add("hidden"); }
  if (!$("#ct-modal").classList.contains("hidden")) $("#ct-modal").classList.add("hidden");
});
$("#schm-add").onclick = async () => {
  const title = $("#schm-title-in").value.trim();
  if (!title) { toast("先填写事项内容"); return; }
  try {
    await call("schedule_add", schModalDay, $("#schm-time").value, title, $("#schm-note").value);
    $("#schm-title-in").value = ""; $("#schm-note").value = ""; $("#schm-time").value = "";
    toast("已添加");
    await loadSchedule();     // fillSchModal 会在 loadSchedule 末尾自动刷新
  } catch (e) { toast(e.message); }
};

/* ================= 外观设置(参考 dsh-ui-appearance: 预设+颜色角色+实时生效) =================
 * 4 个颜色角色(主色/背景/面板/文字)驱动整套设计 token, 主色自动派生
 * 同系色阶; 字体缩放用 body zoom(WebView2=Chromium)。存 localStorage。 */
const AP_DEFAULT = { accent: "#1f5a46", bg: "#fbfaf6", panel: "#ffffff", ink: "#18231e", scale: 120 };
const AP_PRESETS = [
  { name: "默认", accent: "#1f5a46", bg: "#fbfaf6", panel: "#ffffff", ink: "#18231e" },
  { name: "午夜", accent: "#34506e", bg: "#f3f6fa", panel: "#ffffff", ink: "#1b2430" },
  { name: "海洋", accent: "#1d6b7c", bg: "#f1f9fa", panel: "#ffffff", ink: "#10262b" },
  { name: "森林", accent: "#2a7233", bg: "#f3faf1", panel: "#ffffff", ink: "#152416" },
  { name: "玫瑰", accent: "#a04d55", bg: "#fbf4f4", panel: "#ffffff", ink: "#2b1c1f" },
  { name: "单色", accent: "#4d4d4d", bg: "#fafafa", panel: "#ffffff", ink: "#1c1c1c" },
];
function apLoad() {
  try {
    const raw = Store.get("appearance");   /* Store.get 返回 {t, v} 包装 */
    return { ...AP_DEFAULT, ...((raw && raw.v) || {}) };
  } catch { return { ...AP_DEFAULT }; }
}
function apSave(ap) { Store.set("appearance", ap); }
function apHexToRgb(h) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(h || "").trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function apMix(a, b, t) {   /* t=0 → a, t=1 → b */
  const A = apHexToRgb(a), B = apHexToRgb(b);
  if (!A || !B) return a;
  const c = A.map((v, i) => Math.round(v + (B[i] - v) * t));
  return "#" + c.map((v) => v.toString(16).padStart(2, "0")).join("");
}
const AP_TOKENS = ["--green-800", "--green-900", "--green-950", "--green-700",
  "--green-100", "--green-50", "--ivory-50", "--ivory-100", "--ivory-200",
  "--white", "--ink", "--ink-2", "--ink-3", "--border"];
function apApply(ap) {
  const r = document.documentElement.style;
  const scale = Number(ap.scale) || 120;
  const z = scale / 100;
  /* zoom 系数暴露给 CSS: 所有视口相对尺寸(卡片/布局)都要除回它,
     否则 body zoom 会把 94vw/88vh 放大出窗口(实测 88vh→1.23 倍窗高) */
  r.setProperty("--ap-zoom", String(z));
  document.body.style.zoom = scale === 100 ? "" : String(z);
  if (ap.accent === AP_DEFAULT.accent && ap.bg === AP_DEFAULT.bg &&
      ap.panel === AP_DEFAULT.panel && ap.ink === AP_DEFAULT.ink) {
    /* 默认外观: 清掉全部颜色 token 覆写, 回到样式表的设计值 */
    AP_TOKENS.forEach((t) => r.removeProperty(t));
    return;
  }
  const accent = apHexToRgb(ap.accent) ? ap.accent : AP_DEFAULT.accent;
  const bg = apHexToRgb(ap.bg) ? ap.bg : AP_DEFAULT.bg;
  const panel = apHexToRgb(ap.panel) ? ap.panel : AP_DEFAULT.panel;
  const ink = apHexToRgb(ap.ink) ? ap.ink : AP_DEFAULT.ink;
  r.setProperty("--green-800", accent);
  r.setProperty("--green-900", apMix(accent, "#000000", .16));
  r.setProperty("--green-950", apMix(accent, "#000000", .30));
  r.setProperty("--green-700", apMix(accent, "#ffffff", .10));
  r.setProperty("--green-100", apMix(accent, "#ffffff", .84));
  r.setProperty("--green-50", apMix(accent, "#ffffff", .92));
  r.setProperty("--ivory-50", bg);
  r.setProperty("--ivory-100", apMix(bg, ink, .05));
  r.setProperty("--ivory-200", apMix(bg, ink, .10));
  r.setProperty("--white", panel);
  r.setProperty("--ink", ink);
  r.setProperty("--ink-2", apMix(ink, bg, .40));
  r.setProperty("--ink-3", apMix(ink, bg, .62));
  const inkRgb = apHexToRgb(ink);
  /* border 必须保持半透明(原设计 rgba(ink,.1)), 写成不透明 hex = 全屏描边 bug */
  r.setProperty("--border", `rgba(${inkRgb.join(",")},0.1)`);
}
apApply(apLoad());   /* 脚本加载即套用, 避免闪默认色 */

let apPendingScale = null;   /* 滑块拖出的待应用值(按"应用"才生效) */
function apBindPanel() {
  const ap = apLoad();
  const scale = $("#ap-scale"), scaleVal = $("#ap-scale-val");
  scale.value = apPendingScale ?? (ap.scale || 120);
  scaleVal.textContent = `${Math.round(Number(scale.value))}%`;
  scale.oninput = () => {
    apPendingScale = Number(scale.value);   /* 无极调节: 只记数值, 不立即应用 */
    scaleVal.textContent = `${Math.round(apPendingScale)}%`;
  };
  const smoothApply = (fn) => {
    document.body.style.transition = "zoom .28s cubic-bezier(.2,.7,.3,1)";
    fn();
    setTimeout(() => { document.body.style.transition = ""; }, 350);
  };
  $("#ap-apply").onclick = () => {
    const next = { ...apLoad(), scale: Math.round(apPendingScale ?? (apLoad().scale || 120)) };
    apPendingScale = null;
    smoothApply(() => { apApply(next); apSave(next); apBindPanel(); });
    toast(`外观已应用: 字体缩放 ${next.scale}%`);
  };
  $("#ap-accent").value = ap.accent; $("#ap-bg").value = ap.bg;
  $("#ap-panel").value = ap.panel; $("#ap-ink").value = ap.ink;
  $$(".ap-hex").forEach((hex) => { hex.value = ap[hex.dataset.role] || ""; });
  const syncColor = (role) => {
    const c = $(`#ap-${role}`);
    c.oninput = () => {
      const next = { ...apLoad(), [role]: c.value };
      $(`.ap-hex[data-role="${role}"]`).value = c.value;
      apApply(next); apSave(next);
    };
  };
  ["accent", "bg", "panel", "ink"].forEach(syncColor);
  $$(".ap-hex").forEach((hex) => {
    hex.onchange = () => {
      const role = hex.dataset.role;
      if (!apHexToRgb(hex.value)) { hex.value = apLoad()[role]; return; }
      const next = { ...apLoad(), [role]: hex.value };
      $(`#ap-${role}`).value = hex.value;
      apApply(next); apSave(next);
    };
  });
  $("#ap-presets").innerHTML = AP_PRESETS.map((p) =>
    `<button class="ap-preset" data-p="${esc(p.name)}" title="主色 ${p.accent}">
       <i style="background:${p.accent}"></i>${esc(p.name)}</button>`).join("");
  $$("#ap-presets .ap-preset").forEach((b) => {
    b.onclick = () => {
      const p = AP_PRESETS.find((x) => x.name === b.dataset.p);
      const next = { ...p, scale: apLoad().scale || 120 };
      apPendingScale = null;
      apApply(next); apSave(next); apBindPanel();   /* 重绑控件值 */
    };
  });
  $("#ap-reset").onclick = () => {
    const next = { ...AP_DEFAULT };
    apPendingScale = null;
    smoothApply(() => { apApply(next); apSave(next); apBindPanel(); });
    toast("外观已恢复默认");
  };
}
/* 外观面板常驻设置页(loadSettings 时绑定控件值) */

/* ================= 班级课表 ================= */
let gtDay = new Date().toISOString().slice(0, 10);
let gtData = null;
function gtShift(delta) {
  const d = new Date(gtDay);
  d.setDate(d.getDate() + delta);
  gtDay = d.toISOString().slice(0, 10);
  loadGradett().catch((e) => toast(e.message));
}
async function loadGradett() {
  $("#gt-day-label").textContent = gtDay;
  const c = Store.get(`gt|${gtDay}`);
  if (c) {                       // 缓存先渲染, 页面不空
    gtData = c.v;
    $("#gt-day-label").textContent = gtData.day;
    renderGradett();
  } else {
    $("#gt-meta").textContent = "";
    $("#gt-body").innerHTML =
      `<div class="empty">⏳ 正在读取 ${esc(gtDay)} 的全校课表…(Edupage 服务器较慢, 最多约 1 分钟)</div>`;
  }
  const fresh = c && Date.now() - c.t < TTL.gt;
  if (fresh) return;
  try {
    const d = await call("gradett_data", gtDay);
    Store.set(`gt|${gtDay}`, d);
    if (currentView !== "gradett") return;
    gtData = d;
    $("#gt-day-label").textContent = d.day;
    renderGradett();
  } catch (e) {
    if (currentView === "gradett" && !c) {
      $("#gt-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
  }
}
function renderGradett() {
  if (!gtData) return;
  const f = ($("#gt-filter").value || "").trim().toLowerCase();
  const slots = (gtData.slots || [])
    .map((slot) => ({
      time: slot.time,
      lessons: slot.lessons.filter((l) => !f ||
        [l.subject, l.teacher, l.room, l.groups, (l.classes || []).join(" ")]
          .join(" ").toLowerCase().includes(f)),
    }))
    .filter((s) => s.lessons.length);
  $("#gt-meta").textContent = `${gtData.count} 张课卡 · ${gtData.slots.length} 个时段`;
  $("#gt-body").innerHTML = slots.map((s) => `
    <div class="gt-slot"><div class="gt-time">${esc(s.time)}</div>
      <div class="gt-lessons">${s.lessons.map((l) => `
        <div class="gt-lesson ${l.cancelled ? "cancelled" : ""}">
          <b>${esc(l.subject)}</b><span>${esc(l.teacher)}</span>
          <span class="rm">${esc(l.room)}${l.groups ? " · 组 " + esc(l.groups) : ""}</span>
          <span class="cls">${esc((l.classes || []).join(" / "))}</span>
        </div>`).join("")}</div></div>`).join("") ||
    `<div class="empty">没有匹配的课(或当天无课)</div>`;
}
$("#gt-prev").onclick = () => gtShift(-1);
$("#gt-next").onclick = () => gtShift(1);
$("#gt-today").onclick = () => {
  gtDay = new Date().toISOString().slice(0, 10);
  loadGradett().catch((e) => toast(e.message));
};
$("#gt-filter").addEventListener("input", renderGradett);

/* ================= 我的课程 ================= */
/* 布局: 顶上 CAS/EE 两张 IB Core 卡, 左栏最近 DDL(作业), 右栏课程列表
   (课程+总评合并一行, ▲▼ 排序)。点课程行弹课程详情(作业/单元/文件/
   日历), 点作业行弹作业详情。 */
let coData = null;   /* courses_data 引用(箭头排序后重渲染共用) */
const CORE_URLS = {
  cas: "https://shph.managebac.cn/student/ib/activity/cas",
  ee: "https://shph.managebac.cn/student/ib/pbl/778",
};

function renderCourses(d) {
  coData = d;
  const classRows = (d.classes || []).map((c) => `
    <div class="item course-row" data-cid="${esc(c.id)}">
      <span class="move-btns"><button class="move-btn" data-move="up" title="上移">▲</button><button class="move-btn" data-move="down" title="下移">▼</button></span>
      <span class="grow"><span class="co-name">${esc(c.name)}</span>
      <small>总评 ${c.grade ? esc(c.grade) : "未出分"} · 点击看课程详情</small></span>
      ${badge(c.grade || "未出分", c.grade ? "green" : "")}
    </div>`).join("");
  $("#co-classes").innerHTML = classRows ||
    `<div class="empty">还没有课程数据, 点右上角同步</div>`;
  bindCourseList(d);
  renderCourseTasks(d);
  $("#co-link").href = "https://shph.managebac.cn/student";
}
function bindCourseList(d) {
  const rows = () => $$("#co-classes .course-row");
  let lastDragAt = 0;   /* 任意行刚拖完的时戳: 拖完行序会变, 点击抑制要全局 */
  const persistOrder = () => {
    const order = rows().map((r) => r.dataset.cid);
    d.classes.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
    call("course_save_order", JSON.stringify(order)).catch(() => {});
    Store.set("courses", d);   /* 本地缓存同步新顺序 */
  };
  $$("#co-classes .course-row").forEach((row) => {
    row.onclick = (e) => {
      if (e.target.closest("button")) return;
      if (Date.now() - lastDragAt < 500) return;   /* 刚拖完不弹卡 */
      const c = (d.classes || []).find((x) => x.id === row.dataset.cid);
      if (c) openCourseModal(c);
    };
  });
  /* 拖拽排序(纵向指针版): 按住行上下拖, 越过相邻行中线就互换;
     被换位的行用 FLIP 动画平滑滑动, 被拖行深色提示; 与 ▲▼ 共用 persistOrder。 */
  const flip = (el, mutate) => {
    const r1 = el.getBoundingClientRect();
    mutate();
    const r2 = el.getBoundingClientRect();
    const dy = r1.top - r2.top;
    if (Math.abs(dy) < 1) return;
    el.style.transition = "none";
    el.style.transform = `translateY(${dy}px)`;
    requestAnimationFrame(() => {
      el.style.transition = "transform .2s cubic-bezier(.2,.7,.3,1)";
      el.style.transform = "";
      setTimeout(() => { el.style.transition = ""; }, 260);
    });
  };
  $$("#co-classes .course-row").forEach((row) => {
    let drag = null;   // {y0, moved, id}
    row.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      if (e.target.closest("button")) return;
      drag = { y0: e.clientY, moved: false, id: e.pointerId };
      row.setPointerCapture(e.pointerId);
    });
    row.addEventListener("pointermove", (e) => {
      if (!drag || drag.id !== e.pointerId) return;
      const dy = e.clientY - drag.y0;
      if (!drag.moved) {
        if (Math.abs(dy) < 6) return;
        drag.moved = true;
        row.classList.add("dragging");
      }
      row.style.transform = `translateY(${dy}px)`;
      let other = dy < 0 ? row.previousElementSibling : row.nextElementSibling;
      while (other && !other.dataset.cid) {
        other = dy < 0 ? other.previousElementSibling : other.nextElementSibling;
      }
      if (!other) return;
      const r = other.getBoundingClientRect();
      const mid = r.top + r.height / 2;
      if (dy < 0 && e.clientY < mid) {
        flip(other, () => row.parentNode.insertBefore(row, other));
        drag.y0 = e.clientY;
        row.style.transform = "";
      } else if (dy > 0 && e.clientY > mid) {
        flip(other, () => row.parentNode.insertBefore(other, row));
        drag.y0 = e.clientY;
        row.style.transform = "";
      }
    });
    const finish = (e) => {
      if (!drag || drag.id !== e.pointerId) return;
      const moved = drag.moved;
      drag = null;
      if (moved) {
        lastDragAt = Date.now();
        persistOrder();
        /* 被拖行缓动归位, 归位后再摘掉深色提示 */
        row.style.transition = "transform .2s cubic-bezier(.2,.7,.3,1)";
        row.style.transform = "";
        setTimeout(() => {
          row.classList.remove("dragging");
          row.style.transition = "";
        }, 220);
      } else {
        row.classList.remove("dragging");
      }
    };
    row.addEventListener("pointerup", finish);
    row.addEventListener("pointercancel", (e) => {
      if (drag && drag.id === e.pointerId) {
        row.style.transform = "";
        row.classList.remove("dragging");
        drag = null;
      }
    });
  });
  $$("#co-classes .course-row .move-btn").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const row = btn.closest(".course-row");
      const down = btn.dataset.move === "down";
      let el = down ? row.nextElementSibling : row.previousElementSibling;
      while (el && !el.dataset.cid) {
        el = down ? el.nextElementSibling : el.previousElementSibling;
      }
      if (!el) return;
      el.parentNode.insertBefore(row, down ? el.nextElementSibling : el);
      persistOrder();
    };
  });
  $$(".core-card").forEach((card) => {
    card.onclick = () => openCoreModal(card.dataset.core);
  });
}
function renderCourseTasks(d) {
  const tasks = d.tasks_upcoming || [];
  const box = $("#co-tasks");
  box.innerHTML = "";
  if (!tasks.length) {
    box.innerHTML = `<div class="empty">没有未截止的作业 (左滑可移除, ▲▼ 可调顺序, 点击看详情)</div>`;
    return;
  }
  const frag = document.createDocumentFragment();
  tasks.forEach((t) => {
    const el = taskItemEl(t);
    el.querySelectorAll(".move-btn").forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        moveTask(d, tasks, t, btn.dataset.move === "down" ? 1 : -1);
      };
    });
    el.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      if (Date.now() - (el._swipedAt || 0) < 500) return;   /* 刚左滑完不弹卡 */
      openTaskModal(t);
    });
    frag.appendChild(el);
  });
  box.appendChild(frag);
}
function taskItemEl(t) {
  return swipeableItemEl("item ddl-item",
    `<span class="move-btns"><button class="move-btn" data-move="up" title="上移">▲</button><button class="move-btn" data-move="down" title="下移">▼</button></span>
    <span class="dim">${esc((t.due_at || "").slice(5, 16))}</span>
    <span class="grow">${esc(t.title)}<span class="dim"> · ${esc(t.class_name)}</span></span>
    ${badge(t.status || "?", t.status === "Pending" ? "red" : "green")}`,
    async () => {
      await call("ddl_dismiss", `${t.title}|${t.due_at || ""}`);
      Store.drop("courses"); Store.drop("home");
      toast("已移出待办, 可在设置里恢复");
    });
}
const loadCourses = swr("courses", TTL.courses,
  () => call("courses_data"), renderCourses,
  () => {
    $("#co-classes").innerHTML = `<div class="empty">⏳ 正在同步 ManageBac…</div>`;
    $("#co-tasks").innerHTML = "";
  },
  () => currentView === "courses");
/* ▲▼ 移动作业条目: 在当前可见列表内互换位置; 持久化顺序 = 可见列表
   的新顺序在前 + 未显示的任务按原相对顺序排在后面(新作业按截止时间
   排在最后)。key 与已移除 DDL 同款: title|due_at */
function moveTask(d, visible, t, dir) {
  const i = visible.indexOf(t);
  const j = i + dir;
  if (i < 0 || j < 0 || j >= visible.length) return;
  [visible[i], visible[j]] = [visible[j], visible[i]];
  const key = (x) => `${x.title}|${x.due_at || ""}`;
  const visKeys = visible.map(key);
  const rest = (d.tasks_upcoming || []).filter((x) => !visKeys.includes(key(x)));
  d.tasks_upcoming = [...visible, ...rest];
  call("task_save_order", JSON.stringify([...visKeys, ...rest.map(key)])).catch(() => {});
  Store.set("courses", d);   /* 本地缓存同步, 秒开不回跳旧顺序 */
  renderCourseTasks(d);
}
$("#co-refresh").onclick = async () => {
  toast("正在同步 ManageBac…");
  try {
    await call("refresh_tasks");
    Store.drop("courses"); Store.drop("home");
    toast("同步完成");
    show("courses");
  } catch (e) { toast(e.message); }
};

/* ---------------- 课程详情弹卡(仿 ManageBac: 作业/单元/文件/日历) ---------------- */
let cdState = { cid: null, name: "", grade: null, tab: "tasks" };
function openCourseModal(c) {
  cdState = { cid: c.id, name: c.name, grade: c.grade, tab: "tasks" };
  $("#cd-title").textContent = c.name || "课程";
  $("#cd-grade").textContent = c.grade || "未出分";
  $("#cd-modal").classList.remove("hidden");
  setCourseTab("tasks");
}
function setCourseTab(tab) {
  cdState.tab = tab;
  $$("#cd-modal .tabbtn").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  const body = $("#cd-body");
  body.innerHTML = `<div class="empty">加载中…</div>`;
  const cid = cdState.cid;
  const stale = () => cdState.cid !== cid || cdState.tab !== tab;
  if (tab === "tasks") {
    call("course_tasks", cid).then((r) => {
      if (stale()) return;
      const tasks = r.tasks || [];
      if (!tasks.length) { body.innerHTML = `<div class="empty">这门课没有作业卡</div>`; return; }
      body.innerHTML = "";
      tasks.forEach((t) => {
        const el = document.createElement("div");
        el.className = "item clickable";
        el.innerHTML = `<span class="dim">${esc((t.due_at || "").slice(5, 16))}</span>
          <span class="grow">${esc(t.title)}<span class="dim"> · ${esc(t.status || "?")}</span></span>
          ${badge(t.status || "?", t.status === "Pending" ? "red" : "green")}`;
        el.onclick = () => openTaskModal(t);
        body.appendChild(el);
      });
    }).catch((e) => { if (!stale()) body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  } else if (tab === "units") {
    call("course_units", cid).then((r) => {
      if (stale()) return;
      body.innerHTML = r.empty
        ? `<div class="empty">这门课还没有单元内容</div>`
        : `<div class="td-desc">${esc(r.text)}</div>`;
    }).catch((e) => { if (!stale()) body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  } else if (tab === "files") {
    call("course_files", cid).then((r) => {
      if (stale()) return;
      const files = r.files || [];
      if (!files.length) { body.innerHTML = `<div class="empty">这门课还没有共享文件</div>`; return; }
      body.innerHTML = "";
      files.forEach((f) => {
        const el = document.createElement("div");
        el.className = "item clickable file-row";
        el.innerHTML = `<span class="grow">${esc(f.name)}<span class="dim">${esc(f.meta || "")}</span></span>
          <button class="ghost">下载 ↗</button>`;
        el.querySelector("button").onclick = (e) => {
          e.stopPropagation();
          if (!f.url) { toast("这个文件没有下载链接"); return; }
          call("open_external", f.url).then(() => toast("已在浏览器打开下载")).catch((er) => toast(er.message));
        };
        body.appendChild(el);
      });
    }).catch((e) => { if (!stale()) body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  } else if (tab === "events") {
    call("course_events", cid).then((r) => {
      if (stale()) return;
      const evs = (r.events || []).slice(0, 80);
      if (!evs.length) { body.innerHTML = `<div class="empty">这门课的日历没有日程</div>`; return; }
      const when = (ev) => String(ev.starts_at || ev.start || ev.date || ev.due_at || "");
      evs.sort((a, b) => when(a).localeCompare(when(b)));
      body.innerHTML = "";
      evs.forEach((ev) => {
        const el = document.createElement("div");
        el.className = "item";
        el.innerHTML = `<span class="dim">${esc(when(ev).slice(0, 16).replace("T", " "))}</span>
          <span class="grow">${esc(String(ev.title || ev.name || ev.summary || "未命名"))}</span>`;
        body.appendChild(el);
      });
    }).catch((e) => { if (!stale()) body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  } else if (tab === "disc") {
    call("course_discussions", cid).then((r) => {
      if (stale()) return;
      const list = r.discussions || [];
      if (!list.length) { body.innerHTML = `<div class="empty">这门课还没有讨论</div>`; return; }
      body.innerHTML = "";
      list.forEach((d) => {
        const el = document.createElement("div");
        el.className = "item clickable";
        el.innerHTML = `<span class="grow">${esc(d.title)}
            <span class="dim">${esc(d.author || "")}${d.category ? " · " + esc(d.category) : ""}</span></span>
          ${d.preview ? `<span class="dim small">${esc(d.preview.slice(0, 60))}…</span>` : ""}`;
        el.onclick = () => openDiscussion(cid, d.id, d.title);
        body.appendChild(el);
      });
    }).catch((e) => { if (!stale()) body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  }
}
function escHtmlBody(html) {
  /* ManageBac 帖子正文是服务端渲染的 HTML(Redactor), 基本可信;
     但仍去掉 script/iframe/事件属性以防万一 */
  const div = document.createElement("div");
  div.innerHTML = html || "";
  div.querySelectorAll("script,iframe,object,embed").forEach((n) => n.remove());
  div.querySelectorAll("*").forEach((n) => {
    [...n.attributes].forEach((a) => { if (a.name.startsWith("on")) n.removeAttribute(a.name); });
  });
  return div.innerHTML;
}
function openDiscussion(cid, did, title) {
  const body = $("#cd-body");
  const stale = () => cdState.cid !== cid || cdState.tab !== "disc";
  body.innerHTML = `<div class="empty">⏳ 正在加载讨论…</div>`;
  call("discussion_detail", cid, did).then((r) => {
    if (stale()) return;
    const d = r.discussion || {};
    const main = d.main || {};
    const comments = d.comments || [];
    const attList = (atts) => (atts || []).map((a) => `<span class="rc-item">📎 ${esc(a)}</span>`).join("");
    body.innerHTML = `
      <div class="disc-thread">
        <h3 style="margin:4px 0 10px">${esc(title || d.title || "讨论")}</h3>
        <div class="disc-post">
          <div class="muted small">${esc(main.author || "")}${main.category ? " · " + esc(main.category) : ""}${main.date ? " · " + esc(main.date) : ""}</div>
          <div class="disc-body">${escHtmlBody(main.body_html)}</div>
          ${main.attachments && main.attachments.length ? `<div class="att-bar">${attList(main.attachments)}</div>` : ""}
        </div>
        <div class="disc-comments">
          <b class="small">💬 评论 (${comments.length})</b>
          ${comments.map((c) => `
            <div class="disc-post${c.private ? " disc-private" : ""}">
              <div class="muted small">${esc(c.author || "")}${c.private ? " · 🔒 私密" : ""}${c.date ? " · " + esc(c.date) : ""}</div>
              <div class="disc-body">${escHtmlBody(c.body_html)}</div>
            </div>`).join("") || `<div class="empty">还没有评论</div>`}
        </div>
        <div class="disc-reply">
          <textarea id="disc-reply-text" rows="4" placeholder="写回复… (Ctrl+Enter 发送)"></textarea>
          <label class="small muted"><input type="checkbox" id="disc-reply-private"> 私密评论(仅老师可见)</label>
          <button class="primary" id="disc-reply-send">发送回复</button>
        </div>
      </div>`;
    const send = $("#disc-reply-send");
    send.onclick = async () => {
      const txt = $("#disc-reply-text").value.trim();
      if (!txt) { toast("回复内容不能为空"); return; }
      const priv = $("#disc-reply-private").checked;
      send.disabled = true; send.textContent = "发送中…";
      try {
        await call("discussion_reply", cid, did, esc(txt).replace(/\n/g, "<br>"), priv);
        toast("回复已发布 ✅");
        openDiscussion(cid, did, title);
      } catch (e) { toast(e.message); send.disabled = false; send.textContent = "发送回复"; }
    };
    $("#disc-reply-text").addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") send.click();
    });
  }).catch((e) => { if (!stale()) body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
}
$$("#cd-modal .tabbtn").forEach((b) => { b.onclick = () => setCourseTab(b.dataset.tab); });
$("#cd-close").onclick = () => $("#cd-modal").classList.add("hidden");
$("#cd-open-mb").onclick = () => {
  if (cdState.cid) {
    call("open_external", `https://shph.managebac.cn/student/classes/${cdState.cid}`)
      .catch((e) => toast(e.message));
  }
};

/* ---------------- 作业/考试详情弹卡 ---------------- */
let tdTask = null;   /* 当前弹卡对应的作业(提交按钮要用) */
function openTaskModal(t) {
  tdTask = t;
  $("#td-title").textContent = t.title || "作业";
  $("#td-course").textContent = t.class_name || "";
  $("#td-due").textContent = `${(t.due_at || "").slice(0, 16).replace("T", " ")}${t.past_due ? " (已截止)" : ""}`;
  $("#td-status").textContent = t.status || "—";
  $("#td-status").style.display = t.status ? "" : "none";
  $("#td-category").textContent = ""; $("#td-category").style.display = "none";
  $("#td-kind").textContent = ""; $("#td-kind").style.display = "none";
  $("#td-score-row").classList.add("hidden");
  $("#td-dropbox-row").classList.add("hidden");
  $("#td-desc").textContent = "加载详情中…";
  $("#td-modal").classList.remove("hidden");
  $("#td-open-mb").onclick = () => {
    call("open_external",
      `https://shph.managebac.cn/student/classes/${t.class_id}/core_tasks/${t.task_id}`)
      .catch((e) => toast(e.message));
  };
  call("task_detail", t.class_id, t.task_id).then((d) => {
    if (d.title) $("#td-title").textContent = d.title;
    $("#td-category").textContent = d.category || "";
    $("#td-category").style.display = d.category ? "" : "none";
    $("#td-kind").textContent = d.kind || "";
    $("#td-kind").style.display = d.kind ? "" : "none";
    $("#td-status").textContent = d.status || t.status || "—";
    if (d.due_text) $("#td-due").textContent = `${d.due_text}${d.past_due ? " (已截止)" : ""}`;
    if (d.score) {
      $("#td-score").textContent = d.score;
      $("#td-score-row").classList.remove("hidden");
    }
    if (d.dropbox) {
      $("#td-dropbox").textContent = d.dropbox;
      $("#td-dropbox-row").classList.remove("hidden");
    }
    $("#td-desc").textContent = d.description || "(这个作业没有详细说明)";
  }).catch((e) => { $("#td-desc").textContent = `详情加载失败: ${e.message}`; });
}
$("#td-close").onclick = () => $("#td-modal").classList.add("hidden");
$("#td-close2").onclick = () => $("#td-modal").classList.add("hidden");
/* 提交作业: 系统文件选择框 → 动态解析提交入口上传 */
$("#td-submit").onclick = async () => {
  if (!tdTask || $("#td-submit").disabled) return;
  $("#td-submit").disabled = true;
  toast("请在弹出的窗口里选择要提交的文件…");
  try {
    const r = await call("task_pick_and_submit", tdTask.class_id, tdTask.task_id);
    if (r.cancelled) { toast("已取消提交"); return; }
    toast(r.message || "已提交, 请到 ManageBac 网页确认");
    $("#td-dropbox").textContent = r.message || "已提交";
    $("#td-dropbox-row").classList.remove("hidden");
  } catch (e) {
    toast(e.message);
  } finally {
    $("#td-submit").disabled = false;
  }
};

/* ---------------- CAS / EE 弹卡 ---------------- */
const CORE_TITLES = {
  cas: "🎨 CAS 创意 · 行动 · 服务",
  ee: "📄 EE 拓展论文",
};
async function openCoreModal(kind) {
  $("#core-title").textContent = CORE_TITLES[kind] || "IB Core";
  const body = $("#core-body");
  body.innerHTML = `<div class="empty">加载中…</div>`;
  $("#core-modal").classList.remove("hidden");
  $("#core-open-mb").onclick = () => {
    call("open_external", CORE_URLS[kind]).catch((e) => toast(e.message));
  };
  try {
    const d = await call(kind === "cas" ? "cas_overview" : "ee_overview");
    body.innerHTML = "";
    const secs = d.sections || [];
    if (!secs.length) {
      body.innerHTML = `<div class="empty">ManageBac 上还没有内容, 点下方按钮去网页查看</div>`;
      return;
    }
    /* 块级布局: 标题一行、正文一块(行内并排会叠字, 勿改回 span 嵌套) */
    secs.forEach((s) => {
      const el = document.createElement("div");
      el.className = "core-sec";
      el.innerHTML = `<b>${esc(s.h)}</b><div class="td-desc">${esc(s.text)}</div>`;
      body.appendChild(el);
    });
  } catch (e) {
    body.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
$("#core-close").onclick = () => $("#core-modal").classList.add("hidden");
$("#core-close2").onclick = () => $("#core-modal").classList.add("hidden");
/* 点遮罩 / Escape 关闭新弹卡 */
["cd-modal", "td-modal", "core-modal"].forEach((id) => {
  $("#" + id).addEventListener("click", (e) => {
    if (e.target.id === id) $("#" + id).classList.add("hidden");
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    ["cd-modal", "td-modal", "core-modal"].forEach((id) => $("#" + id).classList.add("hidden"));
  }
});

/* ================= 邮箱 ================= */
async function loadMail() { await fetchMail(); }
function renderMail(d) {
  $("#ml-list").innerHTML = (d.mails || []).map((m) => `
    <div class="mail-item ${m.seen ? "" : "unread"}" data-uid="${m.uid}">
      <div class="subj">${m.seen ? "" : "🔵 "}${esc(m.subject)}</div>
      <div class="meta">${esc(m.from)} · ${esc(m.date)}</div>
    </div>`).join("") || `<div class="empty">没有邮件</div>`;
  $$("#ml-list .mail-item").forEach((el) => {
    el.onclick = async () => {
      try {
        $("#ml-read").innerHTML = `<div class="empty">⏳ 正在读取邮件…</div>`;
        const m = await call("mail_read", el.dataset.uid);
        const bodyHtml = m.is_html ? m.body : `<pre class="mail-plain">${esc(m.body)}</pre>`;
        /* 收件人折叠: 超过 5 个时显示前 5 个 + 展开按钮 */
        const tos = (m.to || "").split(/[,;]\s*/).filter(Boolean);
        const ccs = (m.cc || "").split(/[,;]\s*/).filter(Boolean);
        const allRc = [...tos.map(x => `To: ${x}`), ...ccs.map(x => `Cc: ${x}`)];
        let rcHtml = allRc.map(x => `<span class="rc-item">${esc(x)}</span>`).join("");
        if (allRc.length > 5) {
          rcHtml = allRc.slice(0, 5).map(x => `<span class="rc-item">${esc(x)}</span>`).join("")
            + ` <button class="ghost rc-more" style="font-size:11px">…展开(${allRc.length})</button>`
            + `<span class="rc-rest hidden">${allRc.slice(5).map(x => `<span class="rc-item">${esc(x)}</span>`).join("")}</span>`;
        }
        const rcSection = allRc.length > 0
          ? `<div class="rc-bar">${rcHtml}</div>` : "";
        /* 附件 */
        let attHtml = "";
        if ((m.attachments || []).length) {
          attHtml = `<div class="att-bar"><b>📎 附件 (${m.attachments.length})</b>` +
            m.attachments.map((a, i) =>
              `<button class="ghost att-dl" data-uid="${esc(m.uid)}" data-idx="${i}" data-name="${esc(a.filename)}">` +
              `📄 ${esc(a.filename)} (${Math.max(1, Math.round(a.size / 1024))}KB)</button>`).join("") +
            `</div>`;
        }
        $("#ml-read").innerHTML = `
          <h3>${esc(m.subject)}</h3>
          <div class="muted small">${esc(m.from)} · ${esc(m.date)}</div>
          ${rcSection}
          ${attHtml}
          <hr><div class="mail-body">${bodyHtml}</div>`;
        el.classList.remove("unread");
        /* 收件人展开 */
        const moreBtn = $("#ml-read .rc-more");
        if (moreBtn) moreBtn.onclick = () => {
          $("#ml-read .rc-rest").classList.remove("hidden");
          moreBtn.classList.add("hidden");
        };
        /* 附件下载 */
        $$("#ml-read .att-dl").forEach((btn) => {
          btn.onclick = async () => {
            btn.disabled = true; btn.textContent = "下载中…";
            try {
              const r = await call("mail_download_attachment", btn.dataset.uid, btn.dataset.idx, btn.dataset.name);
              toast(`已保存: ${r.path}`);
            } catch (e) { toast(e.message); }
            btn.disabled = false;
          };
        });
      } catch (e) { toast(e.message); }
    };
  });
}
async function fetchMail() {
  const key = `mail|${mailMode}|40`;
  return swr(key, TTL.mail,
    () => call("mail_list", mailMode === 1, 40), renderMail,
    () => { $("#ml-list").innerHTML = `<div class="empty">⏳ 正在连接邮箱…</div>`; },
    () => currentView === "mail")();
}
$("#ml-unseen").onclick = () => { mailMode = 1; fetchMail().catch((e) => toast(e.message)); };
$("#ml-all").onclick = () => { mailMode = 0; fetchMail().catch((e) => toast(e.message)); };
/* ---- 通讯录自动补全 (输入名字/邮箱片段 → 匹配收件人) ---- */
let mlContacts = null;   /* null = 尚未加载 */
const acState = { items: [], idx: -1 };

async function loadMlContacts(force) {
  if (mlContacts && !force) return;
  try {
    const d = await call("mail_contacts", !!force);
    mlContacts = d.contacts || [];
  } catch (e) { mlContacts = mlContacts || []; }
}

function acTokens() {
  return $("#ml-to").value.split(/[,;，；]/);
}
function acSetToken(tok) {
  const parts = acTokens();
  parts[parts.length - 1] = tok;
  $("#ml-to").value = parts.join(", ");
  $("#ml-to").focus();
}
function acClose() {
  $("#ml-ac").classList.add("hidden");
  acState.items = []; acState.idx = -1;
}
function acRender() {
  const q = (acTokens().pop() || "").trim().toLowerCase();
  const box = $("#ml-ac");
  if (!q || !mlContacts) { acClose(); return; }
  acState.items = mlContacts.filter((c) =>
    (c.name || "").toLowerCase().includes(q) ||
    (c.email || "").toLowerCase().includes(q)).slice(0, 6);
  if (!acState.items.length) { acClose(); return; }
  acState.idx = Math.min(Math.max(acState.idx, -1), acState.items.length - 1);
  box.innerHTML = acState.items.map((c, i) =>
    `<div class="ac-item ${i === acState.idx ? "on" : ""}" data-i="${i}">
      <b>${esc(c.name || "(无名)")}</b>
      <span class="ac-mail">${esc(c.email)}</span></div>`).join("");
  box.classList.remove("hidden");
  $$("#ml-ac .ac-item").forEach((el) => {
    el.onclick = () => { acSetToken(acState.items[+el.dataset.i].email); acClose(); };
  });
}
$("#ml-to").addEventListener("input", () => { acState.idx = -1; acRender(); });
$("#ml-to").addEventListener("keydown", (e) => {
  if ($("#ml-ac").classList.contains("hidden")) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const n = acState.items.length;
    acState.idx = e.key === "ArrowDown"
      ? (acState.idx + 1) % n : (acState.idx <= 0 ? n - 1 : acState.idx - 1);
    acRender();
  } else if (e.key === "Enter") {
    e.preventDefault();
    const pick = acState.items[Math.max(acState.idx, 0)];
    if (pick) { acSetToken(pick.email); acClose(); }
  } else if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();   /* 只收起下拉, 不关掉整个弹卡 */
    acClose();
  }
});
$("#ml-compose").onclick = async () => {
  $("#ml-msg").textContent = "";
  $("#ml-modal").classList.remove("hidden");
  $("#ml-body").focus();
  loadMlContacts();   /* 通讯录未加载则后台拉取(磁盘缓存 24h) */
};
$("#ml-cancel").onclick = () => { acClose(); $("#ml-modal").classList.add("hidden"); };

/* ---- 通讯录管理 (查看 / 添加 / 修改 / 删除) ---- */
let ctContacts = [];
let ctEditing = null;   /* 正在修改的旧邮箱 */

function renderContacts() {
  const q = ($("#ct-search").value || "").trim().toLowerCase();
  const list = ctContacts.filter((c) =>
    !q || (c.name || "").toLowerCase().includes(q) ||
    (c.email || "").toLowerCase().includes(q));
  $("#ct-count").textContent = `${list.length} / ${ctContacts.length} 位联系人`;
  $("#ct-list").innerHTML = list.map((c) => `
    <div class="ct-item" data-email="${esc(c.email)}">
      <span class="grow" style="display:flex;gap:10px;align-items:center;overflow:hidden">
        <span class="ct-name">${esc(c.name || "(无名)")}</span>
        <span class="ct-mail">${esc(c.email)}</span>
      </span>
      ${c.custom ? `<span class="badge green">自建</span>` :
        (c.count ? `<span class="muted small">×${c.count}</span>` : "")}
      <button class="ghost" data-edit="${esc(c.email)}" title="修改">✏</button>
      <button class="ghost" data-del="${esc(c.email)}" title="删除">🗑</button>
    </div>`).join("") || `<div class="empty">没有匹配的联系人</div>`;
  $$("#ct-list [data-edit]").forEach((b) => {
    b.onclick = () => {
      const c = ctContacts.find((x) => x.email === b.dataset.edit);
      if (!c) return;
      ctEditing = c.email;
      $("#ct-name").value = c.name || "";
      $("#ct-email").value = c.email;
      $("#ct-add").textContent = "保存修改";
      $("#ct-msg").textContent = `正在修改 ${c.email}, 改完点"保存修改"`;
      $("#ct-name").focus();
    };
  });
  $$("#ct-list [data-del]").forEach((b) => {
    b.onclick = async () => {
      try {
        const d = await call("mail_contact_delete", b.dataset.del);
        ctContacts = d.contacts || [];
        mlContacts = ctContacts;
        if (ctEditing === b.dataset.del) ctEditing = null;
        $("#ct-msg").textContent = "已删除";
        renderContacts();
      } catch (e) { $("#ct-msg").textContent = e.message; }
    };
  });
}
function ctResetForm() {
  ctEditing = null;
  $("#ct-name").value = "";
  $("#ct-email").value = "";
  $("#ct-add").textContent = "＋ 添加";
}
$("#ml-contacts").onclick = async () => {
  $("#ct-modal").classList.remove("hidden");
  $("#ct-msg").textContent = "";
  ctResetForm();
  if (mlContacts) {
    ctContacts = mlContacts;
    renderContacts();
  } else {
    $("#ct-list").innerHTML = `<div class="empty">⏳ 正在读取通讯录…</div>`;
    await loadMlContacts();
    ctContacts = mlContacts || [];
    renderContacts();
  }
};
$("#ct-close").onclick = () => $("#ct-modal").classList.add("hidden");
$("#ct-modal").addEventListener("click", (e) => {
  if (e.target === $("#ct-modal")) $("#ct-modal").classList.add("hidden");
});
$("#ct-search").addEventListener("input", renderContacts);
$("#ct-add").onclick = async () => {
  const name = $("#ct-name").value.trim();
  const email = $("#ct-email").value.trim();
  const msg = $("#ct-msg");
  msg.textContent = "";
  try {
    let d;
    if (ctEditing) {
      d = await call("mail_contact_update", ctEditing, name, email);
      msg.textContent = "已保存修改";
    } else {
      d = await call("mail_contact_add", name, email);
      msg.textContent = "已添加";
    }
    ctResetForm();
    ctContacts = d.contacts || [];
    mlContacts = ctContacts;
    renderContacts();
  } catch (e) { msg.textContent = e.message; }
};
$("#ml-modal").addEventListener("click", (e) => {
  if (e.target === $("#ml-modal")) { acClose(); $("#ml-modal").classList.add("hidden"); }
});
$("#ml-send").onclick = async () => {
  const btn = $("#ml-send");
  btn.disabled = true;
  $("#ml-msg").textContent = "正在发送…";
  try {
    await call("mail_send", $("#ml-to").value, $("#ml-subject").value, $("#ml-body").value);
    Store.drop("mail|"); Store.drop("home");
    toast("已发送");
    $("#ml-modal").classList.add("hidden");
    $("#ml-to").value = ""; $("#ml-subject").value = ""; $("#ml-body").value = "";
    fetchMail().catch(() => {});
  } catch (e) { $("#ml-msg").textContent = `✗ ${e.message}`; }
  btn.disabled = false;
};

/* ================= Agent ================= */
let agentStreamingEl = null;
let agentStreamed = false;
let lastToolCard = null;

function scrollChat() {
  const el = $("#ag-chat");
  el.scrollTop = el.scrollHeight;
}
function addBubble(text, who) {
  const div = document.createElement("div");
  div.className = `bubble ${who}`;
  div.textContent = text;
  $("#ag-chat").appendChild(div);
  scrollChat();
  return div;
}
function addToolCard(name) {
  lastToolCard = document.createElement("div");
  lastToolCard.className = "tool-card";
  lastToolCard.innerHTML =
    `<div class="tc-head"><span class="tc-status">⏳</span><span>🔧 ${esc(name)}</span></div>` +
    `<pre class="tc-body hidden"></pre>`;
  lastToolCard.querySelector(".tc-head").onclick = () =>
    lastToolCard.querySelector(".tc-body").classList.toggle("hidden");
  $("#ag-chat").appendChild(lastToolCard);
  scrollChat();
  return lastToolCard;
}
function updateToolCard(name, preview) {
  if (!lastToolCard) return;
  const st = lastToolCard.querySelector(".tc-status");
  if (st) st.textContent = "✓";
  const body = lastToolCard.querySelector(".tc-body");
  if (body) body.textContent = preview || "(无输出)";
}
function mdToHtml(s) {
  let t = esc(s ?? "");
  t = t.replace(/```([\s\S]*?)```/g, (m, c) => `<pre class="mdcode">${c.trim()}</pre>`);
  t = t.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  t = t.replace(/^### (.*)$/gm, "<h4>$1</h4>");
  t = t.replace(/^## (.*)$/gm, "<h3>$1</h3>");
  t = t.replace(/^- (.*)$/gm, "<li>$1</li>");
  t = t.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, "<ul>$1</ul>");
  return t;
}

/* harness 式流式事件: python 侧 evaluate_js 推送 */
window.__agentEvent = (e) => {
  if (!e) return;
  if (e.type === "delta") {
    if (!agentStreamingEl) {
      agentStreamingEl = addBubble("", "bot streaming cursor");
      agentStreamed = true;
    }
    agentStreamingEl.textContent += e.text;
    scrollChat();
  } else if (e.type === "tool") {
    agentStreamingEl = null;
    addToolCard(e.name);
  } else if (e.type === "tool_result") {
    updateToolCard(e.name, e.preview);
  } else if (e.type === "proposal") {
    refreshProposals();
  }
};

async function loadAgent() {
  const [d, ai] = await Promise.all([call("agent_state"), call("ai_get")]);
  const wsList = d.workspaces || [];
  const all = d.workspace && !wsList.includes(d.workspace) ? [d.workspace, ...wsList] : wsList;
  $("#ag-ws-select").innerHTML =
    all.map((w) => `<option value="${esc(w)}">${esc(w)}</option>`).join("") ||
    `<option value="">(未设置, 输入名称点"新建")</option>`;
  if (d.workspace) $("#ag-ws-select").value = d.workspace;

  /* 模型下拉: 按提供商分组(参考 DeepSeek Harness) */
  const sel = $("#ag-model-select");
  const groups = (ai.providers || []).filter((p) => (p.models || []).length);
  sel.innerHTML = groups.map((p) =>
    `<optgroup label="${esc(p.name)}">` +
    p.models.map((m) =>
      `<option value="${esc(p.id)}|${esc(m)}" ${
        p.id === ai.active_provider_id && m === ai.active_model ? "selected" : ""}>${esc(m)}</option>`
    ).join("") + `</optgroup>`).join("") ||
    `<option value="">(未配置 AI 供应商, 去设置页添加)</option>`;
  $("#ag-model-now").textContent =
    `当前: ${d.provider.name || "?"} · ${d.provider.model || "?"}` +
    (d.provider.has_key ? "" : " · 未填 API Key");
  sel.onchange = async () => {
    const [pid, model] = sel.value.split("|");
    if (!pid) return;
    try {
      await call("ai_set_active", pid, model);
      $("#ag-model-now").textContent = `已切换: ${model}`;
      toast(`模型已切换: ${model}`);
    } catch (e) { toast(e.message); }
  };

  renderProposals(d.proposals);
  renderModebar(d.mode);
  refreshFiles();
  await refreshSessions();
}
/* ---------------- Agent 权限模式(4 档; 高权限切换需双重确认) ---------------- */
const MODE_DESC = {
  readonly: "当前: 只读 — Agent 只能查询, 所有写操作被禁用",
  confirm: "当前: 操作前确认 — 写操作会先提案, 你确认后才执行",
  workspace_write: "当前: 工作区写入 — workspace 内写文档自动执行, 发邮件/交作业仍需确认",
  full_access: "当前: 完全访问 — 所有写操作立即执行, 不再有确认弹窗(后果自负)",
};
const MODE_WARN1 = {
  workspace_write:
    "⚠️ 即将启用「工作区写入」\n\n" +
    "启用后, Agent 在你的 workspace 里新建/修改文件将不再逐次询问你 — " +
    "文档可能被直接创建或覆盖。\n\n只有 workspace 内的文档操作会自动执行; " +
    "发邮件 / 提交作业等对外操作仍会先征求你同意。",
  full_access:
    "⚠️⚠️ 即将启用「完全访问」— 这是最高风险的模式\n\n" +
    "启用后, Agent 的所有写操作(包括 发邮件、提交作业、新增日程、修改文件)" +
    "都会立即执行, 不再弹出任何确认。\n\n" +
    "发错的邮件、交错的作业都无法由本程序撤回 — 启用即表示你了解风险并自愿承担一切后果。",
};
const MODE_WARN2 = {
  workspace_write:
    "最后确认: 确实要让 Agent 免确认写入你的 workspace 文件吗?\n\n" +
    "(立即生效并保存; 随时可切回「操作前确认」)",
  full_access:
    "最后确认: 确实要授予 Agent 完全访问权限吗?\n\n" +
    "(立即生效并保存, 后果自负; 随时可切回「操作前确认」)",
};
function renderModebar(mode) {
  $$("#ag-modebar .modebtn").forEach((b) => {
    b.classList.toggle("on", b.dataset.mode === mode);
  });
  $("#ag-mode-desc").textContent = MODE_DESC[mode] || "";
}
let pendingMode = null;
function closeModeModal() {
  pendingMode = null;
  delete $("#mode-next").dataset.step;
  $("#mode-next").textContent = "我已了解风险，继续";
  $("#mode-modal").classList.add("hidden");
}
$$("#ag-modebar .modebtn").forEach((btn) => {
  btn.onclick = async () => {
    const mode = btn.dataset.mode;
    if (mode === "workspace_write" || mode === "full_access") {
      pendingMode = mode;
      $("#mode-title").textContent = mode === "full_access"
        ? "⚠️ 启用完全访问(第 1/2 步)" : "⚠️ 启用工作区写入(第 1/2 步)";
      $("#mode-warn").textContent = MODE_WARN1[mode];
      $("#mode-next").textContent = "我已了解风险，继续";
      delete $("#mode-next").dataset.step;
      $("#mode-modal").classList.remove("hidden");
      return;
    }
    try {
      const r = await call("agent_set_mode", mode);
      renderModebar(r.mode);
      toast(`权限模式: ${mode === "readonly" ? "只读" : "操作前确认"}`);
    } catch (e) { toast(e.message); }
  };
});
$("#mode-next").onclick = async () => {
  const mode = pendingMode;
  if (!mode) return;
  if ($("#mode-next").dataset.step !== "2") {
    /* 第一次确认 → 出示第二道警告 */
    $("#mode-title").textContent = mode === "full_access"
      ? "⚠️ 启用完全访问(第 2/2 步)" : "⚠️ 启用工作区写入(第 2/2 步)";
    $("#mode-warn").textContent = MODE_WARN2[mode];
    $("#mode-next").textContent = "确认启用（后果自负）";
    $("#mode-next").dataset.step = "2";
    return;
  }
  try {
    const r = await call("agent_set_mode", mode);
    renderModebar(r.mode);
    toast(`权限模式已切换: ${mode === "full_access" ? "完全访问" : "工作区写入"}`);
  } catch (e) { toast(e.message); }
  closeModeModal();
};
$("#mode-cancel").onclick = closeModeModal;
$("#mode-close").onclick = closeModeModal;
$("#mode-modal").addEventListener("click", (e) => {
  if (e.target.id === "mode-modal") closeModeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#mode-modal").classList.add("hidden");
});
async function refreshSessions() {
  try {
    const r = await call("agent_sessions");
    $("#ag-sessions").innerHTML = (r.sessions || []).map((s) =>
      `<div class="session-item ${s.id === r.current ? "active" : ""}" data-sid="${esc(s.id)}"
        title="${esc(s.title)}">${esc(s.title)}</div>`).join("") ||
      `<div class="empty">暂无历史</div>`;
    $$("#ag-sessions .session-item").forEach((el) => {
      el.onclick = async () => {
        try {
          const r2 = await call("agent_open_session", el.dataset.sid);
          const hist = (r2.history || []).filter(
            (m) => m.role === "user" || m.role === "assistant");
          $("#ag-chat").innerHTML = "";
          for (const m of hist) addBubble(m.content, m.role === "user" ? "user" : "bot");
          renderProposals([]);
          toast(`已切换: ${r2.title || r2.session}`);
        } catch (e) { toast(e.message); }
      };
    });
  } catch (e) { /* 静默 */ }
}
async function refreshProposals() {
  try {
    const r = await call("agent_proposals");
    renderProposals(r.proposals);
  } catch (e) { /* 静默 */ }
}
async function refreshFiles() {
  try {
    const d = await call("agent_files");
    $("#ag-files").innerHTML = (d.files || []).map(
      (f) => `<div class="file-item" title="${esc(f)}">${esc(f)}</div>`).join("") ||
      `<div class="empty">workspace 为空</div>`;
    $$("#ag-files .file-item").forEach((el) => {
      el.onclick = () => {
        $("#ag-input").value = `帮我处理 workspace 里的 ${el.textContent}`;
        $("#ag-input").focus();
      };
    });
  } catch (e) {
    $("#ag-files").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
function renderProposals(list) {
  $("#ag-proposals").innerHTML = (list || []).map((p) => `
    <div class="proposal" data-pid="${p.id}">
      <div class="p-title">📝 提案: ${esc(p.title)}</div>
      <pre>${esc(p.detail)}</pre>
      <button class="primary" data-ok="${p.id}">确认执行</button>
      <button class="danger" data-no="${p.id}">拒绝</button>
    </div>`).join("");
  $$("#ag-proposals [data-ok]").forEach((b) => {
    b.onclick = async () => {
      try {
        const r = await call("agent_confirm", b.dataset.ok);
        toast(`已执行: ${r.title || "完成"}`);
        refreshProposals();
        refreshFiles();
      } catch (e) { toast(e.message); }
    };
  });
  $$("#ag-proposals [data-no]").forEach((b) => {
    b.onclick = async () => {
      await call("agent_reject", b.dataset.no);
      refreshProposals();
    };
  });
}
$("#ag-ws-select").addEventListener("change", async () => {
  try {
    const r = await call("agent_set_workspace", $("#ag-ws-select").value);
    toast(`Workspace: ${r.workspace}`);
    refreshFiles();
  } catch (e) { toast(e.message); }
});
$("#ag-new-ws").onclick = async () => {
  const name = $("#ag-ws-name").value.trim();
  if (!name) { toast("先输入新 workspace 名称"); return; }
  try {
    const r = await call("agent_new_workspace", name);
    $("#ag-ws-name").value = "";
    await loadAgent();
    toast(`已创建: ${r.workspace}`);
  } catch (e) { toast(e.message); }
};
$("#ag-open-ws").onclick = async () => {
  try { await call("agent_open_explorer"); } catch (e) { toast(e.message); }
};
/* 从资源管理器里选任意文件夹作为 workspace */
$("#ag-pick-ws").onclick = async () => {
  try {
    const r = await call("agent_pick_workspace");
    if (r.cancelled) return;
    toast(`Workspace: ${r.workspace}`);
    await loadAgent();
  } catch (e) { toast(e.message); }
};
async function agentSend() {
  const input = $("#ag-input");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  addBubble(msg, "user");
  agentStreamed = false;
  agentStreamingEl = addBubble("…", "bot streaming cursor");
  try {
    const r = await call("agent_chat", msg);
    if (agentStreamingEl) {
      agentStreamingEl.innerHTML = mdToHtml(r.reply || "(无回复)");
      agentStreamingEl.classList.remove("cursor");
    }
    agentStreamingEl = null;
    refreshProposals();
    refreshFiles();
  } catch (e) {
    if (agentStreamingEl) {
      agentStreamingEl.textContent = `出错: ${e.message}`;
      agentStreamingEl.classList.remove("cursor");
    }
    agentStreamingEl = null;
  }
}
$("#ag-send").onclick = agentSend;
$("#ag-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); agentSend(); }
});
$("#ag-reset").onclick = async () => {
  try { await call("agent_new_session"); } catch (e) { /* 忽略 */ }
  $("#ag-chat").innerHTML = `<div class="muted">已开启新会话</div>`;
  renderProposals([]);
  refreshSessions();
};
$("#ag-new-chat").onclick = () => $("#ag-reset").click();

/* ================= 设置 ================= */
const PROVIDER_PRESETS_JS = {
  deepseek: { name: "DeepSeek", protocol: "openai", base_url: "https://api.deepseek.com", models: ["deepseek-chat"] },
  kimi: { name: "Kimi (Moonshot)", protocol: "openai", base_url: "https://api.moonshot.cn/v1", models: ["kimi-k2-turbo-preview"] },
  glm: { name: "GLM (智谱)", protocol: "openai", base_url: "https://open.bigmodel.cn/api/paas/v4", models: ["glm-4-plus"] },
  qwen: { name: "通义千问", protocol: "openai", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", models: ["qwen-plus"] },
  ollama: { name: "Ollama 本地", protocol: "openai", base_url: "http://localhost:11434/v1", models: ["qwen2.5:7b"] },
  custom: { name: "自定义", protocol: "openai", base_url: "", models: [] },
};
let providersState = [];
let selectedLessonsCache = [];

async function loadSettings() {
  const d = await call("settings_get");  $("#st-mb-url").value = d.managebac_base_url || "";
  $("#st-mb-email").value = d.managebac_email || "";
  $("#st-ep-user").value = d.edupage_username || "";
  $("#st-ep-sub").value = d.edupage_subdomain || "";
  $("#st-mail-email").value = d.mail_email || "";
  $("#st-mail-imap").value = d.mail_imap_host || "";
  $("#st-mail-smtp").value = d.mail_smtp_host || "";
  $("#st-mail-authcode").value = "";
  $("#st-grades-llm").checked = !!d.send_grades_to_llm;
  selectedLessonsCache = d.selected_lessons || [];
  selLoaded = true;   // 课表弹卡的"取消选课"直接用, 不用再拉
  $("#st-subjects").innerHTML = selectedLessonsCache.map((s) =>
    `<span class="chip on">${esc(s.subject)}${s.teacher ? " · " + esc(s.teacher) : ""}${s.group ? " · 组" + esc(s.group) : ""}</span>`).join("") ||
    `<span class="muted">尚未选课</span>`;
  await renderDismissed();
  apBindPanel();   /* 外观面板(设置页常驻): 每次进入同步控件值 */
  const ai = await call("ai_get");
  providersState = (ai.providers || []).map((p) => ({ ...p, api_key: "" }));
  renderProviderCards();
  renderActiveSelects(ai.active_provider_id, ai.active_model);
}
/* 已移除的作业: 列表 + 恢复按钮 */
async function renderDismissed() {
  try {
    const d = await call("ddl_dismissed_list");
    const items = d.items || [];
    $("#st-dismissed").innerHTML = items.map((it) => `
      <div class="item">
        <span class="grow">${esc(it.title)}<span class="dim"> · ${esc((it.due_at || "").slice(0, 16))}</span></span>
        <button class="ghost" data-restore="${esc(it.key)}">恢复</button>
      </div>`).join("") || `<div class="empty">没有已移除的作业</div>`;
    $$("#st-dismissed [data-restore]").forEach((b) => {
      b.onclick = async () => {
        try {
          await call("ddl_restore", b.dataset.restore);
          Store.drop("home"); Store.drop("courses");
          toast("已恢复, 首页/课程页会重新显示");
          renderDismissed();
        } catch (e) { toast(e.message); }
      };
    });
  } catch (e) {
    $("#st-dismissed").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
function renderProviderCards() {
  $("#st-providers").innerHTML = providersState.map((p, i) => `
    <div class="card provider-card" data-i="${i}" style="margin-bottom:10px">
      <div class="form-row">
        <input data-f="name" value="${esc(p.name)}" placeholder="提供商名称" class="flex1">
        <select data-f="protocol">
          <option value="openai" ${p.protocol !== "anthropic" ? "selected" : ""}>OpenAI 协议</option>
          <option value="anthropic" ${p.protocol === "anthropic" ? "selected" : ""}>Anthropic 协议</option>
        </select>
        <button class="danger" data-del="${i}">删除</button>
      </div>
      <div class="form-row">
        <input data-f="base_url" value="${esc(p.base_url)}" placeholder="API 地址(留空=提供方默认)" class="flex1">
        <input data-f="api_key" type="password" placeholder="${p.has_key ? "Key 已保存, 留空=不变" : "API 密钥"}" class="flex1">
      </div>
      <div class="muted small">模型目录(用户可增删):</div>
      ${(p.models || []).map((m, mi) => `
        <div class="form-row">
          <input data-model="${mi}" value="${esc(m)}" class="flex1">
          <button class="danger" data-mdel="${mi}">✕</button>
        </div>`).join("")}
      <button class="ghost" data-madd="${i}">＋ 添加模型</button>
    </div>`).join("") || `<div class="empty">还没有提供商, 点下方"添加提供商"</div>`;

  $$("#st-providers [data-f]").forEach((el) => {
    el.addEventListener("change", () => {
      const i = Number(el.closest(".provider-card").dataset.i);
      providersState[i][el.dataset.f] = el.value;
    });
  });
  $$("#st-providers [data-del]").forEach((b) => {
    b.onclick = () => {
      providersState.splice(Number(b.dataset.del), 1);
      renderProviderCards();
      renderActiveSelects();
    };
  });
  $$("#st-providers [data-madd]").forEach((b) => {
    b.onclick = () => {
      providersState[Number(b.dataset.madd)].models.push("");
      renderProviderCards();
    };
  });
  $$("#st-providers [data-model]").forEach((el) => {
    el.addEventListener("change", () => {
      const i = Number(el.closest(".provider-card").dataset.i);
      providersState[i].models[Number(el.dataset.model)] = el.value.trim();
    });
  });
  $$("#st-providers [data-mdel]").forEach((b) => {
    b.onclick = () => {
      const i = Number(b.closest(".provider-card").dataset.i);
      providersState[i].models.splice(Number(b.dataset.mdel), 1);
      renderProviderCards();
    };
  });
}
function renderActiveSelects(activePid, activeModel) {
  const ps = $("#st-active-provider");
  ps.innerHTML = providersState.map((p) =>
    `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("") ||
    `<option value="">(无)</option>`;
  ps.value = activePid || (providersState[0] ? providersState[0].id : "");
  const active = providersState.find((p) => p.id === ps.value);
  const ms = $("#st-active-model");
  ms.innerHTML = ((active && active.models) || []).map(
    (m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  if (activeModel && [...ms.options].some((o) => o.value === activeModel)) ms.value = activeModel;
}
$("#st-add-provider").onclick = () => {
  const preset = PROVIDER_PRESETS_JS[$("#st-add-preset").value];
  providersState.push({
    id: "", name: preset.name, protocol: preset.protocol,
    base_url: preset.base_url, api_key: "", models: [...preset.models], has_key: false,
  });
  renderProviderCards();
};
$("#st-ai-save").onclick = async () => {
  try {
    await call("ai_save_all", JSON.stringify({
      providers: providersState,
      active_provider_id: $("#st-active-provider").value,
      active_model: $("#st-active-model").value,
    }));
    toast("AI 供应商已保存");
    await loadSettings();
  } catch (e) { toast(e.message); }
};

/* ---- 选课渲染(向导 + 设置共用) ----
   选课按"教学组"构建: 一个教学组 = 一个选项, 身份是 (科目族, 组号, 老师)。
   学校课表页每个时段列的就是 组|教室|老师|课名; 组号会跨科目复用
   (Psychology 组F 有两个组), 所以老师参与构成身份; 课名会换
   (History HL/SL2 ↔ History HL2), 所以匹配/勾选一律用科目族。
   多个组时用三角形折叠展开(按科目一层)。 */
const selOpen = new Set();   // 折叠展开状态(重渲染后保持)
function subjFamily(n) {   /* 与后端 subject_family 保持一致 */
  return (n || "").trim().replace(/\s*(HL\s*\/\s*SL|HL|SL)\s*\d?\s*(\([^)]*\))?\s*$/, "$2").trim();
}
function fmtSecTimes(times) {
  return (times || []).map((t) => `${t.day} ${t.start}`).join(" / ");
}
function grpChecked(checkedSet, fam, teacher, group) {
  /* 旧版选课无组字段(fam|teacher|) → 该老师所有组都视为已选 */
  return checkedSet.has(`${fam}|${teacher}|${group}`) ||
    checkedSet.has(`${fam}|${teacher}|`);
}
function grpRowHTML(fam, g, checkedSet, pad, lead, autoCheck) {
  /* 无组 = 全班必修课(班会/语文这类), 人人都有, 锁定为已选不可取消 */
  const whole = !g.group;
  const chk = (whole || autoCheck || grpChecked(checkedSet, fam, g.teacher, g.group))
    ? "checked" : "";
  const label = whole ? "全班必修" : `组${g.group}`;
  const rooms = (g.rooms || []).join(" ");
  return `<label class="subject-row${whole ? " wc" : ""}" style="padding-left:${pad}px">
    <input type="checkbox" data-sub="${esc(g.subject)}" data-teacher="${esc(g.teacher)}"
      data-group="${esc(g.group || "")}" ${chk}${whole ? " disabled" : ""}>
    <span class="pick-grow">${lead || ""}<b>${esc(label)}</b>
    <span class="rooms">${esc(g.teacher)}${rooms ? " · " + esc(rooms) : ""}${esc(fmtSecTimes(g.times))}</span></span></label>`;
}
function subjectPickerHTML(subjects, filter, checkedSet, autoCheck) {
  const f = (filter || "").trim().toLowerCase();
  return subjects.filter((s) => !f || s.subject.toLowerCase().includes(f) ||
      (s.groups || []).some((g) => (g.subject || "").toLowerCase().includes(f)))
    .map((s) => {
      const flat = (s.groups || []).length === 1;
      if (flat) {   // 只有一个教学组: 一行搞定, 不用折叠
        const lead = `<b>${esc(s.subject)}</b> <span class="rooms">· </span>`;
        return grpRowHTML(s.subject, s.groups[0], checkedSet, 8, lead, autoCheck);
      }
      const skey = `s:${s.subject}`;
      const sopen = selOpen.has(skey);
      return `<div class="fold-head" data-fold="${esc(skey)}">
          <span class="tri">${sopen ? "▼" : "▶"}</span>
          <span class="pick-grow"><b>${esc(s.subject)}</b>
          <span class="rooms">${s.groups.length} 个教学组, 选你的</span></span></div>
        <div class="fold-body${sopen ? "" : " hidden"}">${
          s.groups.map((g) => grpRowHTML(s.subject, g, checkedSet, 26, ""))
            .join("")}</div>`;
    }).join("") || `<div class="empty">没有匹配的科目</div>`;
}
function bindPickerFolds(container) {
  container.querySelectorAll(".fold-head").forEach((h) => {
    h.onclick = () => {
      const hidden = h.nextElementSibling.classList.toggle("hidden");
      h.querySelector(".tri").textContent = hidden ? "▶" : "▼";
      if (hidden) selOpen.delete(h.dataset.fold); else selOpen.add(h.dataset.fold);
    };
  });
}
function collectPickerSelection(containerId) {
  const sel = [];
  $$(`#${containerId} input[type=checkbox]:checked:not([disabled])`).forEach((c) =>
    sel.push({ subject: c.dataset.sub, teacher: c.dataset.teacher,
               group: c.dataset.group || "" }));
  return sel;
}

/* ---- 设置页 · 修改选课 ---- */
let smSubjects = [];
$("#st-repick").onclick = async () => {
  $("#subject-modal").classList.remove("hidden");
  $("#sm-msg").textContent = "";
  $("#sm-subjects").innerHTML = `<div class="empty">正在读取科目…(Edupage 较慢, 最多约 1 分钟)</div>`;
  try {
    smSubjects = await call("wizard_subject_options");
    renderSmSubjects("");
  } catch (e) {
    $("#sm-subjects").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
};
$("#sm-filter").addEventListener("input", (e) => renderSmSubjects(e.target.value));
$("#sm-cancel").onclick = () => $("#subject-modal").classList.add("hidden");
$("#sm-save").onclick = async () => {
  const sel = collectPickerSelection("sm-subjects");
  try {
    const r = await call("wizard_save_selection", JSON.stringify(sel));
    selectedLessonsCache = sel;
    $("#st-subjects").innerHTML = sel.map((s) =>
      `<span class="chip on">${esc(s.subject)}${s.teacher ? " · " + esc(s.teacher) : ""}${s.group ? " · 组" + esc(s.group) : ""}</span>`).join("") ||
      `<span class="muted">尚未选课</span>`;
    $("#subject-modal").classList.add("hidden");
    toast(`选课已更新(${r.selected} 门)`);
    Store.drop("tt|"); Store.drop("home");   // 选课变了, 课表缓存失效
  } catch (e) { $("#sm-msg").textContent = `✗ ${e.message}`; }
};
function renderSmSubjects(filter) {
  const checked = new Set(selectedLessonsCache.map((s) =>
    `${subjFamily(s.subject)}|${s.teacher || ""}|${s.group || ""}`));
  $("#sm-subjects").innerHTML = subjectPickerHTML(smSubjects, filter, checked);
  bindPickerFolds($("#sm-subjects"));
}
$("#st-save").onclick = async () => {
  try {
    await call("settings_save", JSON.stringify({
      managebac_base_url: $("#st-mb-url").value,
      managebac_email: $("#st-mb-email").value,
      managebac_password: $("#st-mb-pass").value,
      edupage_username: $("#st-ep-user").value,
      edupage_subdomain: $("#st-ep-sub").value,
      edupage_password: $("#st-ep-pass").value,
      mail_email: $("#st-mail-email").value,
      mail_imap_host: $("#st-mail-imap").value,
      mail_smtp_host: $("#st-mail-smtp").value,
      mail_password: $("#st-mail-pass").value,
      mail_authcode: $("#st-mail-authcode").value,
      send_grades_to_llm: $("#st-grades-llm").checked,
    }));
    toast("已保存");
  } catch (e) { toast(e.message); }
};
$("#st-test").onclick = async () => {
  $("#st-test-result").textContent = "测试中…(可能需要十几秒)";
  try {
    const r = await call("connection_test");
    $("#st-test-result").textContent =
      Object.entries(r).map(([k, v]) => `${k}: ${v}`).join("   ");
  } catch (e) { $("#st-test-result").textContent = e.message; }
};

/* ================= 首启向导 ================= */
let wizSubjects = [];
function wzShow(n) {
  for (let i = 1; i <= 5; i++) $(`#wz-${i}`).classList.add("hidden");
  $(`#wz-${n}`).classList.remove("hidden");
  $("#wz-step").textContent = `${n} / 5`;
  // 每步都显示"进入 Hello Pinghe! Launcher"按钮，允许随时完成向导
  $("#wz-finish").classList.remove("hidden");
}
/* 选课进度: python 侧推送(Edupage 单次可达 1 分钟) */
window.__wizardEvent = (e) => {
  if (e && e.type === "subjects_progress") {
    $("#wz-sub-msg").textContent =
      `正在读取 ${e.day} 的全校课表(第 ${e.attempt}/${e.total} 次, Edupage 较慢, 请稍候)…`;
  }
};
$("#wz-ep-go").onclick = async () => {
  $("#wz-ep-msg").textContent = "登录中…(国内访问 Edupage 稍慢)";
  try {
    const r = await call("wizard_edupage_login", $("#wz-ep-user").value,
      $("#wz-ep-pass").value, $("#wz-ep-sub").value);
    $("#wz-ep-msg").textContent = `✓ 已连接 (${r.subdomain})`;
    wzShow(2);
  } catch (e) { $("#wz-ep-msg").textContent = `✗ ${e.message}`; }
};
$("#wz-ep-skip").onclick = () => wzShow(2);
$("#wz-mb-go").onclick = async () => {
  $("#wz-mb-msg").textContent = "登录中…";
  try {
    const r = await call("wizard_managebac_login", $("#wz-mb-url").value,
      $("#wz-mb-email").value, $("#wz-mb-pass").value);
    $("#wz-mb-msg").textContent = `✓ 已连接`;
    wzShow(3);
    $("#wz-subjects").innerHTML = `<div class="empty">读取科目中…</div>`;
    wizSubjects = await call("wizard_subject_options");
    renderSubjects("");
  } catch (e) { $("#wz-mb-msg").textContent = `✗ ${e.message}`; }
};
$("#wz-mb-skip").onclick = () => wzShow(3);
function renderSubjects(filter) {
  $("#wz-subjects").innerHTML = subjectPickerHTML(wizSubjects, filter, new Set(), true);
  bindPickerFolds($("#wz-subjects"));
}
$("#wz-sub-filter").addEventListener("input", (e) => renderSubjects(e.target.value));
$("#wz-sub-go").onclick = async () => {
  const sel = collectPickerSelection("wz-subjects");
  try {
    const r = await call("wizard_save_selection", JSON.stringify(sel));
    $("#wz-sub-msg").textContent = `✓ 已选择 ${r.selected} 门`;
    wzShow(4);
  } catch (e) { $("#wz-sub-msg").textContent = `✗ ${e.message}`; }
};
$("#wz-sub-skip").onclick = () => wzShow(4);
async function loadAiEnv() {
  $("#wz-ai-env").textContent = "检测硬件中…";
  try {
    const env = await call("wizard_ai_env");
    $("#wz-ai-env").textContent =
      `CPU ${env.cpu} 核 · 内存 ${env.ram_gb}GB · 显卡 ${env.gpu} → 建议: ` +
      (env.advice === "local"
        ? `本地 Ollama 模型 ${env.recommended_model}`
        : `性能有限, 建议 API(也可装 Ollama 跑 ${env.recommended_model})`);
  } catch (e) { $("#wz-ai-env").textContent = e.message; }
}
$("#wz-mail-go").onclick = async () => {
  $("#wz-mail-msg").textContent = "验证中…(约 10 秒)";
  try {
    const r = await call("wizard_mail_save", $("#wz-mail-email").value,
      $("#wz-mail-pass").value, $("#wz-mail-authcode").value,
      $("#wz-mail-imap").value, $("#wz-mail-smtp").value);
    $("#wz-mail-msg").textContent = `✓ 邮箱已连接, 未读 ${r.unread} 封`;
    await loadAiEnv();
    wzShow(5);
  } catch (e) { $("#wz-mail-msg").textContent = `✗ ${e.message}(可检查密码/授权码, 或先跳过)`; }
};
$("#wz-mail-skip").onclick = async () => {
  await loadAiEnv();
  wzShow(5);
};
$("#wz-ai-go").onclick = async () => {
  try {
    const r = await call("wizard_ai_save", $("#wz-ai-preset").value, $("#wz-ai-key").value,
      $("#wz-ai-base").value, $("#wz-ai-model").value,
      $("#wz-ai-preset").value === "ollama" ? "openai" : "openai",
      $("#wz-ai-preset").value === "ollama");
    $("#wz-ai-msg").textContent = `✓ ${r.provider} / ${r.model}`;
    $("#wz-finish").classList.remove("hidden");
  } catch (e) { $("#wz-ai-msg").textContent = `✗ ${e.message}`; }
};
$("#wz-ai-skip").onclick = () => {
  $("#wz-finish").classList.remove("hidden");
};
$("#wz-finish").onclick = async () => {
  await call("wizard_finish");
  $("#wizard").classList.add("hidden");
  runSplash();
};
$("#wz-skip").onclick = async () => {
  await call("wizard_finish");
  $("#wizard").classList.add("hidden");
  runSplash();
};

/* ================= 启动 ================= */
async function boot() {
  const st = await call("wizard_status");
  if (!st.done) {
    $("#wizard").classList.remove("hidden");
    wzShow(1);
    show("home");
    return;
  }
  runSplash();
}

/* 启动连接页: Edupage / ManageBac / 邮箱 并行连接,
 * 每行连接成功后立即预载"自己负责的页面"数据进本地缓存 ——
 * 进主界面后每个页面秒开, 之后仅后台静默刷新。 */
function runSplash() {
  const splash = $("#splash");
  splash.classList.remove("hidden");
  const state = { edupage: "pending", managebac: "pending", mail: "pending" };
  const setRow = (k, cls, text) => {
    const s = $(`#sp-row-${k} .sp-status`);
    if (!s) return;
    s.className = `sp-status ${cls}`;
    s.textContent = text;
    const row = $(`#sp-row-${k}`);
    row.classList.toggle("working", cls === "working");
  };
  const checkAll = () => {
    if (Object.values(state).every((v) => v !== "pending")) {
      $("#sp-enter").classList.remove("hidden");
      setTimeout(() => enterApp(), 700);
    }
  };

  /* 每行 = 连接 + 预载各自的页面数据(失败不阻塞进入) */
  const chains = {
    edupage: async () => {
      const r = await call("connect_edupage");
      setRow("edupage", "working", "✓ 已连接 · 预载课表/班级课表…");
      const warm = [];
      warm.push(preload(`tt|0`, () => call("timetable_week", 0))());      // 我的课表
      warm.push(preload(`gt|${new Date().toISOString().slice(0, 10)}`,
        () => call("gradett_data", new Date().toISOString().slice(0, 10)))()); // 班级课表(最慢, 提前热身)
      await Promise.allSettled(warm);
      return r;
    },
    managebac: async () => {
      const r = await call("connect_managebac");
      setRow("managebac", "working", "✓ 已连接 · 预载课程/DDL…");
      await preload("courses", () => call("courses_data"))();            // 我的课程
      return r;
    },
    mail: async () => {
      const r = await call("connect_mail");
      setRow("mail", "working", "✓ 已连接 · 预载邮件/首页…");
      await Promise.allSettled([
        preload(`mail|0|40`, () => call("mail_list", false, 40))(),      // 邮箱
        preload("home", () => call("home_data"))(),                      // 首页
      ]);
      return r;
    },
  };

  Object.entries(chains).forEach(([k, chain]) => {
    setRow(k, "working", "连接中…");
    chain()
      .then((r) => {
        if (state[k] === "skip") { checkAll(); return; }
        state[k] = "done";
        const d = r && typeof r === "object"
          ? Object.entries(r).map(([a, b]) => `${a}:${b}`).join("  ") : "";
        setRow(k, "ok", `✓ ${d} · 页面已就绪`);
        checkAll();
      })
      .catch((e) => {
        if (state[k] === "skip") { checkAll(); return; }
        state[k] = "fail";
        setRow(k, "fail", `✗ ${String(e.message).slice(0, 60)}`);
        checkAll();
      });
  });

  $$("#splash [data-skip]").forEach((b) => {
    b.onclick = () => {
      const k = b.dataset.skip;
      state[k] = "skip";
      setRow(k, "muted", "已跳过");
      b.disabled = true;
      checkAll();
    };
  });
  function enterApp() {
    splash.classList.add("hidden");
    show("home");
  }
  $("#sp-enter").onclick = enterApp;
}
window.addEventListener("pywebviewready", () => {
  boot().catch((e) => toast(e.message, 5000));
});

/* 密码输入框: 自动加显示/隐藏切换按钮 */
document.querySelectorAll('input[type="password"]').forEach((inp) => {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pw-toggle";
  btn.textContent = "👁";
  btn.tabIndex = -1;
  btn.title = "显示/隐藏密码";
  btn.onclick = (e) => {
    e.preventDefault();
    inp.type = inp.type === "password" ? "text" : "password";
    btn.style.opacity = inp.type === "text" ? "1" : ".45";
  };
  inp.after(btn);
});
