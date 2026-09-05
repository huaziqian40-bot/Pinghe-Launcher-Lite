"""ManageBac 学生端纯 HTTP 客户端(无浏览器、无 Selenium).

登录原理(已在 shph.managebac.cn/login 实测验证):
1. GET /login          → Rails 表单 #session_form (action=/sessions, POST)
                         隐藏域 authenticity_token (CSRF) + _managebac_session cookie
2. POST /sessions      → session[login] / session[password] / authenticity_token / remember_me
3. 之后所有数据页只需带 _managebac_session cookie,普通 GET 即可
"""
from __future__ import annotations

import re
from datetime import datetime
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from ..exceptions import LoginError, LoginRequiredError
from .parse import (
    Deadline,
    NO_CLASSES_MARKER,
    extract_classes,
    extract_core_digest,
    extract_deadlines,
    extract_files,
    extract_overall_grade,
    extract_task_cards,
    extract_task_detail,
    extract_units_tab,
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

#: 登录失败时页面上可能出现的关键词(抄 managebac-mcp 的容错思路)
_FAILURE_PATTERNS = re.compile(r"invalid|incorrect|locked|required", re.IGNORECASE)


@dataclass
class LoginProbe:
    """对登录页的无害探测结果(不需要账号)."""

    url: str
    status: int
    form_action: str | None
    has_login_field: bool
    has_password_field: bool
    has_csrf: bool
    cookies: list[str]


class ManageBacClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ------------------------------------------------------------ 基础
    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _get(self, path: str) -> requests.Response:
        resp = self.session.get(self._url(path), timeout=self.timeout)
        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------ 登录
    def probe_login(self) -> LoginProbe:
        """探测登录页:验证表单/CSRF 是否为标准 Rails 结构,无需任何凭据."""
        resp = self.session.get(self._url("/login"), timeout=self.timeout)
        soup = _soup(resp.text)
        form = soup.find("form", id="session_form")
        return LoginProbe(
            url=str(resp.url),
            status=resp.status_code,
            form_action=form.get("action") if form else None,
            has_login_field=bool(soup.find(id="session_login")),
            has_password_field=bool(soup.find(id="session_password")),
            has_csrf=bool(soup.find(attrs={"name": "authenticity_token"})),
            cookies=[c.name for c in self.session.cookies],
        )

    def _csrf_token(self, soup) -> str | None:
        tag = soup.find("input", attrs={"name": "authenticity_token"})
        if tag and tag.get("value"):
            return tag["value"]
        meta = soup.find("meta", attrs={"name": "csrf-token"})
        return meta["content"] if meta else None

    def login(self, email: str, password: str, remember: bool = True) -> None:
        """纯 HTTP 账密登录。失败抛 LoginError(绝不重试,防锁号)."""
        resp = self.session.get(self._url("/login"), timeout=self.timeout)
        resp.raise_for_status()
        soup = _soup(resp.text)
        form = soup.find("form", id="session_form")
        if form is None:
            raise LoginError("登录页结构异常: 未找到 #session_form(学校可能启用了 SSO?)")

        # 字段名从表单里读,不硬编码(新版 UI 是 login/password,老版是 session[login]/...)
        login_field = soup.find(id="session_login") or form.find(attrs={"type": "email"})
        password_field = soup.find(id="session_password") or form.find(attrs={"type": "password"})
        login_name = (login_field.get("name") if login_field else None) or "login"
        password_name = (password_field.get("name") if password_field else None) or "password"

        payload: dict[str, str] = {
            "authenticity_token": self._csrf_token(soup) or "",
            login_name: email,
            password_name: password,
            "remember_me": "1" if remember else "0",
        }
        commit = form.find(attrs={"name": "commit"})
        if commit is not None and commit.get("value"):
            payload["commit"] = commit["value"]

        action = form.get("action") or "/sessions"
        resp = self.session.post(
            self._url(action), data=payload, timeout=self.timeout, allow_redirects=True
        )

        final = _soup(resp.text)
        landed_on_login = "/login" in str(resp.url) or final.find(id="session_password")
        if landed_on_login:
            line = next(
                (ln for ln in final.get_text("\n").splitlines() if _FAILURE_PATTERNS.search(ln)),
                None,
            )
            raise LoginError(f"登录被拒绝(密码错误/账号锁定/需要 SSO)。页面提示: {line}")

        if not self.is_logged_in():
            raise LoginError("登录请求已发出,但会话未生效(请检查账号类型是否为学生账号)")

    def is_logged_in(self) -> bool:
        """检查会话:访问 /student,若又出现密码框则已失效."""
        resp = self._get("/student")
        return _soup(resp.text).find(id="session_password") is None

    # ------------------------------------------------------------ 数据
    def get_classes(self) -> dict[str, str]:
        """全部课程 {class_id: 课程名},自动翻页."""
        classes: dict[str, str] = {}
        for page in range(1, 21):
            resp = self._get(f"/student/classes/my?page={page}")
            if NO_CLASSES_MARKER in resp.text and page > 1:
                break
            found = extract_classes(resp.text)
            if not found:
                break
            classes.update(found)
        return classes

    def get_deadlines(
        self, views: tuple[str, ...] = ("upcoming", "overdue"), days_ahead: int | None = None
    ) -> list[Deadline]:
        """Tasks & Deadlines 三个栏目合并、去重、按时间排序."""
        items: list[Deadline] = []
        seen: set[tuple] = set()
        for view in views:
            resp = self._get(f"/student/tasks_and_deadlines?view={view}")
            for item in extract_deadlines(resp.text, view, str(resp.url)):
                key = (item.title.lower(), item.course or "", item.due_text)
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)

        from datetime import datetime, timedelta

        if days_ahead is not None:
            cutoff = datetime.now() + timedelta(days=days_ahead)
            items = [
                it for it in items
                if it.due_at is None or it.due_at <= cutoff
            ]
        items.sort(key=lambda it: it.due_at or datetime.max)
        return items

    def get_overall_grades(self) -> dict[str, str | None]:
        """各科总评 {课程名: 总评文本}(遍历课程, 注意限速)."""
        grades: dict[str, str | None] = {}
        for class_id, name in self.get_classes().items():
            resp = self._get(f"/student/classes/{class_id}/units")
            grades[name] = extract_overall_grade(resp.text)
        return grades

    def get_class_tasks(self, class_id: str, class_name: str = "") -> list:
        """单门课的全部作业卡(含已截止的)."""
        resp = self._get(f"/student/classes/{class_id}/core_tasks")
        return extract_task_cards(resp.text, class_id=class_id, class_name=class_name)

    def get_all_tasks(self, sleep_seconds: float = 0.3) -> list:
        """全部课程的作业卡(逐课抓取, 带限速)."""
        import time as _time

        tasks = []
        for class_id, name in self.get_classes().items():
            try:
                tasks.extend(self.get_class_tasks(class_id, class_name=name))
            except Exception:  # noqa: BLE001
                continue
            _time.sleep(sleep_seconds)
        tasks.sort(key=lambda t: t.due_at or datetime(1970, 1, 1))
        return tasks

    # ------------------------------------------------------------ 课程详情页
    def get_class_files(self, class_id: str) -> list[dict]:
        """课程 Files 页文件列表(download_url 为 S3 预签名, 短时效)."""
        resp = self._get(f"/student/classes/{class_id}/files")
        return extract_files(resp.text)

    def get_class_events(self, class_id: str) -> list:
        """课程 Calendar 的 JSON 事件源(/student/classes/<id>/events.json)."""
        resp = self.session.get(
            self._url(f"/student/classes/{class_id}/events.json"),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            data = data.get("events") or data.get("items") or []
        return data if isinstance(data, list) else []

    def get_class_units(self, class_id: str) -> dict:
        """课程 Units 页(weekly planner, 不少课为空)."""
        resp = self._get(f"/student/classes/{class_id}/units")
        return extract_units_tab(resp.text)

    def get_task_detail(self, class_id: str, task_id: str) -> dict:
        """单个任务的详情页(标题/类别/状态/截止/分数/正文/Dropbox)."""
        resp = self._get(f"/student/classes/{class_id}/core_tasks/{task_id}")
        out = extract_task_detail(resp.text)
        out["task_id"] = str(task_id)
        out["class_id"] = str(class_id)
        return out

    def get_cas_overview(self) -> dict:
        """CAS worksheet 概览(/student/ib/activity/cas)."""
        resp = self._get("/student/ib/activity/cas")
        out = extract_core_digest(resp.text, focus=[
            (".aims-and-goals", "Aims & Goals 目标"),
            (".statuses-legend", "进度状态图例"),
            (".card-body", "官方指南"),
        ])
        out["url"] = "/student/ib/activity/cas"
        return out

    def get_ee_overview(self) -> dict:
        """EE 项目页(本校挂在 /student/ib/pbl/778, 页面标题 Extended Essay)."""
        resp = self._get("/student/ib/pbl/778")
        out = extract_core_digest(resp.text, focus=[
            (".pbl-worksheet", "EE 工作表"),
            (".js-core-project-documents", "EE 文档"),
        ])
        out["url"] = "/student/ib/pbl/778"
        return out


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")
