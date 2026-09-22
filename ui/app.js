/* Pinghe Launcher Lite 前端逻辑: 路由 + 各视图加载 + 向导 + Agent */
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
  xinlv: "心履 · 心情", agent: "Agent 助手", settings: "设置",
};
let currentView = "home";
let ttOffset = 0;
let mailMode = 0;

/*: 每个页面的加载器。自动刷新用的是**同一张表**——
 * 所以"自动"和"用户切页"走的是完全一样的代码路径（swr 自己会判断数据够不够新）。 */
const LOADERS = {
  home: () => loadHome(), timetable: () => loadTimetable(), schedule: () => loadSchedule(),
  gradett: () => loadGradett(), courses: () => loadCourses(), mail: () => loadMail(),
  xinlv: () => loadXinlv(), agent: () => loadAgent(), settings: () => loadSettings(),
};

function show(view) {
  currentView = view;
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#view-${view}`).classList.add("active");
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.go === view));
  $("#view-title").textContent = TITLES[view] || view;
  (LOADERS[view] || (() => Promise.resolve()))().catch((e) => toast(e.message));
  paintDataStamp();
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
/* 点左上角软件名/logo 回首页 */
$("#logo").addEventListener("click", () => show("home"));

/* ================= 自动刷新 / 自动同步（界面没有刷新按钮） =================
 * 用户 2026-09-21：「右上角那个刷新按钮和窗口控件重叠了，直接删掉好了，
 * 软件自动刷新，自动更新同步数据，逻辑和 phl 一样」。照 PHL 的做法：
 *
 *   · 界面上**一个刷新/同步按钮都没有**；
 *   · 每 20 秒醒一次，但每一页有自己的节拍（下面 AUTO_EVERY）：没到自己的节拍就什么都不做，
 *     到了就跑一遍该页的加载器 —— 数据新不新由 swr 判断，新的时候一个请求都不发；
 *     过期就后台悄悄重取，取到后无感替换（用户永远看到的是内容，不是转圈）；
 *   · 窗口重新拿到焦点 / 从托盘里恢复出来时立刻查一次；
 *   · 任何写操作之后（加日程、发邮件、记心情、改选课…）相关页的缓存本来就会被
 *     `Store.drop(...)` 作废，下一次查就顺手补上；
 *   · 只在侧栏底部留一行不起眼的小字「当前数据：几月几日几点几分」。
 */
/*: 每一页自己的自动刷新节拍（毫秒）。和 swr 的 TTL 对齐；没有缓存层的页面靠这里节流，
 * 免得每分钟去敲学校网站或心履服务器。agent / settings 是操作面板，不自动刷。 */
const AUTO_EVERY = {
  home: 60e3, timetable: 120e3, schedule: 60e3, gradett: 300e3,
  courses: 180e3, mail: 45e3, xinlv: 120e3,
};
const AUTO_TICK_MS = 20 * 1000;
const lastAutoAt = {};

/** 当前这一页的数据是什么时候取回来的（没有缓存返回 0）。
 *  缓存 key 是带参数的（`tt|周偏移`、`gt|日期`、`mail|模式|条数`），
 *  所以这里按**前缀**去 localStorage 里找，而不是猜一个完整 key —— 猜错就会一直显示空。 */
const VIEW_CACHE_PREFIX = {
  home: ["home"], timetable: ["tt|"], gradett: ["gt|"],
  courses: ["courses"], mail: ["mail|"], schedule: ["schedule"],
};

function viewDataAt(view) {
  const prefixes = VIEW_CACHE_PREFIX[view] || [];
  let newest = 0;
  try {
    Object.keys(localStorage).forEach((key) => {
      if (!key.startsWith("sh_")) return;
      const name = key.slice(3);
      if (!prefixes.some((prefix) => name === prefix || name.startsWith(prefix))) return;
      const cached = Store.get(name);
      if (cached && cached.t > newest) newest = cached.t;
    });
  } catch (e) { /* localStorage 读不到就当没有 */ }
  return newest;
}

/** 侧栏底部那行小字：当前数据的时间（读不到就留空，绝不显示骗人的假时间）。 */
function paintDataStamp() {
  const el = $("#data-stamp");
  if (!el) return;
  const at = viewDataAt(currentView);
  if (!at) { el.textContent = "当前数据：读取中…"; return; }
  const d = new Date(at);
  const hhmm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const sameDay = d.toDateString() === new Date().toDateString();
  el.textContent = `当前数据：${sameDay ? hhmm : `${d.getMonth() + 1}/${d.getDate()} ${hhmm}`}`;
}

/** 走一遍当前页的加载器：新不新鲜由 swr 判断，这里只负责"想起来要去看一眼"。 */
function autoRefreshCurrent(force) {
  const view = currentView;
  const every = AUTO_EVERY[view];
  if (!every || document.hidden) return;               // 收进托盘时不打扰
  paintDataStamp();                                    // 小字每个节拍都跟着走一次
  if (!force && Date.now() - (lastAutoAt[view] || 0) < every) return;
  lastAutoAt[view] = Date.now();
  const loader = AUTO_LOADERS[view];
  if (!loader) return;
  Promise.resolve(loader())
    .then(() => { if (currentView === view) paintDataStamp(); })
    .catch(() => { /* 自动刷新失败不出声：下一次节拍再试，界面上也没有按钮可点 */ });
}

/*: 后台自动刷新专用的"温和版"加载器。
 * 有些页面的加载器是为"用户点进来"写的（会重置表单、切回第一个标签），
 * 在后台定时跑就会打扰用户正在输入的东西 —— 这里逐个换成不打扰的做法。 */
const AUTO_LOADERS = Object.assign({}, LOADERS, {
  /* 心履：只做一次静默同步（内部会自己刷新日历与最近记录），不动填写中的表单 */
  xinlv: () => call("xinlv_status").then((st) => (st.logged_in ? xlSyncQuiet() : null)),
});

setInterval(() => autoRefreshCurrent(false), AUTO_TICK_MS);
window.addEventListener("focus", () => autoRefreshCurrent(true));
document.addEventListener("visibilitychange", () => { if (!document.hidden) autoRefreshCurrent(true); });

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
  const pRows = [];   // P1-P10 的行号(用于行高统一)
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
    if (!p.rest) pRows.push(r);   // 正课行 → 稍后统一高度
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
  ttPRows = pRows;
  ttFitRows(pRows);
  updateNowLine();
}

/* P1-P10 行高统一: 量出所有正课行的自然高度取最大值, 再把每个正课行都固定
   成这个高度 —— 免得"三节课并行"的行很高、"只有一节课"的行很矮, 一片杂乱。
   Lunch/晚自习横幅行与"课外"行保持自适应; 连堂合并块跨两行, 天然够高。 */
function ttFitRows(pRows) {
  const grid = $("#tt-week");
  if (!grid || !pRows || !pRows.length) return;
  grid.style.gridTemplateRows = "";          // 先还原, 量的才是自然高度
  const byRow = new Map();
  $$("#tt-week .tt-cell").forEach((c) => {
    const row = parseInt(c.style.gridRow, 10);
    if (!row) return;
    const h = c.offsetHeight;
    if (h > (byRow.get(row) || 0)) byRow.set(row, h);
  });
  const heights = pRows.map((row) => byRow.get(row) || 0);
  const maxH = Math.max(...heights);
  if (!maxH) return;                          // 页面不可见(隐藏时量到 0) → 不处理
  const lastRow = Math.max(...$$("#tt-week [style*='grid-row']")
    .map((el) => parseInt(el.style.gridRow, 10) || 0));
  const tpl = [];
  for (let row = 1; row <= lastRow; row++) {
    tpl.push(pRows.includes(row) ? `${maxH}px` : "auto");
  }
  grid.style.gridTemplateRows = tpl.join(" ");
}
/* 窗口尺寸/缩放变化时重新统一(字体大小变了, 自然高度也会变) */
let ttPRows = [];
let ttFitTimer = 0;
window.addEventListener("resize", () => {
  clearTimeout(ttFitTimer);
  ttFitTimer = setTimeout(() => {
    if (currentView === "timetable" && ttPRows.length) ttFitRows(ttPRows);
  }, 180);
});
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
  const upcoming = d.tasks_upcoming || [];
  const past = d.tasks_past || [];
  const box = $("#co-tasks");
  box.innerHTML = "";
  if (!upcoming.length && !past.length) {
    box.innerHTML = `<div class="empty">没有作业 (左滑可移除, ▲▼ 可调顺序, 点击看详情)</div>`;
    return;
  }
  const frag = document.createDocumentFragment();
  const bind = (t, list) => {
    const el = taskItemEl(t);
    el.querySelectorAll(".move-btn").forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        moveTask(d, list, t, btn.dataset.move === "down" ? 1 : -1);
      };
    });
    el.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      if (Date.now() - (el._swipedAt || 0) < 500) return;   /* 刚左滑完不弹卡 */
      openTaskModal(t);
    });
    return el;
  };
  if (!upcoming.length) {
    frag.appendChild(Object.assign(document.createElement("div"),
      { className: "empty", textContent: "没有未截止的作业" }));
  }
  upcoming.forEach((t) => frag.appendChild(bind(t, upcoming)));
  if (past.length) {
    /* 已过期(首页不显示, 这里完整可查): 分组标题下按时间倒序 */
    const head = document.createElement("div");
    head.className = "list-sep";
    head.innerHTML = `<span>已过期 · ${past.length} 项</span>`;
    frag.appendChild(head);
    past.forEach((t) => frag.appendChild(bind(t, past)));
  }
  box.appendChild(frag);
}
function taskItemEl(t) {
  const pastBadge = t.past_due
    ? badge(t.status && t.status !== "Pending" ? t.status : "已过期", "past")
    : badge(t.status || "?", t.status === "Pending" ? "red" : "green");
  return swipeableItemEl("item ddl-item" + (t.past_due ? " past-due" : ""),
    `<span class="move-btns"><button class="move-btn" data-move="up" title="上移">▲</button><button class="move-btn" data-move="down" title="下移">▼</button></span>
    <span class="dim">${esc((t.due_at || "").slice(5, 16))}</span>
    <span class="grow">${esc(t.title)}<span class="dim"> · ${esc(t.class_name)}</span></span>
    ${pastBadge}`,
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
  tdSyncSubmitBtn(t, null);
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
    /* 详情比列表准（dropbox 段会写"已提交/待提交"）→ 用详情再校正一次按钮 */
    tdSyncSubmitBtn(t, d);
  }).catch((e) => { $("#td-desc").textContent = `详情加载失败: ${e.message}`; });
}

/* 提交按钮按实际情况显示，而不是永远挂着一个"提交作业"。
   列表里的 can_submit 来自卡片上有没有 Submit Coursework 按钮；
   详情里的 dropbox 文案更准（会写"已提交"）。
   注意：`can_submit` 缺失时**不要藏按钮** —— 老缓存里没这个字段，
   藏了就变成"作业交不了"。只有明确 false 才降级。 */
function tdSyncSubmitBtn(t, detail) {
  const btn = $("#td-submit");
  if (!btn) return;
  const blob = ((detail && (detail.dropbox || "")) + " " +
                (t.status || "") + " " + ((detail && detail.status) || "")).toLowerCase();
  const done = /submitted|已提交|已上传|uploaded|received|graded|已评分/.test(blob);
  const closed = t.can_submit === false
    || (t.can_submit === undefined && t.past_due === true && !!t.status && t.status !== "Pending");
  if (done) {
    btn.textContent = "✓ 已提交";
    btn.disabled = true;
    btn.classList.remove("primary");
    btn.classList.add("ghost");
    btn.title = "ManageBac 上显示这次作业已经交过了";
    return;
  }
  if (closed) {
    btn.textContent = "🚫 未开放网上提交";
    btn.disabled = true;
    btn.classList.remove("primary");
    btn.classList.add("ghost");
    btn.title = "ManageBac 的作业卡上没有提交入口（可能已截止或要老师开放）";
    return;
  }
  btn.textContent = "📤 提交作业";
  btn.disabled = false;
  btn.classList.remove("ghost");
  btn.classList.add("primary");
  btn.title = "";
}

