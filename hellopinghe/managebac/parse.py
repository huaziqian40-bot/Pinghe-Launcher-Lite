"""ManageBac 学生端 HTML 解析器.

选择器来源(已在真实页面验证):
- 课程列表页 /student/classes/my: li.f-menu-submenu-item > a[href] > span.f-menu-submenu-link-title
  (来源: ManageBac-GPA-Scraper, MIT)
- DDL 页 /student/tasks_and_deadlines?view=...: 纯文本行解析,
  日期行格式 "Jan 5, 11:59 PM"(来源: managebac-mcp extractors.ts, 逻辑重写)
- 总评页 /student/classes/<id>/units: div.sidebar-items-list > div.cell
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

#: DDL 页日期行, 如 "Sep 12, 11:59 PM"
DUE_LINE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+"
    r"(\d{1,2}),\s+(\d{1,2}):(\d{2})\s*(AM|PM)$",
    re.IGNORECASE,
)

STATUS_LINE = re.compile(
    r"^(Pending|Submitted|Late|Missing|Overdue|Not Submitted|"
    r"Not Assessed Yet|Complete|Completed|Returned|Excused)$",
    re.IGNORECASE,
)

#: 页面上明确的“无课程”提示
NO_CLASSES_MARKER = "No classes found"


@dataclass
class Deadline:
    title: str
    course: str | None
    due_at: datetime | None
    due_text: str
    status: str | None
    category: str          # upcoming / past / overdue
    source_url: str


@dataclass
class Task:
    """单门课 core_tasks 页的一张作业卡."""

    class_id: str
    class_name: str
    task_id: str
    title: str
    href: str
    due_at: datetime | None
    due_text: str
    status: str | None         # Pending / Submitted / Late / ...
    category: str | None       # Summative / Formative
    kind: str | None           # Coursework / Final Exam / Quiz / ...
    past_due: bool
    can_submit: bool           # 卡片上是否还有 Submit Coursework 按钮
    score_text: str | None


def _infer_task_year(month: int, day: int, today: date, past_due: bool = False) -> int | None:
    """卡片只给 月/日, 需推断年份.

    利用 ManageBac 自己的 past-due 徽标定方向:
    - past_due  → 取"今天或之前"里最晚的那个年份(刚过去的那个学期)
    - 否则      → 取"今天或之后"里最早的年份(真正未截止的作业/考试)
    候选限定在 ±400 天内。
    """
    options: list[date] = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if abs((candidate - today).days) <= 400:
            options.append(candidate)
    if not options:
        return None
    if past_due:
        past = [d for d in options if d <= today]
        return max(past).year if past else max(options).year
    future = [d for d in options if d >= today]
    return min(future).year if future else min(options).year


def extract_task_cards(html: str, class_id: str = "", class_name: str = "",
                       today: date | None = None) -> list[Task]:
    """解析 /student/classes/<id>/core_tasks 页面的作业卡片(新版 UI)."""
    today = today or date.today()
    soup = BeautifulSoup(html, "html.parser")
    tasks: list[Task] = []

    for card in soup.select("div.fusion-card-item.short-assignment"):
        link = card.select_one(".h4.title a[href]")
        if link is None:
            continue
        href = link.get("href") or ""
        task_id_m = re.search(r"/core_tasks/(\d+)", href)

        badge = card.select_one(".date-badge")
        month_txt = badge.select_one(".month").get_text(strip=True) if badge else ""
        day_txt = badge.select_one(".day").get_text(strip=True) if badge else ""
        past_due = "past-due" in (badge.get("class") or []) if badge else False
        month = MONTHS.get(month_txt[:3].upper())
        day = int(day_txt) if day_txt.isdigit() else None

        due_span = card.select_one(".due-date")
        due_text = due_span.get_text(" ", strip=True) if due_span else ""
        due_at: datetime | None = None
        if month and day:
            year = _infer_task_year(month, day, today, past_due)
            if year is not None:
                hour, minute = 23, 59
                time_m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", due_text, re.IGNORECASE)
                if time_m:
                    hour = int(time_m.group(1)) % 12
                    minute = int(time_m.group(2))
                    if time_m.group(3).upper() == "PM":
                        hour += 12
                due_at = datetime(year, month, day, hour, minute)

        status_el = card.select_one(".badge .badge-label")
        labels = [el.get_text(strip=True) for el in card.select(".labels-set div.label")]
        category = next((t for t in labels if t.lower() in ("formative", "summative")), None)
        kind = next((t for t in labels if t.lower() not in ("formative", "summative")), None)

        score_el = card.select_one(".assessment.task-score")
        score_text = " ".join(score_el.get_text(" ", strip=True).split()) if score_el else None

        tasks.append(Task(
            class_id=class_id,
            class_name=class_name,
            task_id=task_id_m.group(1) if task_id_m else "",
            title=link.get_text(strip=True)[:200],
            href=href,
            due_at=due_at,
            due_text=due_text,
            status=status_el.get_text(strip=True) if status_el else None,
            category=category,
            kind=kind,
            past_due=past_due,
            can_submit=any(
                re.search("Submit Coursework", a.get_text(" ", strip=True), re.IGNORECASE)
                for a in card.find_all("a")
            ),
            score_text=score_text,
        ))
    return tasks


#: 卡片上的操作链接文本(不是课程名, 必须忽略)
_ACTION_WORDS = {"leave", "units", "tasks", "updates", "grades", "class", "classes", "course"}


def extract_classes(html: str) -> dict[str, str]:
    """从课程列表页解析 {class_id: 课程名}.

    一张课程卡片会同时产生多个链接(标题链接 + Units/Tasks/Leave 等操作链接),
    因此对同一 class_id 保留最长文本(即课程全名), 并忽略纯动作词。
    """
    soup = BeautifulSoup(html, "html.parser")
    classes: dict[str, str] = {}

    def _offer(cid: str, name: str) -> None:
        name = name.strip()
        if not name or len(name) < 2 or name.lower() in _ACTION_WORDS:
            return
        old = classes.get(cid)
        if old is None or len(name) > len(old):
            classes[cid] = name[:150]

    # 版式 A: 左侧菜单(GPA-Scraper 验证的选择器)
    for a in soup.select("li.f-menu-submenu-item a[href]"):
        href = a.get("href") or ""
        match = re.search(r"/student/classes/(\d+)", href)
        title = a.select_one("span.f-menu-submenu-link-title")
        if match and title:
            _offer(match.group(1), title.get_text(strip=True))

    # 版式 B: #classes 卡片流
    for a in soup.select("#classes a[href]"):
        href = a.get("href") or ""
        match = re.search(r"/student/classes/(\d+)", href)
        if match:
            _offer(match.group(1), a.get_text(" ", strip=True))

    return classes


def _parse_due_line(line: str, reference: datetime, category: str) -> datetime | None:
    match = DUE_LINE.match(line.strip())
    if not match:
        return None
    month = MONTHS[match.group(1)[:3].upper()]
    day = int(match.group(2))
    hour = int(match.group(3))
    minute = int(match.group(4))
    meridiem = match.group(5).upper()
    if meridiem == "PM" and hour < 12:
        hour += 12
    if meridiem == "AM" and hour == 12:
        hour = 0

    year = reference.year
    dt = reference.replace(year=year, month=month, day=day, hour=hour, minute=minute,
                           second=0, microsecond=0)
    # upcoming: 月份小于当前月 → 属于明年; past/overdue: 超前 45 天以上 → 属于去年
    if category == "upcoming" and dt.date() < reference.date() and month < reference.month:
        dt = dt.replace(year=year + 1)
    elif category != "upcoming" and dt > reference + timedelta(days=45):
        dt = dt.replace(year=year - 1)
    return dt


def extract_deadlines(html: str, category: str, source_url: str,
                      reference: datetime | None = None) -> list[Deadline]:
    """从 Tasks & Deadlines 页面解析 DDL 列表(纯文本行算法)."""
    reference = reference or datetime.now()
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [ln.replace("\u00a0", " ").strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]

    due_indexes = [i for i, ln in enumerate(lines) if _parse_due_line(ln, reference, category)]
    items: list[Deadline] = []

    for pos, due_index in enumerate(due_indexes):
        title = lines[due_index - 1] if due_index >= 1 else ""
        due_at = _parse_due_line(lines[due_index], reference, category)
        if not title or due_at is None:
            continue
        # 排除页脚/分组标题等假标题
        if re.match(r"^(Upcoming|Past|Overdue|Show More|Guides|Privacy)", title, re.I):
            continue
        if re.match(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),", title, re.I):
            continue

        next_due = due_indexes[pos + 1] if pos + 1 < len(due_indexes) else len(lines)
        block = lines[due_index + 1:next_due]
        course = block[0] if block and not STATUS_LINE.match(block[0]) else None
        status = next((ln for ln in block if STATUS_LINE.match(ln)), None)

        items.append(Deadline(
            title=title[:200],
            course=course[:120] if course else None,
            due_at=due_at,
            due_text=lines[due_index],
            status=status,
            category=category,
            source_url=source_url,
        ))
    return items


def extract_overall_grade(units_html: str) -> str | None:
    """从单科 units 页侧栏提取总评(GPA-Scraper 的取法, 第 4 个 cell)."""
    soup = BeautifulSoup(units_html, "html.parser")
    sidebar = soup.find("div", class_="sidebar-items-list")
    if sidebar is None:
        return None
    cells = sidebar.find_all("div", class_="cell")
    if len(cells) < 4:
        return None
    parts = [ln for ln in cells[3].get_text("\n").splitlines() if ln.strip()]
    if len(parts) < 2:
        return None
    grade = parts[1].replace("(", "").replace(")", "").strip()
    return grade or None


def _txt(el, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()[:limit]


def extract_files(files_html: str) -> list[dict]:
    """课程 Files 页: div.row.file, 下载链接在 data-ec3-info JSON 里
    (S3 预签名 URL, 约 36 分钟有效, 因此列表不能缓存太久)."""
    soup = BeautifulSoup(files_html, "html.parser")
    out = []
    for row in soup.select("div.row.file"):
        info = {}
        raw = row.get("data-ec3-info")
        if raw:
            try:
                info = json.loads(raw)
            except Exception:  # noqa: BLE001
                info = {}
        name = (info.get("name") or "").strip() or _txt(row, 120)
        out.append({
            "name": name,
            "url": info.get("download_url") or "",
            "meta": _txt(row, 160),
        })
    return out


def extract_task_detail(task_html: str) -> dict:
    """任务详情页(.core-task-show): 头部卡(标题/类别/状态/截止/分数)
    + Dropbox 状态 + 正文(头部卡之后、Dropbox 段之前的文本)."""
    soup = BeautifulSoup(task_html, "html.parser")
    show = soup.select_one(".core-task-show")
    out: dict = {"title": "", "category": None, "kind": None, "status": None,
                 "due_badge": "", "past_due": False, "due_text": "",
                 "score": "", "dropbox": "", "description": ""}
    if show is None:
        return out
    head = show.select_one(".fusion-card-item")
    if head is not None:
        # 详情页的标题不是链接(.h4.title 直接着文本); 列表页才是 a 链接
        title_el = head.select_one(".h4.title")
        link = head.select_one(".h4.title a")
        if link is not None:
            out["title"] = link.get_text(strip=True)
        elif title_el is not None:
            out["title"] = _txt(title_el, 120)
        else:
            out["title"] = _txt(head, 80)
        badge = head.select_one(".date-badge")
        if badge is not None:
            out["due_badge"] = _txt(badge, 40)
            out["past_due"] = "past-due" in (badge.get("class") or [])
        labels = [_txt(el, 30) for el in head.select(".labels-set div.label")]
        out["category"] = next(
            (t for t in labels if t.lower() in ("formative", "summative")), None)
        out["kind"] = next(
            (t for t in labels if t.lower() not in ("formative", "summative")), None)
        st = head.select_one(".badge .badge-label")
        out["status"] = st.get_text(strip=True) if st is not None else None
        due = head.select_one(".due-date")
        out["due_text"] = _txt(due, 60) if due is not None else ""
        score = head.select_one(".assessment")
        score_txt = _txt(score, 60) if score is not None else ""
        m = re.search(r"\d+\s*/\s*\d+\s*pts", score_txt, re.I)
        out["score"] = m.group(0) if m else score_txt
    # 正文: 头部卡之后逐个子块收集, 碰到 Dropbox 段(f-title header)就停
    parts: list[str] = []
    for ch in show.children:
        if getattr(ch, "name", None) is None:
            continue
        if ch is head or ch.name == "hr":
            continue
        cls = " ".join(ch.get("class") or [])
        if "f-title" in cls or "recent-discussions" in cls:
            break
        txt = _txt(ch, 4000)
        if txt:
            parts.append(txt)
    out["description"] = "\n".join(parts)[:4000]
    drop = show.select_one("div.mb-6")
    if drop is not None:
        out["dropbox"] = _txt(drop, 120)
    return out


def extract_units_tab(units_html: str) -> dict:
    """Units 页的 Weekly Planner 列表(不少课是空的)."""
    soup = BeautifulSoup(units_html, "html.parser")
    tab = soup.select_one(".units-list-tab") or soup.select_one(".units-tabs")
    text = _txt(tab, 3000) if tab is not None else ""
    empty = (not text) or ("No records" in text) or ("No Current Units" in text)
    return {"text": "" if empty else text, "empty": empty}


def extract_core_digest(page_html: str,
                        focus: list[tuple[str, str]] | None = None) -> dict:
    """通用页面摘要(CAS worksheet / EE 项目页): 优先按 focus 的定向选择器
    [(css, 标题)] 提取内容块, 没给 focus 或全空时退回 h2/h3 通用分段。
    去掉导航/页脚/cookie 弹窗等噪音。"""
    soup = BeautifulSoup(page_html, "html.parser")
    for sel in ("script", "style", "nav", "footer", "aside",
                ".f-cookie-consent-modal", ".f-menu", ".sidebar-items-list",
                "#f-menu", ".f-topbar", ".alert-content"):
        for el in soup.select(sel):
            el.decompose()
    sections: list[dict] = []
    for sel, label in (focus or []):
        els = soup.select(sel)[:4]
        for i, el in enumerate(els):
            t = _txt(el, 1200)
            if t:
                sections.append(
                    {"h": label if len(els) == 1 else f"{label} {i + 1}", "text": t})
    if not sections:
        seen: set[str] = set()
        for h2 in soup.find_all(["h2", "h3"]):
            head = _txt(h2, 80)
            if not head or head in seen:
                continue
            parts: list[str] = []
            for sib in h2.find_next_siblings():
                if sib.name in ("h2", "h3"):
                    break
                txt = _txt(sib, 800)
                if txt and txt not in parts:
                    parts.append(txt)
            body = "\n".join(parts)[:1200]
            seen.add(head)
            if body:
                sections.append({"h": head, "text": body})
    h1 = soup.find("h1")
    return {
        "title": _txt(h1, 100) if h1 is not None else "",
        "sections": sections[:10],
    }
