/* Hello! Pinghe launcher 前端逻辑: 路由 + 各视图加载 + 向导 + Agent */
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
  gradett: "年级课表", courses: "我的课程", mail: "平和邮箱",
  agent: "Agent 助手", settings: "设置",
};
let currentView = "home";
let ttOffset = 0;
let mailMode = 0;
let currentClassFilter = "";

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
$("#btn-refresh").addEventListener("click", () => show(currentView));

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
function ttLessonHtml(l, withTime) {
  return `<div class="tt-lesson ${l.cancelled ? "cancelled" : ""}">
    ${withTime ? `<span class="rm">${esc(l.start)}</span>` : ""}
    <b>${esc(l.subject)}</b>
    <span class="rm">${esc(l.room)}${l.teacher ? " · " + esc(l.teacher) : ""}</span>
  </div>`;
}

/* 按节次对齐的周课表: 行=时段(P1-P10/Lunch/晚自习), 列=周一到周日 */
function renderTimetable(d) {
  $("#tt-range").textContent = `${d.week[0].day} ~ ${d.week[6].day}`;
  const today = isoOf(new Date());

  /* 每天的课先按节次分桶 */
  const byDay = d.week.map((day) => {
    const cells = PERIODS.map(() => []);
    const other = [];
    (day.lessons || []).forEach((l) => {
      const pi = periodOf(l.start);
      if (pi >= 0) cells[pi].push(l);
      else other.push(l);
    });
    return { cells, other, day };
  });

  /* 表头: 空角 + 七天 */
  let html = `<div class="tt-head tt-corner"></div>`;
  html += byDay.map(({ day }) =>
    `<div class="tt-head ${day.day === today ? "today" : ""}">${esc(day.label)}</div>`).join("");

  PERIODS.forEach((p, pi) => {
    const busy = byDay.some((b) => b.cells[pi].length);
    const timeCell = `<div class="tt-time ${p.rest ? "rest" : ""}"><b>${esc(p.name)}</b>
      <span>${esc(p.start)}</span></div>`;
    /* Lunch/晚自习整周没课时渲染成一条横幅, 不占七列 */
    if (p.rest && !busy) {
      html += timeCell +
        `<div class="tt-restbar" style="grid-column:2/-1">${esc(p.name)} ${esc(p.start)} – ${esc(p.end)}</div>`;
      return;
    }
    html += timeCell;
    byDay.forEach((b) => {
      const ls = b.cells[pi];
      html += ls.length
        ? `<div class="tt-cell ${p.rest ? "rest" : ""}">${ls.map((l) => ttLessonHtml(l, false)).join("")}</div>`
        : `<div class="tt-cell ${p.rest ? "rest" : ""}"></div>`;
    });
  });

  /* 不在任何时段的课(如临时调课)归到"课外"一行 */
  if (byDay.some((b) => b.other.length)) {
    html += `<div class="tt-time"><b>课外</b></div>` +
      byDay.map((b) =>
        `<div class="tt-cell">${b.other.map((l) => ttLessonHtml(l, true)).join("")}</div>`).join("");
  }
  $("#tt-week").innerHTML = html;
}
function loadTimetable() {
  return swr(`tt|${ttOffset}`, TTL.tt,
    () => call("timetable_week", ttOffset), renderTimetable,
    () => { $("#tt-week").innerHTML = `<div class="tt-skeleton">${'<div class="skel-card"></div>'.repeat(7)}</div>`; },
    () => currentView === "timetable")();
}
$("#tt-prev").onclick = () => { ttOffset--; loadTimetable().catch((e) => toast(e.message)); };
$("#tt-next").onclick = () => { ttOffset++; loadTimetable().catch((e) => toast(e.message)); };
$("#tt-this").onclick = () => { ttOffset = 0; loadTimetable().catch((e) => toast(e.message)); };

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