/* 提交成功后：回读详情 + 让课程页缓存失效，列表徽章随之变成 Submitted */
async function tdRefreshAfterSubmit(t) {
  Store.drop("courses");
  try {
    const d = await call("task_detail", t.class_id, t.task_id);
    $("#td-status").textContent = d.status || $("#td-status").textContent;
    if (d.dropbox) {
      /* 别把刚拿到的"已提交：xxx"覆盖掉 —— 它比页面文案更直接 */
      const keep = ($("#td-dropbox").textContent || "").trim();
      $("#td-dropbox").textContent = (keep && !keep.includes(d.dropbox))
        ? `${keep}（当前页面：${d.dropbox}）` : d.dropbox;
      $("#td-dropbox-row").classList.remove("hidden");
    }
    tdSyncSubmitBtn(t, d);
  } catch (e) { /* 回读失败不影响"已提交"这个结论，按钮状态下一次打开会刷新 */ }
  try { await loadCourses(); } catch (e) { /* 课程页没打开时不强求 */ }
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
    if (r.cancelled) { toast("已取消提交"); tdSyncSubmitBtn(tdTask, null); return; }
    toast(r.message || "已提交, 请到 ManageBac 网页确认");
    $("#td-dropbox").textContent = r.message || "已提交";
    $("#td-dropbox-row").classList.remove("hidden");
    await tdRefreshAfterSubmit(tdTask);
  } catch (e) {
    toast(e.message);
    tdSyncSubmitBtn(tdTask, null);
  }
};

/* ---------------- CAS / EE 弹卡 ---------------- */
const CORE_TITLES = {
  cas: "🎨 CAS 创意 · 行动 · 服务",
  ee: "📄 EE 拓展论文",
};
let coreKind = "cas";
let coreForms = [];      /* 页面上真实存在的可提交表单 */
let coreFilePicked = ""; /* 需要附件时用户选的文件路径 */

async function openCoreModal(kind) {
  coreKind = kind;
  $("#core-title").textContent = CORE_TITLES[kind] || "IB Core";
  const body = $("#core-body");
  body.innerHTML = `<div class="empty">加载中…</div>`;
  $("#core-modal").classList.remove("hidden");
  $("#core-open-mb").onclick = () => {
    call("open_external", CORE_URLS[kind]).catch((e) => toast(e.message));
  };
  await coreLoadForms();
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

/* 把 ManageBac 页面上**真实存在**的表单渲染出来 —— 字段名、下拉选项、
   必填与否全部来自页面本身，不写死任何路由或字段，学校改版也不用改代码。 */
async function coreLoadForms() {
  const wrap = $("#core-forms-wrap");
  const box = $("#core-forms");
  if (!wrap || !box) return;
  coreFilePicked = "";
  const fn = $("#core-file-name"); if (fn) fn.textContent = "";
  try {
    const d = await call("core_forms", coreKind);
    coreForms = (d && d.forms) || [];
  } catch (e) {
    coreForms = [];
    wrap.hidden = false;
    box.innerHTML = `<div class="muted small">表单探测失败：${esc(e.message)}</div>`;
    coreUpdateSubmitRow();
    return;
  }
  if (!coreForms.length) {
    wrap.hidden = false;
    box.innerHTML = `<div class="muted small">这个页面上没有可以直接提交的表单。`
      + `CAS 的经历 / 反思可能要在 ManageBac 网页里填写 —— 点左下角按钮去网页完成。</div>`;
    coreUpdateSubmitRow();
    return;
  }
  wrap.hidden = false;
  box.innerHTML = coreForms.map((f, i) =>
    `<div class="core-form-block" data-form="${i}">
       <div class="muted small">${coreForms.length > 1 ? `表单 ${i + 1}` : "可填提交表单"}${f.has_file ? " · 需要附件" : ""}</div>`
       + f.fields.filter((x) => x.type !== "file").map((x) => coreFieldHtml(x)).join("")
     + `</div>`).join("");
  coreUpdateSubmitRow();
}

function coreFieldHtml(x) {
  const req = x.required ? `<span class="core-form-req">*</span>` : "";
  const attrs = `data-field="${esc(x.name)}" data-type="${esc(x.type)}"`;
  let input;
  if (Array.isArray(x.options) && x.options.length) {
    input = `<select ${attrs}>` + x.options.map((o) =>
      `<option value="${esc(o.value)}"${String(o.value) === String(x.value) ? " selected" : ""}>${esc(o.label)}</option>`).join("") + `</select>`;
  } else if (x.type === "textarea") {
    input = `<textarea ${attrs}>${esc(x.value)}</textarea>`;
  } else {
    const t = x.type === "checkbox" ? "checkbox" : (x.type === "date" ? "date" : "text");
    input = `<input type="${t}" ${attrs} value="${esc(x.value)}"${x.type === "checkbox" && x.value ? " checked" : ""}>`;
  }
  return `<div class="core-form-field"><label>${esc(x.label)}${req}</label>${input}</div>`;
}

function coreUpdateSubmitRow() {
  const btn = $("#core-submit");
  const fileBtn = $("#core-file-btn");
  if (!btn || !fileBtn) return;
  const hasFields = coreForms.some((f) => f.fields.some((x) => x.type !== "file"));
  const needsFile = coreForms.some((f) => f.has_file);
  const canDo = hasFields || needsFile;
  btn.hidden = !canDo;
  fileBtn.hidden = !needsFile;
  btn.disabled = !canDo;
}

function coreCollectValues() {
  const out = {};
  $$("#core-forms [data-field]").forEach((el) => {
    out[el.dataset.field] = el.dataset.type === "checkbox"
      ? (el.checked ? (el.value || "1") : "") : el.value;
  });
  return out;
}

async function coreSubmit() {
  const btn = $("#core-submit");
  if (!btn || btn.disabled) return;
  const msg = $("#core-msg");
  const payload = { form_index: 0, values: coreCollectValues(), file_path: coreFilePicked };
  btn.disabled = true;
  if (msg) { msg.style.color = ""; msg.textContent = "正在提交…"; }
  try {
    const r = await call("core_submit", coreKind, JSON.stringify(payload));
    if (r && r.cancelled) { if (msg) msg.textContent = "已取消"; return; }
    if (msg) msg.textContent = (r && r.message) || "已提交";
    toast("已提交");
    if (r && r.overview) {
      const body = $("#core-body");
      const secs = r.overview.sections || [];
      if (secs.length) {
        body.innerHTML = "";
        secs.forEach((s) => {
          const el = document.createElement("div");
          el.className = "core-sec";
          el.innerHTML = `<b>${esc(s.h)}</b><div class="td-desc">${esc(s.text)}</div>`;
          body.appendChild(el);
        });
      }
    }
    await coreLoadForms();
  } catch (e) {
    if (msg) { msg.style.color = "#c0392b"; msg.textContent = e.message; }
    toast(e.message);
  } finally {
    btn.disabled = false;
    coreUpdateSubmitRow();
  }
}

$("#core-close").onclick = () => $("#core-modal").classList.add("hidden");
$("#core-close2").onclick = () => $("#core-modal").classList.add("hidden");
$("#core-forms-refresh").onclick = async (e) => {
  e.target.disabled = true;
  try { await coreLoadForms(); } finally { e.target.disabled = false; }
};
$("#core-file-btn").onclick = async (e) => {
  e.target.disabled = true;
  try {
    const r = await call("core_pick_file");
    if (r && r.cancelled) return;
    coreFilePicked = r.path || "";
    $("#core-file-name").textContent = r.name || "";
  } catch (err) {
    $("#core-msg").style.color = "#c0392b";
    $("#core-msg").textContent = err.message;
  } finally {
    e.target.disabled = false;
  }
};
$("#core-submit").onclick = () => coreSubmit();
/* 点遮罩 / Escape 关闭新弹卡(ml-modal 的遮罩点击在邮箱那一节自己处理) */
["cd-modal", "td-modal", "core-modal", "phix-choice"].forEach((id) => {
  $("#" + id).addEventListener("click", (e) => {
    /* phix-choice 是"必须选一个"的确认框：点遮罩不关，只能点按钮决定 */
    if (id === "phix-choice") return;
    if (e.target.id === id) $("#" + id).classList.add("hidden");
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    ["cd-modal", "td-modal", "core-modal", "ml-modal"].forEach((id) => $("#" + id).classList.add("hidden"));
  }
});

/* ================= 邮箱 ================= */
async function loadMail() { await fetchMail(); }
function renderMail(d) {
  /* 共享摘要(另一个程序写的): 只能看标题, 不给点击, 避免点到读不出来的邮件 */
  const shared = !!(d && d.shared);
  const note = shared
    ? `<div class="empty shared-note">${esc(d.note || "这是另一个程序上次同步到的邮箱摘要（只能看标题）。要读正文，请在本机登录邮箱。")}</div>`
    : "";
  $("#ml-list").innerHTML = note + ((d.mails || []).map((m) => `
    <div class="mail-item ${m.seen ? "" : "unread"}"${shared ? "" : ` data-uid="${m.uid}"`}>
      <div class="subj">${m.seen ? "" : "🔵 "}${esc(m.subject)}</div>
      <div class="meta">${esc(m.from)} · ${esc(m.date)}</div>
    </div>`).join("") || `<div class="empty">没有邮件</div>`);
  if (shared) return;
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
          <div class="mail-actions">
            <button class="primary btn-reply" data-act="reply">↩ 回复</button>
            <button class="primary btn-forward" data-act="forward">➡ 转发</button>
            <span class="spacer"></span>
          </div>
          <h3>${esc(m.subject)}</h3>
          <div class="muted small">${esc(m.from)} · ${esc(m.date)}</div>
          ${rcSection}
          ${attHtml}
          <hr><div class="mail-body">${bodyHtml}</div>`;
        el.classList.remove("unread");
        /* 标题左侧的未读蓝点也要去掉 */
        const subjEl = el.querySelector(".subj");
        if (subjEl) subjEl.textContent = subjEl.textContent.replace(/^🔵\s*/, "");
        /* 同步修本地缓存: 刷新列表后蓝点不再出现 */
        const ck = `mail|${mailMode}|40`;
        const cached = Store.get(ck);
        if (cached && cached.v) {
          const it = (cached.v.mails || []).find((x) => x.uid === el.dataset.uid);
          if (it) { it.seen = true; Store.set(ck, cached.v); }
        }
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
        /* 回复 / 转发(按钮在阅读窗格顶部) */
        $$("#ml-read .mail-actions [data-act]").forEach((btn) => {
          btn.onclick = () => startMailFrom(btn.dataset.act, el.dataset.uid, btn);
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
/* ================= 写邮件 / 回复 / 转发 =================
 * 撰写窗口是同一个: 写邮件=空白、回复=预填收件人+Re: 主题+引用块、
 * 转发=收件人留空+Fwd: 主题+引用块+原附件。
 * 附件对象: {name, size, path?, data_base64?} —— path 走"路径上传"(不经过
 * base64), 拿不到路径时退到 FileReader 读字节。
 */
const ML_ATT_MAX_ONE = 20 * 1024 * 1024;    /* 与 Python 端单个上限一致 */
let mlAtts = [];        /* 撰写窗口里已选的附件 */
let mlMode = "compose"; /* compose | reply | forward */

function mlSize(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}
function mlAttLabel() {
  const box = $("#ml-att-list");
  const info = $("#ml-att-info");
  if (!mlAtts.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    info.textContent = "";
    return;
  }
  const total = mlAtts.reduce((s, a) => s + (a.size || 0), 0);
  info.textContent = `${mlAtts.length} 个附件 · ${mlSize(total)}`;
  box.classList.remove("hidden");
  box.innerHTML = mlAtts.map((a, i) => `
    <div class="att-one">
      <span class="grow" title="${esc(a.name)}">📎 ${esc(a.name)}</span>
      <span class="att-size">${mlSize(a.size)}</span>
      <button class="ghost att-x" data-i="${i}" title="移除这个附件">✕</button>
    </div>`).join("");
  $$("#ml-att-list .att-x").forEach((b) => {
    b.onclick = (e) => {
      /* 必须阻止冒泡: ✕ 点下去会冒泡到整行, 不拦就会误删**别的**附件 */
      e.stopPropagation();
      mlAtts.splice(Number(b.dataset.i), 1);
      mlAttLabel();
    };
  });
}

function readFileB64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => {
      const s = String(fr.result || "");
      resolve(s.slice(s.indexOf(",") + 1));   /* 去掉 data:...;base64, 前缀 */
    };
    fr.onerror = () => reject(new Error(`读取文件失败: ${file.name}`));
    fr.readAsDataURL(file);
  });
}

/* 选中的 File 对象 → 附件规格。优先 file.path(pywebview 会给真实路径),
 * 拿不到就退回把字节读成 base64 —— 两条路 Python 端都收。 */
async function mlFileToSpec(file) {
  if (!file) return null;
  if (file.size > ML_ATT_MAX_ONE) {
    toast(`「${file.name}」${mlSize(file.size)} 超过单个附件上限 ${mlSize(ML_ATT_MAX_ONE)}, 没有加进来`);
    return null;
  }
  const raw = file.path || (file.webkitRelativePath || "");
  if (raw && !/^[a-z]:[\\/]fakepath[\\/]/i.test(raw) && raw.includes(":")) {
    return { name: file.name, size: file.size, path: raw };
  }
  try {
    return { name: file.name, size: file.size, data_base64: await readFileB64(file) };
  } catch (e) {
    toast(e.message);
    return null;
  }
}

async function mlAddFiles(files) {
  for (const f of files) {
    const dup = mlAtts.some((a) => a.name === f.name && a.size === f.size);
    if (dup) continue;                       /* 同一个文件不重复加 */
    if (mlAtts.length >= 10) { toast("一次最多带 10 个附件"); break; }
    const spec = await mlFileToSpec(f);
    if (spec) mlAtts.push(spec);
  }
  mlAttLabel();
}

/* 打开撰写窗口。prefill 由 Python 端算出(mail_prefill), 含 quote/attachments */
function openCompose(prefill) {
  mlMode = (prefill && prefill.mode) || "compose";
  mlAtts = ((prefill && prefill.attachments) || [])
    .map((a) => ({ name: a.name, size: a.size || 0, data_base64: a.data_base64 }));
  $("#ml-title").textContent = mlMode === "reply" ? "↩ 回复邮件"
    : mlMode === "forward" ? "➡ 转发邮件" : "✉ 写邮件";
  $("#ml-to").value = (prefill && prefill.to) || "";
  $("#ml-cc").value = (prefill && prefill.cc) || "";
  $("#ml-bcc").value = (prefill && prefill.bcc) || "";
  $("#ml-subject").value = (prefill && prefill.subject) || "";
  $("#ml-body").value = (prefill && prefill.body) || "";
  $("#ml-msg").textContent = (prefill && prefill.skipped || []).length
    ? `⚠ ${prefill.skipped.length} 个原附件没带上(超过大小上限)` : "";
  $("#ml-att-list").innerHTML = "";
  $("#ml-att-info").textContent = "";
  mlAttLabel();
  $("#ml-modal").classList.remove("hidden");
  if (mlMode === "forward") {
    /* 转发: 焦点落在收件人框 */
    $("#ml-to").focus();
  } else if (mlMode === "reply") {
    /* 回复: 光标停在引用块**上方**, 直接打字就是写在引用前面 */
    const ta = $("#ml-body");
    ta.focus();
    try { ta.setSelectionRange(0, 0); ta.scrollTop = 0; } catch (e) { /* 忽略 */ }
  } else {
    $("#ml-body").focus();
  }
  loadMlContacts();   /* 通讯录未加载则后台拉取(磁盘缓存 24h) */
}

/* 回复 / 转发: 先让 Python 端取原邮件算出预填, 再开撰写窗口 */
async function startMailFrom(mode, uid, btn) {
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "准备中…"; }
  try {
    const prefill = await call("mail_prefill", uid, mode);
    openCompose(prefill);
  } catch (e) {
    toast(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
  }
}

$("#ml-compose").onclick = () => openCompose({ mode: "compose" });
$("#ml-cancel").onclick = () => { acClose(); $("#ml-modal").classList.add("hidden"); };
/* 附件: 优先走 HTML 文件选择框; 某些环境下打不开则退到系统对话框(拿真实路径) */
$("#ml-attach").onclick = () => {
  const inp = $("#ml-file");
  try {
    inp.value = "";
    inp.click();
  } catch (e) {
    pickBySystemDialog().catch((err) => toast(err.message));
  }
};
$("#ml-file").onchange = async (e) => {
  const files = Array.from(e.target.files || []);
  e.target.value = "";                     /* 允许再次选同一个文件 */
  if (!files.length) return;
  await mlAddFiles(files);
};
async function pickBySystemDialog() {
  const d = await call("mail_pick_attachments");
  if (d && d.files && d.files.length) {
    for (const f of d.files) {
      if (mlAtts.some((a) => a.path && a.path === f.path)) continue;
      if (f.size > ML_ATT_MAX_ONE) {
        toast(`「${f.name}」${mlSize(f.size)} 超过单个附件上限, 没有加进来`);
        continue;
      }
      mlAtts.push({ name: f.name, size: f.size, path: f.path });
    }
    mlAttLabel();
  }
}

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
    const specs = mlAtts.map((a) => (a.path
      ? { name: a.name, path: a.path }
      : { name: a.name, data_base64: a.data_base64 || "" }));
    const r = await call("mail_send", $("#ml-to").value, $("#ml-subject").value,
      $("#ml-body").value, $("#ml-cc").value, $("#ml-bcc").value,
      JSON.stringify(specs));
    Store.drop("mail|"); Store.drop("home");
    if (r && r.skipped && r.skipped.length) {
      /* 有附件没带上: 如实说清楚, 不假装全发了 */
      $("#ml-msg").textContent = r.message || `已发送(${r.skipped.length} 个附件没带上)`;
      toast(`已发送, 但 ${r.skipped.length} 个附件没带上: ${r.skipped.map((s) => s.name).join("、")}`);
      return;
    }
    toast(specs.length ? `已发送(带 ${specs.length} 个附件)` : "已发送");
    $("#ml-modal").classList.add("hidden");
    mlAtts = [];
    mlAttLabel();
    $("#ml-to").value = ""; $("#ml-cc").value = ""; $("#ml-bcc").value = "";
    $("#ml-subject").value = ""; $("#ml-body").value = "";
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
/* 推理模型的"思考过程"。默认折叠 —— 用户要的是"能看见"，不是"每次都铺一屏"。
   流式过程中自动展开（不然转半天没动静像卡死），第一段正文到达后收起。 */
let agentThinkingEl = null;
function addThinkingBlock() {
  if (agentThinkingEl) return agentThinkingEl;
  const det = document.createElement("details");
  det.className = "think-block";
  det.open = true;
  det.innerHTML =
    `<summary><span class="think-dot">🧠</span> 思考过程` +
    `<span class="think-hint">（点击可折叠）</span></summary>` +
    `<div class="think-body"></div>`;
  const chat = $("#ag-chat");
  // 发消息时会先放一个"…"占位气泡。思考必须排在它**前面**，
  // 否则时间顺序就反了（用户看到"…"在思考上方，还以为是卡住）。
  if (agentStreamingEl && agentStreamingEl.parentNode === chat) {
    chat.insertBefore(det, agentStreamingEl);
  } else {
    chat.appendChild(det);
  }
  agentThinkingEl = det;
  scrollChat();
  return det;
}
function appendThinking(text) {
  const det = addThinkingBlock();
  const body = det.querySelector(".think-body");
  if (body) body.textContent += text;
  scrollChat();
}
/* 偶发的内部提示（例如"该模型要求回传思考，已自动适配并重试"）。
   单独一行小字，不跟正文混在一起。 */
function addInfoNote(text) {
  const div = document.createElement("div");
  div.className = "agent-note";
  div.textContent = text;
  const chat = $("#ag-chat");
  if (agentStreamingEl && agentStreamingEl.parentNode === chat) {
    chat.insertBefore(div, agentStreamingEl);
  } else {
    chat.appendChild(div);
  }
  scrollChat();
}
function renderThinking(parent, text) {
  const det = document.createElement("details");
  det.className = "think-block";
  det.innerHTML =
    `<summary><span class="think-dot">🧠</span> 思考过程` +
    `<span class="think-hint">（点击可折叠）</span></summary>` +
    `<div class="think-body"></div>`;
  det.querySelector(".think-body").textContent = text;
  parent.appendChild(det);
}
function addToolCard(name, before) {
  lastToolCard = document.createElement("div");
  lastToolCard.className = "tool-card";
  lastToolCard.innerHTML =
    `<div class="tc-head"><span class="tc-status">⏳</span><span>🔧 ${esc(name)}</span></div>` +
    `<pre class="tc-body hidden"></pre>`;
  lastToolCard.querySelector(".tc-head").onclick = () =>
    lastToolCard.querySelector(".tc-body").classList.toggle("hidden");
  const chat = $("#ag-chat");
  // 工具卡要排在"…"占位气泡前面，时间顺序才对。
  // 注意：调用方会先把 agentStreamingEl 置空，所以占位气泡要**显式传进来**。
  const anchor = before !== undefined ? before : agentStreamingEl;
  if (anchor && anchor.parentNode === chat) {
    chat.insertBefore(lastToolCard, anchor);
  } else {
    chat.appendChild(lastToolCard);
  }
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
  if (e.type === "thinking") {
    // 推理模型的思考增量（deepseek-flash 等）。以前完全被丢掉 →
    // 用户只看到长时间没动静，以为卡住了。
    appendThinking(e.text || "");
  } else if (e.type === "info") {
    addInfoNote(e.text || "");
  } else if (e.type === "delta") {
    if (agentThinkingEl) agentThinkingEl.open = false;   // 正文开始了，收起思考
    if (!agentStreamingEl) {
      agentStreamingEl = addBubble("", "bot streaming cursor");
      agentStreamed = true;
    }
    agentStreamingEl.textContent += e.text;
    scrollChat();
  } else if (e.type === "tool") {
    // 先留住占位气泡再置空：工具卡要插在它前面，否则顺序变成
    // "…" → 工具卡，看起来像先出了回答再去调工具。
    const ph = agentStreamingEl;
    agentStreamingEl = null;
    addToolCard(e.name, ph);
    // 占位气泡用完就删 —— 不删的话它会一直留在那里闪，
    // 而工具跑完后的正文会另起一个气泡（屏幕上多一个孤零零的"…"）。
    if (ph && ph.parentNode) ph.remove();
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
          agentThinkingEl = null;
          for (const m of hist) {
            if (m.role === "assistant" && m.reasoning) {
              renderThinking($("#ag-chat"), m.reasoning);
            }
            addBubble(m.content, m.role === "user" ? "user" : "bot");
          }
          if (!hist.length) {
            $("#ag-chat").innerHTML = `<div class="muted">这个会话还没有内容</div>`;
          }
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
/* 出错 / 没能回答时的统一显示。
   不能假设 agentStreamingEl 还在 —— 走过工具轮之后它是 null
   （占位气泡已随工具卡一起收掉），那时若只在 agentStreamingEl 上写字，
   **错误就永远显示不出来**，用户只看到一直转圈。 */
function showAgentError(text) {
  let el = agentStreamingEl;
  if (!el) {
    el = addBubble("", "bot");
  }
  el.classList.remove("cursor");
  el.classList.add("err");
  el.textContent = text;
  agentStreamingEl = null;
  scrollChat();
}

async function agentSend() {
  const input = $("#ag-input");
  const msg = input.value.trim();
  if (!msg) return;
  input.value = "";
  addBubble(msg, "user");
  agentStreamed = false;
  agentThinkingEl = null;
  agentStreamingEl = addBubble("…", "bot streaming cursor");
  try {
    const r = await call("agent_chat", msg);
    if (agentStreamingEl) {
      if (r && r.ok === false) {
        showAgentError(`没能回答：${r.error || "未知原因"}`);
      } else {
        agentStreamingEl.innerHTML = mdToHtml(r.reply || "(无回复)");
        agentStreamingEl.classList.remove("cursor");
        agentStreamingEl = null;
      }
    } else if (r && r.ok === false) {
      showAgentError(`没能回答：${r.error || "未知原因"}`);
    }
    refreshProposals();
    refreshFiles();
    // 新会话在列表里要立刻出现（列表读的是磁盘文件）
    refreshSessions();
  } catch (e) {
    showAgentError(`出错: ${e.message}`);
    refreshSessions();
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
  await phixRefresh().catch(() => {});
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
  /* 无组 = 全班必修课(班会/语文这类) 或 默认必选课(国家理科),
     人人都有, 锁定为已选不可取消 */
  const whole = !g.group;
  const chk = (whole || autoCheck || grpChecked(checkedSet, fam, g.teacher, g.group))
    ? "checked" : "";
  const label = whole ? (g.default ? "默认必选" : "全班必修") : `组${g.group}`;
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

/* ================= phix 首启引导 ================= */
function obShow(what) {
  ["ob-ask", "ob-login", "ob-register"].forEach((id) => {
    $(`#${id}`).classList.add("hidden");
  });
  if (what) $(`#${what}`).classList.remove("hidden");
}
function obBind() {
  $("#ob-yes").onclick = () => obShow("ob-login");
  $("#ob-no").onclick = () => obShow("ob-register");
  $("#ob-back-ask").onclick = () => obShow("ob-ask");
  $("#ob-back-ask2").onclick = () => obShow("ob-ask");
  $("#ob-login-btn").onclick = async () => {
    const user = $("#ob-username").value.trim();
    const pass = $("#ob-password").value;
    if (!user || !pass) { $("#ob-msg").textContent = "账号、密码都要填"; return; }
    $("#ob-msg").textContent = "登录中…";
    try {
      /* 服务器由程序自己探测（内网自建 → phix.ing），口令也不在登录页问：
         强模式账号登录成功后是**锁着**的，界面会放人进主界面并在设置里解锁
         （见 obFail / phixRenderLocked）。这也是 PHL 的做法，用户 2026-09-21 要求对齐。 */
      const st = await call("phix_login", "", user, pass, "");
      const r = await phixOnboardSync($("#ob-msg"));
      $("#ob-msg").textContent = "登录成功：" + phixSummary(r.summary);
      phixDone();
    } catch (e) { obFail(e, $("#ob-msg")); }
  };
  $("#ob-reg-btn").onclick = async () => {
    const user = $("#ob-reg-user").value.trim();
    const pass = $("#ob-reg-pass").value;
    if (!user || pass.length < 6) {
      $("#ob-msg2").textContent = "账号要填，密码至少 6 位"; return;
    }
    $("#ob-msg2").textContent = "注册中…";
    try {
      await call("phix_register", "", user, pass, "password");
      $("#ob-msg2").textContent = "注册成功！请抄下恢复码。";
      // 注册后自动登录并同步
      await call("phix_login", "", user, pass, "");
      const r = await phixOnboardSync($("#ob-msg2"));
      $("#ob-msg2").textContent = "注册成功，已登录：" + phixSummary(r.summary);
      phixDone();
    } catch (e) { obFail(e, $("#ob-msg2")); }
  };
  phixShowResolvedServer();
  $("#ob-skip").onclick = () => phixDone();
}