/* ================= 年级课表 ================= */
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
function renderCourses(d) {
  const chips = [`<span class="chip ${!currentClassFilter ? "on" : ""}" data-cid="">全部</span>`]
    .concat(d.classes.map((c) =>
      `<span class="chip ${currentClassFilter === c.id ? "on" : ""}" data-cid="${c.id}" draggable="true">${esc(c.name.slice(0, 22))}</span>`));
  $("#co-chips").innerHTML = chips.join("");
  $$("#co-chips .chip").forEach((c) => {
    c.onclick = () => { currentClassFilter = c.dataset.cid; filterTasks(d); };
  });
  bindChipDrag(d);
  filterTasks(d);
  $("#co-grades").innerHTML = Object.entries(d.grades || {}).map(
    ([name, grade]) => `<div class="item"><span class="grow">${esc(name)}</span>
      ${badge(grade || "未出分", grade ? "green" : "")}</div>`).join("") ||
    `<div class="empty">暂无成绩数据</div>`;
  $("#co-link").href = "https://shph.managebac.cn/student";
}
/* 课程 chip 拖拽排序: "全部"固定首位, 松手后把新顺序持久化到后端 */
function bindChipDrag(d) {
  const wrap = $("#co-chips");
  let dragEl = null;
  const allChips = () => $$("#co-chips .chip");
  allChips().forEach((chip) => {
    if (!chip.dataset.cid) return; /* "全部" 固定第一位, 不参与拖拽 */
    chip.addEventListener("dragstart", () => {
      dragEl = chip;
      chip.classList.add("dragging");
    });
    chip.addEventListener("dragend", () => {
      chip.classList.remove("dragging");
      allChips().forEach((c) => c.classList.remove("drop-target"));
      dragEl = null;
      const order = allChips().map((c) => c.dataset.cid).filter(Boolean);
      d.classes.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
      call("course_save_order", JSON.stringify(order)).catch(() => {});
      filterTasks(d);
    });
  });
  wrap.addEventListener("dragover", (e) => {
    if (!dragEl) return;
    e.preventDefault();
    const rest = allChips().filter((c) => c.dataset.cid && c !== dragEl);
    let after = null;
    for (const c of rest) {
      const r = c.getBoundingClientRect();
      if (e.clientX < r.left + r.width / 2) { after = c; break; }
    }
    rest.forEach((c) => c.classList.remove("drop-target"));
    if (after) { wrap.insertBefore(dragEl, after); after.classList.add("drop-target"); }
    else wrap.appendChild(dragEl);
  });
}
function taskItemEl(t) {
  return swipeableItemEl("item ddl-item",
    `<span class="dim">${esc((t.due_at || "").slice(5, 16))}</span>
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
    $("#co-chips").innerHTML = "";
    $("#co-tasks").innerHTML = `<div class="empty">⏳ 正在同步 ManageBac…</div>`;
    $("#co-grades").innerHTML = "";
  },
  () => currentView === "courses");
function filterTasks(d) {
  const tasks = (d.tasks_upcoming || []).filter(
    (t) => !currentClassFilter || t.class_id === currentClassFilter);
  $("#co-tasks").innerHTML = "";
  if (!tasks.length) {
    $("#co-tasks").innerHTML = `<div class="empty">没有未截止的作业 (左滑作业条目可移除)</div>`;
    return;
  }
  const frag = document.createDocumentFragment();
  tasks.forEach((t) => frag.appendChild(taskItemEl(t)));
  $("#co-tasks").appendChild(frag);
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
        $("#ml-read").innerHTML = `
          <h3>${esc(m.subject)}</h3>
          <div class="muted small">${esc(m.from)} → ${esc(m.to)} · ${esc(m.date)}</div>
          <hr><div class="mail-body">${bodyHtml}</div>`;
        el.classList.remove("unread");
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
  refreshFiles();
  await refreshSessions();
}
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
  const d = await call("settings_get");
  $("#st-mb-url").value = d.managebac_base_url || "";
  $("#st-mb-email").value = d.managebac_email || "";
  $("#st-ep-user").value = d.edupage_username || "";
  $("#st-ep-sub").value = d.edupage_subdomain || "";
  $("#st-mail-email").value = d.mail_email || "";
  $("#st-mail-imap").value = d.mail_imap_host || "";
  $("#st-mail-smtp").value = d.mail_smtp_host || "";
  $("#st-mail-authcode").value = "";
  $("#st-grades-llm").checked = !!d.send_grades_to_llm;
  selectedLessonsCache = d.selected_lessons || [];
  $("#st-subjects").innerHTML = selectedLessonsCache.map((s) =>
    `<span class="chip on">${esc(s.subject)}${s.teacher ? " · " + esc(s.teacher) : ""}</span>`).join("") ||
    `<span class="muted">尚未选课</span>`;
  const ai = await call("ai_get");
  providersState = (ai.providers || []).map((p) => ({ ...p, api_key: "" }));
  renderProviderCards();
  renderActiveSelects(ai.active_provider_id, ai.active_model);
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
  const sel = [];
  $$("#sm-subjects input[type=checkbox]:checked").forEach((c) =>
    sel.push({ subject: c.dataset.sub, teacher: c.dataset.teacher }));
  const radios = {};
  $$("#sm-subjects input[type=radio]:checked").forEach((r) => {
    radios[r.dataset.sub] = { subject: r.dataset.sub, teacher: r.dataset.teacher };
  });
  Object.values(radios).forEach((r) => {
    if (!sel.some((x) => x.subject === r.subject)) sel.push(r);
  });
  try {
    const r = await call("wizard_save_selection", JSON.stringify(sel));
    selectedLessonsCache = sel;
    $("#st-subjects").innerHTML = sel.map((s) =>
      `<span class="chip on">${esc(s.subject)}${s.teacher ? " · " + esc(s.teacher) : ""}</span>`).join("") ||
      `<span class="muted">尚未选课</span>`;
    $("#subject-modal").classList.add("hidden");
    toast(`选课已更新(${r.selected} 门)`);
  } catch (e) { $("#sm-msg").textContent = `✗ ${e.message}`; }
};
function renderSmSubjects(filter) {
  const current = new Set(selectedLessonsCache.map((s) => `${s.subject}|${s.teacher}`));
  const f = (filter || "").trim().toLowerCase();
  $("#sm-subjects").innerHTML = smSubjects
    .filter((s) => !f || s.subject.toLowerCase().includes(f))
    .map((s) => {
      if (s.options.length === 1) {
        const o = s.options[0];
        const checked = current.has(`${s.subject}|${o.teacher}`) ? "checked" : "";
        return `<label class="subject-row"><input type="checkbox" data-sub="${esc(s.subject)}"
          data-teacher="${esc(o.teacher)}" ${checked}>
          <span class="grow"><b>${esc(s.subject)}</b>
          <span class="rooms">${esc(o.teacher)} ${esc(o.rooms.join(" "))}</span></span></label>`;
      }
      return `<div class="subject-row" style="flex-wrap:wrap">
        <b style="width:100%">${esc(s.subject)} <span class="muted small">(多个分组, 选你的)</span></b>
        ${s.options.map((o) => {
          const checked = current.has(`${s.subject}|${o.teacher}`) ? "checked" : "";
          return `<label class="subject-row" style="padding-left:26px">
            <input type="radio" name="smg:${esc(s.subject)}" data-sub="${esc(s.subject)}"
              data-teacher="${esc(o.teacher)}" ${checked}>
            <span class="grow">${esc(o.teacher)} <span class="rooms">${esc(o.rooms.join(" "))}</span></span>
          </label>`;
        }).join("")}</div>`;
    }).join("") || `<div class="empty">没有匹配的科目</div>`;
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
  // 每步都显示"进入 Hello! Pinghe launcher"按钮，允许随时完成向导
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
  const f = filter.trim().toLowerCase();
  $("#wz-subjects").innerHTML = wizSubjects
    .filter((s) => !f || s.subject.toLowerCase().includes(f))
    .map((s) => {
      if (s.options.length === 1) {
        const o = s.options[0];
        return `<label class="subject-row"><input type="checkbox" data-sub="${esc(s.subject)}"
          data-teacher="${esc(o.teacher)}" checked>
          <span class="grow"><b>${esc(s.subject)}</b>
          <span class="rooms">${esc(o.teacher)} ${esc(o.rooms.join(" "))}</span></span></label>`;
      }
      return `<div class="subject-row" style="flex-wrap:wrap">
        <b style="width:100%">${esc(s.subject)} <span class="muted small">(多个分组, 选你的)</span></b>
        ${s.options.map((o, i) => `<label class="subject-row" style="padding-left:26px">
          <input type="radio" name="g:${esc(s.subject)}" data-sub="${esc(s.subject)}"
            data-teacher="${esc(o.teacher)}" ${i === 0 ? "" : ""}>
          <span class="grow">${esc(o.teacher)} <span class="rooms">${esc(o.rooms.join(" "))}</span></span>
        </label>`).join("")}</div>`;
    }).join("") || `<div class="empty">没有匹配的科目</div>`;
}
$("#wz-sub-filter").addEventListener("input", (e) => renderSubjects(e.target.value));
$("#wz-sub-go").onclick = async () => {
  const sel = [];
  $$("#wz-subjects input[type=checkbox]:checked").forEach((c) =>
    sel.push({ subject: c.dataset.sub, teacher: c.dataset.teacher }));
  const radios = {};
  $$("#wz-subjects input[type=radio]:checked").forEach((r) => {
    radios[r.dataset.sub] = { subject: r.dataset.sub, teacher: r.dataset.teacher };
  });
  Object.values(radios).forEach((r) => {
    if (!sel.some((x) => x.subject === r.subject)) sel.push(r);
  });
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
      setRow("edupage", "working", "✓ 已连接 · 预载课表/年级课表…");
      const warm = [];
      warm.push(preload(`tt|0`, () => call("timetable_week", 0))());      // 我的课表
      warm.push(preload(`gt|${new Date().toISOString().slice(0, 10)}`,
        () => call("gradett_data", new Date().toISOString().slice(0, 10)))()); // 年级课表(最慢, 提前热身)
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