/* 引导层里登录/同步失败时**绝不能把用户卡在引导页**：
   "数据是锁着的"这种要去设置里解锁，其它错误也要放人进去。 */
function obFail(e, msgEl) {
  const text = (e && e.message) || String(e);
  if (msgEl) {
    msgEl.textContent = /锁着|locked/.test(text)
      ? text + "（马上进入主界面，请在 设置 → phix 账号 里解锁）"
      : text;
  }
  if (/锁着|locked/.test(text)) setTimeout(phixDone, 1500);
}

/* 引导层用的同步：与设置页同款（先探测 → 必要时问方向 → 再同步），
   只是消息写到引导页自己的提示位上。 */
async function phixOnboardSync(msgEl) {
  let prefer = "merge";
  try {
    const probe = await call("phix_sync_probe");
    if (probe && probe.needs_choice) {
      if (msgEl) msgEl.textContent = "本地和账号里都有数据，请选一下以哪边为准…";
      prefer = await phixAskDirection(probe);
    }
  } catch (e) { prefer = "merge"; }
  if (msgEl) msgEl.textContent = "正在同步…";
  const r = await call("phix_sync", false, prefer);
  Store.drop("home"); Store.drop("courses"); Store.drop("mail");
  return r;
}
let _obDone = false;
function phixDone() {
  _obDone = true;
  $("#phix-onboard").classList.add("hidden");
  // 向导或进入主界面
  const bootFn = window._phixBootNext;
  if (bootFn) bootFn();
}

/* ================= 首启向导 ================= */
let wizSubjects = [];
function wzShow(n) {
  for (let i = 1; i <= 5; i++) $(`#wz-${i}`).classList.add("hidden");
  $(`#wz-${n}`).classList.remove("hidden");
  $("#wz-step").textContent = `${n} / 5`;
  // 每步都显示"进入 Pinghe Launcher Lite"按钮，允许随时完成向导
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
/** 开机画面收起来（首次启动要走向导/引导时用）。 */
function hideSplash() {
  const splash = document.getElementById("splash");
  if (splash) splash.classList.add("hidden");
  setBooting(false);
}

/** 开机画面期间给 body 挂 `booting`：自绘标题栏会据此"融进"墨绿底（见 styles.css）。 */
function setBooting(on) {
  document.body.classList.toggle("booting", !!on);
}

async function boot() {
  // 服务器地址不让用户填：启动时就把"会自动连哪台"问出来写在那行小字上（不阻塞界面）。
  phixShowResolvedServer().catch(() => {});
  const st = await call("wizard_status");
  if (!st.done) {
    // 首次启动：先检查 phix 会话
    hideSplash();   // 开机画面先收起来，让向导露出来（boot 时它是盖在最上面的）
    try {
      const phix = await call("phix_status");
      const hasSession = phix.has_access_token || phix.has_refresh_token || phix.has_token;
      if (!hasSession) {
        // 无会话 → 显示 phix 引导
        obBind();
        $("#phix-onboard").classList.remove("hidden");
        window._phixBootNext = () => {
          // 引导完成后进入向导
          $("#wizard").classList.remove("hidden");
          wzShow(1);
          show("home");
        };
        show("home");
        return;
      }
    } catch (e) { /* phix_status 失败就跳过引导 */ }
    // 有会话或检查失败 → 直接进向导
    $("#wizard").classList.remove("hidden");
    wzShow(1);
    show("home");
    return;
  }
  runSplash();
}

/* 启动画面（2026-09-20 用户要求，三端统一）：墨绿底 + 中间 logo + 一根细进度条，
   **一个字都没有**，像 Mac 开机。三件真实工作（Edupage / ManageBac / 邮箱）各自
   连接 + 预载自己负责的页面数据；进度取三者平均，全部落定（成功/失败/超时）才进主界面。 */
function runSplash() {
  const splash = $("#splash");
  splash.classList.remove("hidden");
  setBooting(true);      // 这一刻起自绘标题栏"融进"墨绿底
  const state = { edupage: "pending", managebac: "pending", mail: "pending" };
  const fill = () => document.getElementById("splash-fill");
  const paint = () => {
    const done = Object.values(state).filter((v) => v !== "pending").length;
    const el = fill();
    if (el) el.style.width = Math.round((done / 3) * 100) + "%";
  };
  /* 用户 2026-09-21：「进度条走完以后等个半秒，然后再渐渐消失，不要一下子没掉，
     让用户能看到进度条走完」。所以：三件都落定 → 进度条到 100% → 停 500ms →
     淡出 450ms → 才真正收起。 */
  const HOLD_AFTER_DONE_MS = 500;
  const FADE_MS = 450;
  let entered = false;
  const checkAll = () => {
    paint();
    if (Object.values(state).every((v) => v !== "pending")) setTimeout(() => enterApp(), HOLD_AFTER_DONE_MS);
  };
  paint();

  /* 每行 = 连接 + 预载各自的页面数据(失败不阻塞进入) */
  const chains = {
    edupage: async () => {
      const r = await call("connect_edupage");
      const warm = [];
      warm.push(preload(`tt|0`, () => call("timetable_week", 0))());      // 我的课表
      warm.push(preload(`gt|${new Date().toISOString().slice(0, 10)}`,
        () => call("gradett_data", new Date().toISOString().slice(0, 10)))()); // 班级课表(最慢, 提前热身)
      await Promise.allSettled(warm);
      return r;
    },
    managebac: async () => {
      const r = await call("connect_managebac");
      await preload("courses", () => call("courses_data"))();            // 我的课程
      return r;
    },
    mail: async () => {
      const r = await call("connect_mail");
      await Promise.allSettled([
        preload(`mail|0|40`, () => call("mail_list", false, 40))(),      // 邮箱
        preload("home", () => call("home_data"))(),                      // 首页
      ]);
      return r;
    },
  };

  Object.entries(chains).forEach(([k, chain]) => {
    chain()
      .then(() => { if (state[k] === "pending") state[k] = "done"; checkAll(); })
      .catch(() => { if (state[k] === "pending") state[k] = "fail"; checkAll(); });
  });

  /* 硬上限：45 秒还没齐就直接进主界面（原来靠「跳过」按钮，现在不摆按钮了）。 */
  setTimeout(() => enterApp(), 45000);
  /* 点一下画面也能直接进。 */
  splash.onclick = () => enterApp();

  function enterApp() {
    if (entered) return;
    entered = true;
    // 先把主界面摆好（它就在开机画面背后），再把开机画面淡出 —— 不然会看到一片空白。
    show("home");
    splash.style.transition = `opacity ${FADE_MS}ms ease`;
    splash.style.opacity = "0";
    setTimeout(() => { splash.classList.add("hidden"); setBooting(false); }, FADE_MS + 60);
  }
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

/* ================= 心履(xin-lv.com 心情记录) =================
 * 记录 / 月历 / 推荐 / 个人主页; 同步协议: uuid upsert + LWW + 墓碑,
 * 同步失败(离线)静默保留脏记录, 界面提示"待同步"。 */
const XL_MOODS = {
  happy:    { label: "开心", emoji: "😄", color: "#FFD56B" },
  calm:     { label: "平静", emoji: "🙂", color: "#9BD1C6" },
  excited:  { label: "兴奋", emoji: "🤩", color: "#FF9F68" },
  grateful: { label: "感恩", emoji: "🥰", color: "#F7A6C4" },
  tired:    { label: "疲惫", emoji: "😪", color: "#A6A6C9" },
  anxious:  { label: "焦虑", emoji: "😟", color: "#7FA6E8" },
  sad:      { label: "难过", emoji: "😢", color: "#6D8FB8" },
  angry:    { label: "愤怒", emoji: "😠", color: "#E8736B" },
  lonely:   { label: "孤独", emoji: "🌧️", color: "#8E94B8" },
  numb:     { label: "麻木", emoji: "😶", color: "#B0B0B0" },
};
const XL_LEVELS = { 1: "略微", 2: "有点", 3: "相当", 4: "十分" };
let xlMonth = (() => { const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; })();
let xlMood = "";
let xlEntries = [];      // 当前月 + recent
let xlRegistering = false;

function xlMoodBtn(key, active) {
  const m = XL_MOODS[key];
  return `<button class="xl-mood ${active ? "on" : ""}" data-mood="${key}"
    style="--mc:${m.color}" title="${m.label}">
    <img src="xinlv/mood_${key}.png" alt="${m.label}" draggable="false">
    <span>${m.label}</span></button>`;
}

function xlSetTab(tab) {
  $$("#view-xinlv [data-xtab]").forEach((b) =>
    b.classList.toggle("on", b.dataset.xtab === tab));
  ["checkin", "rec", "me"].forEach((t) =>
    $(`#xl-tab-${t}`).classList.toggle("hidden", t !== tab));
  if (tab === "me") xlLoadProfile();
  if (tab === "rec") {
    $("#xl-rec-moods").innerHTML =
      Object.keys(XL_MOODS).map((k) => xlMoodBtn(k, k === xlMood)).join("");
    xlLoadRecommend(xlMood || "happy");   // 进推荐页立即出推荐, 默认开心
  }
}

/* 心情时间/日期默认 = 系统当前时间 */
function xlNowInputs() {
  const now = new Date();
  $("#xl-date").value = isoOf(now);
  $("#xl-time").value = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}

async function loadXinlv() {
  const st = await call("xinlv_status");   // 不依赖登录, 一定成功
  $("#xl-login-card").classList.toggle("hidden", !!st.logged_in);
  $("#xl-main").classList.toggle("hidden", !st.logged_in);
  if (!st.logged_in) return;
  xlNowInputs();
  xlSetTab("checkin");
  await xlRefresh();
  xlSyncQuiet();   // 后台尽力同步, 完了自动刷新
}

async function xlRefresh() {
  const d = await call("xinlv_month", xlMonth);
  xlEntries = d.entries || [];
  xlRenderCalendar(d.entries || []);
  xlRenderRecent(d.recent || []);
  $("#xl-pending").textContent = d.pending > 0 ? `· ${d.pending} 条待同步` : "";
  $("#xl-sync-state").textContent = d.server_time ? `上次同步 ${d.server_time.slice(0, 16).replace("T", " ")}` : "";
}

async function xlSyncQuiet() {
  try {
    const r = await call("xinlv_sync");
    if (r.error) { $("#xl-sync-state").textContent = `同步失败: ${r.error}`; return; }
    await xlRefresh();
  } catch (e) { $("#xl-sync-state").textContent = `同步失败: ${e.message}`; }
}

function xlRenderCalendar(entries) {
  $("#xl-cal-month").textContent = xlMonth;
  const [y, mo] = xlMonth.split("-").map(Number);
  const first = new Date(y, mo - 1, 1);
  const days = new Date(y, mo, 0).getDate();
  const lead = (first.getDay() + 6) % 7;   // 周一开头
  // 每天 → 该天最后一条记录(按 date+at 排序后取末尾)
  const byDay = {};
  entries.forEach((e) => { (byDay[e.date] = byDay[e.date] || []).push(e); });
  let html = ["一", "二", "三", "四", "五", "六", "日"]
    .map((w) => `<span class="xl-cal-head">${w}</span>`).join("");
  for (let i = 0; i < lead; i++) html += `<span class="xl-cal-cell dim"></span>`;
  const today = new Date();
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  for (let d = 1; d <= days; d++) {
    const iso = `${xlMonth}-${String(d).padStart(2, "0")}`;
    const list = byDay[iso] || [];
    const last = list[list.length - 1];
    const m = last ? XL_MOODS[last.mood] : null;
    html += `<button class="xl-cal-cell ${iso === todayIso ? "today" : ""}"
      data-day="${iso}" title="${iso}${last ? " " + m.label : ""}"
      style="${m ? `background:${m.color}33;border-color:${m.color}` : ""}">
      <b>${d}</b>${m ? `<i>${m.emoji}</i>` : ""}${list.length > 1 ? `<u>+${list.length - 1}</u>` : ""}</button>`;
  }
  $("#xl-cal").innerHTML = html;
  $$("#xl-cal .xl-cal-cell[data-day]").forEach((c) => {
    c.onclick = () => xlRenderDayList(c.dataset.day);
  });
}

function xlRenderRecent(recent) {
  const box = $("#xl-recent");
  box.innerHTML = recent.map((e) => {
    const m = XL_MOODS[e.mood] || XL_MOODS.numb;
    return `<div class="item xl-entry" data-uuid="${esc(e.uuid)}">
      <span class="dim">${esc((e.date || "").slice(5))}${e.at ? " " + esc(e.at.slice(0, 5)) : ""}</span>
      <span class="xl-mood-dot" style="background:${m.color}"></span>
      <span class="grow">${m.emoji} ${m.label}
        <span class="dim">· 强度 ${e.intensity_percent}%(${XL_LEVELS[e.intensity_level] || ""})</span>
        ${e.note ? `<span class="dim">· ${esc(e.note.slice(0, 40))}</span>` : ""}</span>
      <button class="danger" data-del="${esc(e.uuid)}">删</button>
    </div>`;
  }).join("") || `<div class="empty">还没有记录, 从上面挑一个心情开始吧</div>`;
  $$("#xl-recent [data-del]").forEach((b) => {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      try {
        await call("xinlv_delete", b.dataset.del);
        toast("已删除(同步到所有设备)");
        await xlRefresh();
      } catch (e) { toast(e.message); }
    };
  });
}

function xlRenderDayList(dayIso) {
  const d = call("xinlv_month", xlMonth).then((data) => {
    const list = (data.entries || []).filter((e) => e.date === dayIso);
    xlRenderRecent(list.length ? list : data.recent || []);
  });
}

async function xlLoadProfile() {
  try {
    const p = await call("xinlv_profile");
    $("#xl-profile").innerHTML = `
      <div class="xl-profile-head">
        ${p.avatar_url
          ? `<img class="xl-avatar" src="${esc(p.avatar_url)}" alt="">`
          : `<div class="xl-avatar xl-avatar-ph">🌿</div>`}
        <div><b>${esc(p.username)}</b>
          <div class="muted small">${esc(p.bio || "这个人还没有写简介")}</div>
          <div class="muted small">加入于 ${esc((p.date_joined || "").slice(0, 10))}</div></div>
      </div>
      <div class="xl-stats">
        <span>🔥 连续 <b>${p.streak ?? 0}</b> 天</span>
        <span>📓 累计 <b>${p.total_entries ?? 0}</b> 条</span>
      </div>`;
    const badges = p.badges || [];
    $("#xl-badges").innerHTML = badges.map((b) => `
      <div class="xl-badge" title="${esc(b.desc || "")}">
        <img src="xinlv/badge_${b.days}.png" alt="${esc(b.name)}">
        <b>${esc(b.emoji)} ${esc(b.name)}</b>
        <span class="muted small">${esc(b.desc || "")}</span>
      </div>`).join("") || `<div class="empty">连续记录 5 天点亮第一枚 🌱</div>`;
  } catch (e) {
    $("#xl-profile").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

/* ---- 事件绑定(元素常驻, 只绑一次) ---- */
function xlBindMoodGrid(container, onPick) {
  container.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-mood]");
    if (!btn) return;
    xlMood = btn.dataset.mood;
    container.querySelectorAll("[data-mood]").forEach((b) =>
      b.classList.toggle("on", b.dataset.mood === xlMood));
    if (onPick) onPick(xlMood);
  });
}
xlBindMoodGrid($("#xl-mood-grid"));
$("#xl-mood-grid").innerHTML =
  Object.keys(XL_MOODS).map((k) => xlMoodBtn(k, false)).join("");

$("#xl-intensity").addEventListener("input", (e) => {
  $("#xl-intensity-pct").textContent = `${e.target.value}%`;
});

$("#xl-save").onclick = async () => {
  const msg = $("#xl-save-msg");
  if (!xlMood) { msg.textContent = "先选一个心情"; return; }
  const date = $("#xl-date").value;
  if (!date) { msg.textContent = "选个日期"; return; }
  msg.textContent = "保存中…";
  try {
    const r = await call("xinlv_add", date, $("#xl-time").value,
      xlMood, $("#xl-note").value,
      Number($("#xl-level").value), Number($("#xl-intensity").value));
    const s = r.sync || {};
    msg.textContent = s.error
      ? `已保存本地(${s.error}), 联网后自动同步`
      : "已记录 ✓";
    $("#xl-note").value = "";
    xlMood = r.entry.mood;
    xlNowInputs();          // 时间/日期回到当前时刻, 方便连记
    await xlRefresh();
    xlSetTab("rec");        // 自动跳到这份心情的推荐页
    setTimeout(() => { msg.textContent = ""; }, 2600);
  } catch (e) { msg.textContent = e.message; }
};

$("#xl-cal-prev").onclick = () => { xlShiftMonth(-1); };
$("#xl-cal-next").onclick = () => { xlShiftMonth(1); };
function xlShiftMonth(delta) {
  const [y, m] = xlMonth.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  xlMonth = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  xlRefresh().catch((e) => toast(e.message));
}

$("#view-xinlv").addEventListener("click", (e) => {
  const tab = e.target.closest("[data-xtab]");
  if (tab) xlSetTab(tab.dataset.xtab);
});

$("#xl-disclaimer").onclick = (e) => {
  e.preventDefault();
  call("open_external", "https://xin-lv.com/disclaimer/").catch(() => toast("请用浏览器打开 xin-lv.com/disclaimer/"));
};

$("#xl-login").onclick = async () => {
  const msg = $("#xl-msg");
  const u = $("#xl-user").value.trim(), p = $("#xl-pass").value;
  if (!u || !p) { msg.textContent = "填用户名和密码"; return; }
  msg.textContent = "登录中…";
  try {
    const r = await call("xinlv_login", u, p);
    msg.textContent = "";
    toast(`欢迎回来, ${r.username} 🔥连续 ${r.streak} 天`);
    loadXinlv().catch(() => {});
  } catch (e) { msg.textContent = e.message; }
};

$("#xl-register").onclick = async () => {
  const msg = $("#xl-msg");
  const u = $("#xl-user").value.trim(), p = $("#xl-pass").value;
  if (!u || !p) { msg.textContent = "填用户名和密码"; return; }
  if (!$("#xl-agree").checked) { msg.textContent = "请先阅读并勾选同意免责声明"; return; }
  msg.textContent = "注册中…";
  try {
    const r = await call("xinlv_register", u, p, true);
    msg.textContent = "";
    toast(`注册成功, 欢迎 ${r.username} 🌱`);
    loadXinlv().catch(() => {});
  } catch (e) { msg.textContent = e.message; }
};
/* 密码框获得焦点时切换到注册模式才显示注册按钮/勾选框 */
$("#xl-pass").addEventListener("focus", () => {
  $("#xl-register").classList.remove("hidden");
  $("#xl-agree-row").classList.remove("hidden");
}, { once: false });

$("#xl-logout").onclick = async () => {
  try {
    await call("xinlv_logout");
    toast("已退出(本设备令牌已注销, 记录保留在服务器)");
    xlMood = "";
    loadXinlv().catch(() => {});
  } catch (e) { $("#xl-me-msg").textContent = e.message; }
};

/* 推荐页: 选心情 → 立即出推荐(进 tab 时默认开心) */
async function xlLoadRecommend(mood) {
  const box = $("#xl-rec-result");
  const m = XL_MOODS[mood] || XL_MOODS.happy;
  box.innerHTML = `<div class="card"><div class="empty">正在为你准备「${m.label}」的推荐…</div></div>`;
  try {
    const r = await call("xinlv_recommend", mood);
    box.innerHTML = `
      <div class="card xl-rec-head" style="border-color:${m.color}">
        <h3>${m.emoji} ${esc(r.info?.label || m.label)}</h3>
        ${r.practice ? `<p class="xl-practice">${esc(r.practice)}</p>` : ""}
      </div>
      ${(r.songs || []).length ? `<div class="card"><div class="card-title">🎵 听点什么</div>
        ${r.songs.map((s) => `<div class="xl-song"><b>${esc(s.title)}</b>
          <span class="muted"> ${esc(s.artist || "")}</span>
          <audio controls preload="none" src="${esc(s.url)}"></audio></div>`).join("")}
      </div>` : ""}
      ${(r.activities || []).length ? `<div class="card"><div class="card-title">🌿 试试这些</div>
        ${r.activities.map((a) => `<div class="item"><span>${esc(a)}</span></div>`).join("")}
      </div>` : ""}
      ${(r.tips || []).length ? `<div class="card"><div class="card-title">💡 小知识</div>
        ${r.tips.map((t) => `<div class="xl-tip"><b>${esc(t.title)}</b>
          <p>${esc(t.content)}</p>${t.source ? `<span class="muted small">—— ${esc(t.source)}</span>` : ""}</div>`).join("")}
      </div>` : ""}
      ${r.video ? `<div class="card"><div class="card-title">📺 看点什么</div>
        <div class="form-row">${esc(r.video.title)}
          <button class="ghost" id="xl-open-video">打开视频 ↗</button></div></div>` : ""}`;
    const v = $("#xl-open-video");
    if (v) v.onclick = () =>
      call("open_external", r.video.url || r.video.embed_url).catch(() => {});
  } catch (e) {
    box.innerHTML = `<div class="card"><div class="empty">${esc(e.message)}</div></div>`;
  }
}
xlBindMoodGrid($("#xl-rec-moods"), (mood) => xlLoadRecommend(mood));

/* ================= phix 统一账号 · 云同步 =================
   一套账号打通「心履」与「PH Launcher / PLL」。数据在本地加密后才上传，
   服务器只存密文；DEK 只活在内存里，进程退出就没了，下次运行重新用口令解一次。
   协议细节见 D:\phix\phix-协议规范.md。 */
const PHIX_OBJECT_LABELS = {
  "settings.accounts": "四平台账号（Edupage / ManageBac / 邮箱 / 心履）",
  "settings.lessons": "选课（教学组）",
  "settings.ui": "界面排序偏好",
  "settings.ai": "AI 供应商与 Key",
  schedule: "日程",
  timetable: "课表",
  school: "学校数据快照（课表 / 作业 / 邮箱摘要）",
  profile: "个人资料（头像 / 显示名）",
  mood: "心情记录",
};
const PHIX_DEFAULT_OBJECTS = ["settings.accounts", "settings.lessons",
  "settings.ui", "schedule", "timetable", "school", "profile", "mood"];

let phixState = null;
/*: 自动选中的 phix 服务器地址（界面不让用户填，见 phixShowResolvedServer）。 */
let phixResolvedServer = "";

/** 把"会自动连哪台服务器"问出来显示在界面上（不让用户填，但要让他看得见）。
 *  探测只在第一次做（结果缓存住）—— 内网那台 0.1s 就答，不通时才回落到 phix.ing。 */
async function phixShowResolvedServer() {
  if (!phixResolvedServer) {
    try {
      const r = await call("phix_resolve_server");
      phixResolvedServer = (r && r.server) || "";
    } catch (e) { phixResolvedServer = ""; }
  }
  const text = phixResolvedServer
    ? `服务器：${phixResolvedServer}`
    : "服务器：自动选择（内网自建 → phix.ing）";
  ["#phix-server-line", "#ob-server-line"].forEach((id) => {
    const el = $(id); if (el) el.textContent = text;
  });
  return phixResolvedServer;
}

async function phixBusy(btn, fn) {
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "处理中…"; }
  try {
    return await fn();
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old; }
  }
}

function phixMsg(text, isErr) {
  const el = $("#phix-login-msg");
  const el2 = $("#phix-sync-msg");
  [el, el2].forEach((n) => { if (n) n.textContent = ""; });
  const target = (phixState && phixState.logged_in) ? el2 : el;
  if (target) { target.textContent = text || ""; target.style.color = isErr ? "#c0392b" : ""; }
}

async function phixRefresh() {
  const st = await call("phix_status");
  phixState = st;
  $("#phix-login-box").hidden = !!st.logged_in;
  $("#phix-main-box").hidden = !st.logged_in;
  if (!st.logged_in) {
    /* 服务器地址不让用户填（2026-09-21 用户要求，与 PHL 一致）：这里只把**探测结果**
       如实显示出来；没探测到就显示"自动选择中…"。 */
    const line = $("#phix-server-line");
    if (line) {
      line.textContent = st.server
        ? `服务器：${st.server}`
        : "服务器：自动选择（内网自建 → phix.ing）";
    }
    phixShowResolvedServer();
    $("#phix-username").value = st.username || "";
    const sl = $("#phix-sessions-list");
    if (sl) sl.innerHTML = "";               // 登出后别留着上一份设备列表
    phixRenderConflicts([]);                 // 也别留着上一个人的冲突记录
    phixRenderAccountId();
    const dn = $("#phix-display-name");
    if (dn) dn.value = "";
    phixRenderTransportWarning(st);
    return st;
  }
  const when = st.last_sync_at ? String(st.last_sync_at).slice(0, 16).replace("T", " ") : "还没有同步过";
  const mode = st.key_mode === "syncphrase" ? "独立同步口令（服务器也解不开）" : "登录密码";
  /* 网线加密（应用层）：开了之后**不接 HTTPS 也是安全的**——网上只有密文 */
  const wire = st.encrypted
    ? `<span class="phix-wire on">🔐 网线加密：已启用</span>`
    : (st.e2e ? `<span class="phix-wire off">网线加密：未生效</span>`
              : `<span class="phix-wire off">网线加密：已关闭</span>`);
  $("#phix-status-line").innerHTML =
    `<b>${esc(st.username)}</b>`
    + (st.user_id ? ` <span class="muted small">ID ${esc(st.user_id)}</span>` : "")
    + ` <span class="muted small">· ${esc(st.server)} · 设备「${esc(st.device)}」`
    + ` · 加密方式：${esc(mode)} · 上次同步：${esc(when)}</span> `
    + wire
    + (st.unlocked ? "" : ` <span class="phix-lock">🔒 未解锁</span>`);
  $("#phix-locked").hidden = !!st.unlocked;
  if (!st.unlocked) phixRenderLocked(st);
  $("#phix-auto").checked = !!st.auto_sync;
  $("#phix-interval").value = st.sync_interval_minutes || 10;
  const objs = st.objects && st.objects.length ? st.objects : PHIX_DEFAULT_OBJECTS;
  /* 标题（含已选数量）在 <summary> 上，容器里只放勾选框 —— 折叠时也能一眼看到选了几项。
     计数只认 PHIX_OBJECT_LABELS 里有的名字，避免把界面没列出的对象算进"已选"。 */
  const objKeys = Object.keys(PHIX_OBJECT_LABELS);
  const objSel = objKeys.filter((k) => objs.includes(k));
  $("#phix-objs").innerHTML = objKeys.map((k) =>
    `<label class="chk phix-obj"><input type="checkbox" data-obj="${esc(k)}"${objs.includes(k) ? " checked" : ""}> ${esc(PHIX_OBJECT_LABELS[k])}</label>`).join("");
  const objSum = $("#phix-objs-sum");
  if (objSum) objSum.textContent = `（已选 ${objSel.length} / ${objKeys.length}）`;
  if (st.recovery_code) {
    $("#phix-recovery").hidden = false;
    $("#phix-recovery-code").textContent = st.recovery_code;
  }
  phixRenderConflicts(((st.state || {}).conflicts) || []);
  phixRenderTransportWarning(st);
  phixRenderAccountId();
  phixLoadSessions();      // 设备列表异步补上，不挡着状态渲染
  phixLoadProfile();       // 异步加载头像
  return st;
}

/* 冲突不再只是"提一句"：每条给出两边各有多少内容，并允许一键定方向。
   冲突数据来自 `phix_status().state.conflicts`（后端每轮记末 50 条、这里回末 10 条）。 */
function phixRenderConflicts(conf) {
  const box = $("#phix-conflicts");
  if (!box) return;
  if (!conf.length) { box.innerHTML = ""; return; }
  const rows = conf.map((c) => {
    const vals = [];
    if (c.local !== undefined && c.local !== null) vals.push("本地：" + c.local);
    if (c.remote !== undefined && c.remote !== null) vals.push("账号：" + c.remote);
    return `<div class="phix-conflict"><span class="grow">${esc(c.object || c.path || "")}
      <span class="dim">${esc(c.note || "")}</span>`
      + (vals.length ? `<span class="phix-cf-vals">${esc(vals.join(" · "))}</span>` : "")
      + `</span></div>`;
  }).join("");
  box.innerHTML =
    `<div class="card-title" style="margin-top:10px">需要你留意的冲突（数据都还在，没有被丢）</div>`
    + rows
    + `<div class="phix-choice-btns">
         <button class="ghost" id="phix-cf-local">用本地覆盖账号</button>
         <button class="ghost" id="phix-cf-remote">用账号覆盖本地</button>
         <span class="muted small">整体定一次方向，两边就不再来回吵</span>
       </div>`;
  const l = $("#phix-cf-local"), r = $("#phix-cf-remote");
  if (l) l.onclick = (e) => phixBusy(e.target, () => phixApplyDirection("local"));
  if (r) r.onclick = (e) => phixBusy(e.target, () => phixApplyDirection("remote"));
}

/* 按选定的方向真同步一轮（会写本地文件、也会推云端）。

   注意**不传 force**：`SyncManager` 的并发护栏会在「另一个程序（PHL）正在运行」
   时跳过这一轮，避免两边抢写同一份 `data/Schedule`。跳过了也照样报出来。 */
async function phixApplyDirection(prefer) {
  const label = prefer === "local" ? "用本地覆盖 phix 账号" : "用 phix 账号覆盖本地";
  if (!confirm(`${label}？\n\n两边同名的数据会以被选中的那一边为准，另一边的那些内容会被替换掉。`)) return;
  try {
    const r = await call("phix_sync", false, prefer);
    await phixRefresh();
    phixMsg(`已按「${label}」同步：` + phixSummary(r.summary));
    Store.drop("home"); Store.drop("courses"); Store.drop("mail");
  } catch (err) { phixMsg(err.message, true); }
}

/* 登录之后该不该问用户选方向：本地和账号里**同一个对象都有内容**时就要问。
   不弹窗（用户可能不在）时一律退回最安全的"自动合并"。 */
async function phixAskDirection(probe) {
  const box = $("#phix-choice");
  if (!box) return "merge";
  const names = (probe.both || []).map((n) => PHIX_OBJECT_LABELS[n] || n);
  const what = $("#phix-choice-what");
  if (what) {
    what.textContent = "本地有：" + names.join("、")
      + "。这些内容在你的 phix 账号里也有 —— 要按哪边为准？";
  }
  box.classList.remove("hidden");
  return await new Promise((resolve) => {
    const finish = (v) => {
      box.classList.add("hidden");
      ["#phix-choice-merge", "#phix-choice-local", "#phix-choice-remote"]
        .forEach((id) => { const el = $(id); if (el) el.onclick = null; });
      resolve(v);
    };
    $("#phix-choice-merge").onclick = () => finish("merge");
    $("#phix-choice-local").onclick = () => {
      if (!confirm("用本地数据覆盖 phix 账号？\n\n账号里这些对象的内容会被本机版本替换。")) return;
      finish("local");
    };
    $("#phix-choice-remote").onclick = () => {
      if (!confirm("用 phix 账号的数据覆盖本地？\n\n本机这些对象的内容会被账号里的版本替换。")) return;
      finish("remote");
    };
  });
}

/* 登录成功后统一的同步入口：先探测 → 必要时问方向 → 再同步 */
async function phixLoginSync() {
  let prefer = "merge";
  try {
    const probe = await call("phix_sync_probe");
    if (probe && probe.needs_choice) {
      phixMsg("本地和账号里都有数据，先选一下以哪边为准…");
      prefer = await phixAskDirection(probe);
    }
  } catch (err) {
    // 探测失败（后端版本旧 / 网络抖）绝不能挡住登录本身 → 退回安全的自动合并
    prefer = "merge";
  }
  const r = await call("phix_sync", false, prefer);
  await phixRefresh();
  phixMsg(phixSummary(r.summary));
  Store.drop("home"); Store.drop("courses"); Store.drop("mail");
}

/* 已登录但没解锁：**说清楚要输哪一个口令**。
   - 简单模式：DEK 用登录密码包裹 → 输登录密码
   - 强模式：DEK 用独立同步口令包裹 → 输同步口令
   程序重启后密钥不在内存里了（这是有意的设计），所以重启后第一次同步前要输一次。 */
function phixRenderLocked(st) {
  const desc = $("#phix-locked-desc");
  const inp = $("#phix-unlock-pass");
  const strong = (st.key_mode === "syncphrase");
  if (desc) {
    desc.textContent = strong
      ? "：这个账号用的是独立同步口令，连服务端都解不开你的数据。"
        + "程序重启后要再输一次它才能同步（口令只在本机用来解密钥，不会外发）。"
      : "：程序重启后密钥不在内存里了，输入一次你的登录密码即可继续同步"
        + "（口令只在本机解密钥，不会外发；云端密文一个字节都不会动）。";
  }
  if (inp) inp.placeholder = strong ? "独立同步口令" : "登录密码";
}

/* 账号信息区：ID / 账号名 / 本机设备 / 加密方式 —— 登录后一眼看到"我是谁" */function phixRenderAccountId() {
  const el = $("#phix-account-id");
  if (!el) return;
  const st = phixState || {};
  if (!st.logged_in) { el.textContent = ""; return; }
  const bits = [];
  if (st.user_id) bits.push("ID " + st.user_id);
  if (st.username) bits.push("@" + st.username);
  if (st.device) bits.push("本机 " + st.device);
  if (st.key_mode) bits.push(st.key_mode === "syncphrase" ? "独立同步口令" : "登录密码");
  el.textContent = bits.join(" · ");
}

/* 明文 HTTP 到非本机 → 登录口令会明文过网线，必须显眼提示。
   简单模式下拿到口令等于能解开全部数据；切成"独立同步口令"后才安全。 */
function phixRenderTransportWarning(st) {
  const box = $("#phix-transport-warn");
  if (!box) return;
  if (!st.insecure_transport) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `⚠️ <b>这台服务器走的是明文 HTTP（${esc(st.server)}）</b>，` +
    `登录口令会在网络上明文传输。<br>` +
    (st.key_mode === "syncphrase"
      ? `好在当前账号用的是<b>独立同步口令</b>：就算口令被抓走，对方也解不开你的数据。`
      : `当前是<b>简单模式</b>——口令被抓走就等于云端数据全被解开。` +
        `建议在下面的「高级与安全设置」里<b>改用独立同步口令</b>，` +
        `或者等接入 HTTPS 域名后再用。`);
}

/* ---------- 头像 / profile ---------- */
async function phixLoadProfile() {
  const wrap = $("#phix-avatar-wrap");
  const img = $("#phix-avatar-img");
  const name = $("#phix-avatar-name");
  if (!wrap || !img || !name) return;
  let p = { display_name: "", avatar: "" };
  try { p = await call("phix_profile_get"); } catch (e) { /* 读不到就按空处理 */ }
  /* 显示名没设过就**回落到账号名** —— 登录之后这里不该是一片"未设置" */
  name.textContent = String(p.display_name || "").trim()
    || (phixState && phixState.username) || "未设置";
  if (p.avatar) {
    img.innerHTML = `<img src="${esc(p.avatar)}" style="width:100%;height:100%;object-fit:cover" alt="头像">`;
  } else {
    img.innerHTML = `<span>👤</span>`;
  }
  const dn = $("#phix-display-name");
  if (dn && document.activeElement !== dn) dn.value = p.display_name || "";
  phixRenderAccountId();
}

function phixBindAvatar() {
  const wrap = $("#phix-avatar-wrap");
  const file = $("#phix-avatar-file");
  if (!wrap || !file) return;
  wrap.onclick = () => file.click();
  file.onchange = async () => {
    const f = file.files && file.files[0];
    if (!f) return;
    if (f.size > 200 * 1024) {
      toast("头像文件不能超过 200KB（当前 " + Math.round(f.size / 1024) + "KB）");
      file.value = "";
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      const dataUrl = reader.result;
      try {
        await call("phix_profile_save", JSON.stringify({ avatar: dataUrl }));
        await phixLoadProfile();
        toast("头像已保存");
      } catch (e) { toast("保存头像失败：" + e.message); }
    };
    reader.readAsDataURL(f);
    file.value = "";
  };
}

/* ---------- 登录设备 / 会话（P3 的 /auth/devices） ----------
   一次登录 = 一个服务端会话。列出设备名与最近活动时间，可以注销某一台
   （那台机器的访问令牌立刻失效）或一次注销其它全部。
   **拿不到列表就安静降级** —— 绝不能让设备列表拖垮整个 phix 面板。 */
/* 同一个设备名算同一台（用户 2026-09-21 要求）。
   网页端每次登录都会留下一条 device="website"（见 website/server.py 的登录 body），
   官网侧另有 "官网" / "官网 SSO" / "官网迁移" / "官网 → 心履" 几种写法 ——
   这些全部并成一行，否则列表会被浏览器的历史会话刷成一长串。 */
const PHIX_WEBSITE_KEY = "__website__";
const PHIX_WEBSITE_LABEL = "phix 官网（浏览器）";

function sessGroupKey(s) {
  const name = String((s && s.device) || "").trim();
  return (/^(website|官网)/i.test(name)) ? PHIX_WEBSITE_KEY : (name || "未命名设备");
}

async function phixLoadSessions() {
  const box = $("#phix-sessions-list");
  if (!box) return;
  if (!phixState || !phixState.logged_in) { box.innerHTML = ""; return; }
  const api = (window.pywebview && window.pywebview.api) || {};
  if (typeof api.phix_devices !== "function") {
    box.innerHTML = `<div class="muted small">（当前后端没有设备列表接口）</div>`;
    return;
  }
  try {
    phixRenderSessions(await call("phix_devices"));
  } catch (err) {
    box.innerHTML = `<div class="muted small">设备列表拿不到：${esc(err.message)}</div>`;
  }
}

function phixRenderSessions(d) {
  const box = $("#phix-sessions-list");
  if (!box) return;
  const sum = $("#phix-sess-sum");
  const rows = (d && d.sessions) || [];
  const stamp = (s) => String((s && (s.last_seen_at || s.created_at)) || "");
  const when = (iso) => (iso.slice(0, 16).replace("T", " ") || "—");
  if (!rows.length) {
    if (sum) sum.textContent = "";
    box.innerHTML = `<div class="muted small">这台服务器没报出会话列表。</div>`;
    return;
  }

  /* 归组：保持首次出现的顺序（服务端按时间倒序给，所以每组第一条就是最新的） */
  const order = [];
  const groups = new Map();
  rows.forEach((s) => {
    const key = sessGroupKey(s);
    if (!groups.has(key)) { groups.set(key, []); order.push(key); }
    groups.get(key).push(s);
  });

  box.innerHTML = order.map((key) => {
    const list = groups.get(key);
    const latest = list.reduce((a, s) => (stamp(s) > a ? stamp(s) : a), "");
    const alive = list.filter((s) => !s.revoked);
    const gone = list.length - alive.length;
    const bits = [];
    if (list.length > 1) bits.push(`${list.length} 个会话`);
    if (gone) bits.push(`${gone} 个已注销`);
    const tags = (list.some((s) => s.current) ? `<span class="phix-sess-tag on">本机</span>` : "")
      + (alive.length === 0 ? `<span class="phix-sess-tag off">已注销</span>` : "")
      + (list.some((s) => s.dpop_bound) ? `<span class="phix-sess-tag">已绑密钥</span>` : "");
    /* 整组里「还能注销」的会话（非本机、未注销）一次全部注销 */
    const ids = alive.filter((s) => !s.current && s.id !== undefined && s.id !== null)
                     .map((s) => s.id);
    const btn = ids.length
      ? `<button class="ghost" data-revoke="${esc(ids.join(","))}">注销</button>` : "";
    return `<div class="phix-sess"><span class="grow">`
      + esc(key === PHIX_WEBSITE_KEY ? PHIX_WEBSITE_LABEL : key)
      + (bits.length ? `<span class="dim"> · ${esc(bits.join(" · "))}</span>` : "")
      + `<span class="dim"> · 最近活动 ${esc(when(latest))}</span></span>${tags}${btn}</div>`;
  }).join("");

  if (sum) {
    const live = rows.filter((s) => !s.revoked).length;
    sum.textContent = `（${order.length} 台 · ${live} 个活跃会话 · 一次登录 = 一个会话）`;
  }

  box.querySelectorAll("button[data-revoke]").forEach((b) => {
    b.onclick = (e) => phixBusy(e.target, async () => {
      const ids = String(b.dataset.revoke || "").split(",").filter((x) => x !== "").map(Number);
      if (!ids.length) return;
      if (!confirm(ids.length > 1
        ? `注销这台设备上的 ${ids.length} 个会话？\n\n那边的登录会全部立刻失效（不影响本机）。`
        : "注销这台设备？\n\n它那边的登录会立刻失效（不影响本机）。")) return;
      let last = null, failed = false;
      for (const id of ids) {
        try { last = await call("phix_revoke_device", id, false); }
        catch (err) { phixMsg(err.message, true); failed = true; break; }
      }
      if (last) phixRenderSessions(last);
      if (!failed) phixMsg(ids.length > 1 ? `已注销 ${ids.length} 个会话` : "那台设备已注销");
    });
  });
}

function phixBind() {
  const on = (id, fn) => { const el = $(id); if (el) el.onclick = fn; };

  on("#phix-login", (e) => phixBusy(e.target, async () => {
    const username = $("#phix-username").value.trim();
    const password = $("#phix-password").value;
    if (!username || !password) { phixMsg("账号、密码都要填", true); return; }
    try {
      /* 服务器自动选择、口令不在登录页问（用户 2026-09-21 要求）。
         强模式账号登录后是锁着的 → 下面的"未解锁"面板负责解锁。 */
      await call("phix_login", "", username, password, "");
      $("#phix-password").value = "";
      await phixRefresh();
      phixMsg("登录成功，正在检查本地和账号里的数据…");
      /* 本地有数据 + 账号里也有数据时，这里会先问用户"以哪边为准" */
      await phixLoginSync();
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-save-name", (e) => phixBusy(e.target, async () => {
    try {
      const p = await call("phix_profile_save", JSON.stringify({
        display_name: $("#phix-display-name").value.trim(),
      }));
      await phixLoadProfile();
      phixMsg(p.display_name
        ? `显示名已保存并推到账号：${p.display_name}`
        : "显示名已清空（头像区回落到账号名）");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-register", (e) => phixBusy(e.target, async () => {
    const username = $("#phix-username").value.trim();
    const password = $("#phix-password").value;
    if (!username || password.length < 6) {
      phixMsg("注册需要：账号、密码（至少 6 位）", true); return;
    }
    const where = phixResolvedServer || "phix 服务器（自动选择）";
    if (!confirm(`确定要在 ${where} 注册新账号「${username}」吗？\n\n注册后会出现一串恢复码，请立刻抄下来。`)) return;
    try {
      await call("phix_register", "", username, password, "password");
      $("#phix-password").value = "";
      await phixRefresh();
      phixMsg("注册成功！请把上面的恢复码抄到安全的地方。");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-sync", (e) => phixBusy(e.target, async () => {
    try {
      const r = await call("phix_sync");
      await phixRefresh();
      phixMsg(phixSummary(r.summary));
      Store.drop("home"); Store.drop("courses"); Store.drop("mail");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-preview", (e) => phixBusy(e.target, async () => {
    try {
      const r = await call("phix_sync_preview");
      phixMsg("预览：" + phixSummary(r.summary) + "（没有写入任何文件）");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-logout", async () => {
    if (!confirm("退出登录？\n\n本机保存的令牌会被清掉（云端数据不受影响，下次用账号密码登录即可）。")) return;
    try { await call("phix_logout"); await phixRefresh(); phixMsg("已退出登录"); }
    catch (err) { phixMsg(err.message, true); }
  });

  on("#phix-unlock", (e) => phixBusy(e.target, async () => {
    try {
      await call("phix_unlock", $("#phix-unlock-pass").value);
      $("#phix-unlock-pass").value = "";
      await phixRefresh();
      phixMsg("已解锁，可以同步了");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-save-opts", (e) => phixBusy(e.target, async () => {
    const objs = $$("#phix-objs input[data-obj]").filter((c) => c.checked).map((c) => c.dataset.obj);
    if (!objs.length) { phixMsg("至少要选一项要同步的内容", true); return; }
    try {
      await call("phix_settings_save", JSON.stringify({
        auto_sync: $("#phix-auto").checked,
        sync_interval_minutes: Number($("#phix-interval").value) || 10,
        objects: objs,
      }));
      await phixRefresh();
      phixMsg("同步设置已保存");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-change-pass", (e) => phixBusy(e.target, async () => {
    const o = $("#phix-old-pass").value, n = $("#phix-new-pass").value;
    if (n.length < 6) { phixMsg("新密码至少 6 位", true); return; }
    try {
      await call("phix_change_password", o, n);
      $("#phix-old-pass").value = ""; $("#phix-new-pass").value = "";
      /* 改密码只换"包裹密钥的口令"，云端密文一个字节都不动；
         再跑一轮同步确认新凭证在账号上真的能用（失败也不影响已经改成的密码）。 */
      let extra = "";
      try {
        const r = await call("phix_sync", false);
        extra = "，并已用新密码同步一次（" + phixSummary(r.summary) + "）";
      } catch (err) {
        extra = "。同步这一步没成功（" + err.message + "），但密码本身已经改好了";
      }
      await phixRefresh();
      phixMsg("密码已在账号上改好，云端密文一个字节都没动，别的设备照常能同步" + extra);
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-set-sp", (e) => phixBusy(e.target, async () => {
    const lp = $("#phix-sp-login").value, sp = $("#phix-sp-new").value;
    if (sp.length < 6) { phixMsg("同步口令至少 6 位", true); return; }
    if (!confirm("改用独立同步口令后：\n· 连服务器都拿不到你的数据（真正端到端）\n· 每次运行程序都要输一次这个口令\n\n确定吗？")) return;
    try {
      await call("phix_set_passphrase", lp, sp);
      $("#phix-sp-login").value = ""; $("#phix-sp-new").value = "";
      await phixRefresh();
      phixMsg("已切换。以后同步时请在登录框的「独立同步口令」里输入它。");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-recovery-done", () => { $("#phix-recovery").hidden = true; });

  on("#phix-recover", (e) => phixBusy(e.target, async () => {
    const user = $("#phix-rc-username").value.trim();
    const codeTxt = $("#phix-rc-code").value.trim();
    const np = $("#phix-rc-newpass").value;
    if (!user || !codeTxt || np.length < 6) {
      phixMsg("要填：账号、恢复码、新的登录密码（至少 6 位）", true); return;
    }
    if (!confirm("用恢复码重设密码？\n\n· 恢复码只在本机使用，不会上传\n· 云端数据一个字节都不会动")) return;
    const server = phixResolvedServer || (phixState && phixState.server) || "";
    try {
      await call("phix_recover", server, user, codeTxt, np);
      $("#phix-rc-code").value = ""; $("#phix-rc-newpass").value = "";
      phixMsg("密码已重设，用新密码登录即可（云端数据没动过）");
    } catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-open-dir", (e) => phixBusy(e.target, async () => {
    try { const d = await call("phix_open_data_dir"); phixMsg("已打开 " + d.path); }
    catch (err) { phixMsg(err.message, true); }
  }));

  on("#phix-sess-refresh", (e) => phixBusy(e.target, () => phixLoadSessions()));

  on("#phix-sess-revoke-others", (e) => phixBusy(e.target, async () => {
    if (!confirm("注销除本机以外的全部设备？\n\n其它机器上的登录都会立刻失效。")) return;
    try {
      phixRenderSessions(await call("phix_revoke_device", 0, true));
      phixMsg("其它设备都已注销");
    } catch (err) { phixMsg(err.message, true); }
  }));
}

function phixSummary(s) {
  if (!s) return "";
  if (s.skipped) return s.skipped;
  const parts = [];
  if ((s.pulled || []).length) parts.push(`拉取 ${s.pulled.length} 项`);
  if ((s.pushed || []).length) parts.push(`上传 ${s.pushed.length} 项`);
  if (!parts.length) parts.push("没有需要同步的变化");
  if (s.conflicts) parts.push(`${s.conflicts} 处冲突已记录`);
  if ((s.errors || []).length) parts.push(`${s.errors.length} 项出错：` + s.errors[0]);
  return parts.join("，");
}

/* ================= 自绘窗口控件（无边框窗口） =================
 * 用户 2026-09-21：窗口控件不要系统那条单独的标题栏，要像 PHL 那样是软件的一部分；
 * 并且「窗口四周有一圈白边，去掉」「侧边栏往上移，不要让上面有一块空着」。
 *
 * 所以：
 *   · 右上角三个按钮浮在内容之上（不占布局 → 侧栏顶到最上面，没有空条）；
 *   · 四周 8 个透明把手做缩放（不用 WS_THICKFRAME —— 它会围出一圈白边）；
 *   · `.drag-zone`（侧栏空白处 + 页面标题那一行）按下 → 系统原生拖动，双击 → 最大化/还原。
 * 任何一步失败都安静放过：窗口控件坏了也不能让界面崩。
 */
function bindWindowControls() {
  const callWin = async (name, ...args) => {
    try { return await call(name, ...args); } catch (e) { return { ok: false, error: e.message }; }
  };

  function paintMaxGlyph(maximized) {
    const icon = $("#win-max-icon");
    const btn = $("#win-max");
    if (icon) {
      // 最大化时画"两个叠起来的小方块"（还原图标），否则画一个方框
      icon.innerHTML = maximized
        ? '<rect x=".6" y="2.6" width="6.8" height="6.8" rx="1"/>'
          + '<path d="M2.6 2.6V1.6a1 1 0 0 1 1-1h4.8a1 1 0 0 1 1 1v4.8a1 1 0 0 1-1 1h-1"/>'
        : '<rect x=".6" y=".6" width="8.8" height="8.8" rx="1.2"/>';
    }
    if (btn) btn.title = maximized ? "还原" : "最大化";
  }

  async function toggleMax() {
    const r = await callWin("win_maximize_toggle");
    paintMaxGlyph(!!r.maximized);
  }

  const min = $("#win-min");
  const max = $("#win-max");
  const close = $("#win-close");
  if (min) min.onclick = () => callWin("win_minimize");
  if (max) max.onclick = () => toggleMax();
  if (close) close.onclick = () => callWin("win_close");
  callWin("win_state").then((r) => paintMaxGlyph(!!(r && r.maximized)));

  /* ---- 拖动区：按下列表/按钮这些可交互的东西要让位，只有空白处才拖窗口 ---- */
  const INTERACTIVE = "button,a,input,select,textarea,label,.logo,.go-card,[role=button],[data-go]";
  document.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    const zone = ev.target.closest(".drag-zone");
    if (!zone) return;
    if (ev.target.closest(INTERACTIVE)) return;
    callWin("win_drag_start");
  });
  document.addEventListener("dblclick", (ev) => {
    const zone = ev.target.closest(".drag-zone");
    if (!zone) return;
    if (ev.target.closest(INTERACTIVE)) return;
    toggleMax();
  });

  /* ---- 缩放把手：按下取一次窗口位置，之后按指针位移算新边界 ---- */
  const MIN_W = 1000, MIN_H = 640;
  document.querySelectorAll(".rz").forEach((handle) => {
    handle.addEventListener("mousedown", async (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      const edge = handle.getAttribute("data-rz") || "";
      const start = await callWin("win_get_bounds");
      if (!start.ok || !start.bounds) return;
      const [ox, oy, ow, oh] = start.bounds;
      const sx = ev.screenX, sy = ev.screenY;
      let busy = false, pending = null;
      document.body.classList.add("resizing");

      const push = (x, y, w, h) => {
        pending = [x, y, w, h];
        if (busy) return;
        busy = true;
        callWin("win_set_bounds", ...pending).then(() => {
          busy = false;
          const last = pending;
          if (last && (last[0] !== x || last[1] !== y || last[2] !== w || last[3] !== h)) {
            push(...last);
          }
        });
      };
      const onMove = (move) => {
        const dx = move.screenX - sx, dy = move.screenY - sy;
        let x = ox, y = oy, w = ow, h = oh;
        if (edge.includes("e")) w = Math.max(MIN_W, ow + dx);
        if (edge.includes("s")) h = Math.max(MIN_H, oh + dy);
        if (edge.includes("w")) { w = Math.max(MIN_W, ow - dx); x = ox + (ow - w); }
        if (edge.includes("n")) { h = Math.max(MIN_H, oh - dy); y = oy + (oh - h); }
        push(x, y, w, h);
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.classList.remove("resizing");
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  });
}

phixBind();
bindWindowControls();
phixBindAvatar();
