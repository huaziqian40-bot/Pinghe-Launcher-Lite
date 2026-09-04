"""应用服务层: Edupage 会话 / 选课课表 / 邮件 / 日程 / ManageBac 课程.

所有服务对 bridge(AI 桥接层)暴露统一、可 JSON 化的返回值;
异常统一抛 SchoolHubError 子类, 由 bridge 转成 {ok:False, error:...}.
"""
from __future__ import annotations

import json
import re
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from ..config import Config
from ..exceptions import LoginRequiredError, SchoolHubError
from ..managebac.client import ManageBacClient
from .. import storage

KEYRING_SERVICE = "schoolhub"


# ---------------------------------------------------------------- keyring
def secret_set(key: str, secret: str) -> bool:
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, key, secret)
        return True
    except Exception:  # noqa: BLE001
        return False


def secret_get(key: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, key)
    except Exception:  # noqa: BLE001
        return None


def secret_del(key: str) -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, key)
    except Exception:  # noqa: BLE001
        pass


# ================================================================ Edupage
class EdupageService:
    """持有 edupage-api 会话; 提供全校总课表、选课个人课表、科目选项."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._ed = None
        self._lock = threading.Lock()
        self._plan_cache: dict[date, list] = {}
        self._week_cache: dict[str, dict[str, list]] = {}
        self._rooms_cache: list | None = None

    # ---- 会话 ----
    def _patch(self, ed) -> None:
        original = ed.session.request

        def request(method, url, **kwargs):  # noqa: ANN001, ANN003
            if not kwargs.get("timeout") or kwargs.get("timeout") < 40:
                kwargs["timeout"] = 40
            return original(method, url, **kwargs)

        ed.session.request = request

    def _speed_patch(self, ed) -> None:
        """edupage-api 的 get_teachers/get_classes/get_subjects/get_classrooms
        每次被调用都会重新解析整个 dbi 列表, 而解析课表时每张课卡都要查一次
        老师/班级/教室/科目 —— 实测一整周 149 张课卡会触发 9.6 万次
        get_teacher、1400 万次对象解析(60 秒以上)。这里把"全量列表"方法按
        登录会话缓存一份, 查单条的方法(get_teacher/get_class/...)随之变成
        内存查找, 整周解析降到 1 秒左右。缓存挂在 edupage 实例上, 因为
        helper 对象(如 People(self.edupage))每张课卡都会新建一个。"""
        from edupage_api.classes import Classes
        from edupage_api.classrooms import Classrooms
        from edupage_api.people import People
        from edupage_api.subjects import Subjects

        for cls, name in (
            (People, "get_teachers"),
            (Classes, "get_classes"),
            (Subjects, "get_subjects"),
            (Classrooms, "get_classrooms"),
        ):
            original = getattr(cls, name, None)
            if original is None or getattr(original, "_sh_memo", False):
                continue

            def wrapper(self, _orig=original, _name=name):  # noqa: ANN001
                store = getattr(self.edupage, "_sh_lists", None)
                if store is None:
                    store = {}
                    self.edupage._sh_lists = store
                if _name not in store:
                    store[_name] = _orig(self)
                return store[_name]

            wrapper._sh_memo = True
            setattr(cls, name, wrapper)

    def login(self, username: str, password: str, subdomain: str) -> None:
        from edupage_api import Edupage

        ed = Edupage()
        self._patch(ed)
        ed.login(username, password, subdomain)
        self._speed_patch(ed)
        self._ed = ed
        secret_set(f"edupage:{subdomain}:{username}", password)

    def _ensure(self):
        with self._lock:
            if self._ed is not None:
                return self._ed
            pw = secret_get(
                f"edupage:{self.cfg.edupage_subdomain}:{self.cfg.edupage_username}"
            )
            if not self.cfg.edupage_username or not self.cfg.edupage_subdomain or not pw:
                raise LoginRequiredError("edupage")
            from edupage_api import Edupage

            ed = Edupage()
            self._patch(ed)
            ed.login(self.cfg.edupage_username, pw, self.cfg.edupage_subdomain)
            self._speed_patch(ed)
            self._ed = ed
            return ed

    # ---- 数据 ----
    def week_monday(self, day: date) -> date:
        return day - timedelta(days=day.weekday())

    def week_plans(self, start: date, days: int = 6) -> dict[str, list]:
        """一次请求拉取整周课表(Edupage gcall 支持 date+dateto 区间).

        带磁盘缓存(6 小时过期): Edupage 服务器高峰期单次可达数分钟,
        缓存后周内重复启动/刷新零等待。
        """
        monday = self.week_monday(start)
        key = monday.isoformat()
        if key in self._week_cache:
            return self._week_cache[key]

        cache_file = Path.home() / ".schoolhub" / f"edupage_week_{key}.json"
        if cache_file.exists():
            age = _time.time() - cache_file.stat().st_mtime
            if age < 6 * 3600:
                try:
                    raw = json.loads(cache_file.read_text(encoding="utf-8"))
                    out = self._parse_week(raw)
                    self._week_cache[key] = out
                    return out
                except Exception:  # noqa: BLE001
                    pass  # 缓存损坏则重新拉取

        ed = self._ensure()
        from edupage_api.timetables import Timetables
        from edupage_api.utils import RequestUtil

        parse = getattr(Timetables(ed), "_Timetables__parse_timetable")

        csrf = ed.session.get(
            f"https://{ed.subdomain}.edupage.org/dashboard/eb.php?mode=ttday"
        )
        gpid = csrf.text.split("gpid=")[1].split("&")[0]
        gsh = csrf.text.split("gsh=")[1].split('"')[0]
        end = monday + timedelta(days=days - 1)
        resp = ed.session.post(
            f"https://{ed.subdomain}.edupage.org/gcall",
            data=RequestUtil.encode_form_data({
                "gpid": str(int(gpid) + 1),
                "gsh": gsh,
                "action": "loadData",
                "user": ed.get_user_id(),
                "changes": "{}",
                "date": monday.strftime("%Y-%m-%d"),
                "dateto": end.strftime("%Y-%m-%d"),
                "_LJSL": "4096",
            }),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        uid = str(ed.get_user_id())
        payload = resp.text.split(f'{uid}",')[1].rsplit(",[", 1)[0]
        data = json.loads(payload)

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

        out = self._parse_week(data)
        self._week_cache[key] = out
        return out

    def _parse_week(self, data: dict) -> dict[str, list]:
        """把 gcall 返回的 dates JSON 解析成 {day_iso: [Lesson...] }."""
        from edupage_api.timetables import Timetables

        parse = getattr(Timetables(self._ensure()), "_Timetables__parse_timetable")
        out: dict[str, list] = {}
        for day_key, day_data in (data.get("dates") or {}).items():
            plan = day_data.get("plan") if isinstance(day_data, dict) else None
            if not plan:
                continue
            try:
                out[day_key] = list(parse(plan) or [])
            except Exception:  # noqa: BLE001
                out[day_key] = []
        return out

    def master_plan(self, day: date) -> list:
        """全校总课表(全部候选课卡): 优先整周缓存."""
        try:
            week = self.week_plans(day, days=7)
            lessons = week.get(day.isoformat())
            if lessons is not None:
                return lessons
        except Exception:  # noqa: BLE001
            pass
        # 回退: 单日拉取
        if day in self._plan_cache:
            return self._plan_cache[day]
        ed = self._ensure()
        tt = ed.get_my_timetable(day)
        lessons = list(tt or [])
        self._plan_cache[day] = lessons
        return lessons

    def rooms(self) -> list[str]:
        if self._rooms_cache is None:
            ed = self._ensure()
            self._rooms_cache = [r.name for r in (ed.get_classrooms() or [])]
        return self._rooms_cache

    def subject_options(self, progress=None) -> list[dict]:
        """向导用: 跨整周聚合 (科目 → 老师/教室 选项).

        只看一天会漏掉当天没排课的科目(如 English B SL),
        因此用 gcall 区间接口一次拉整周, 再聚合全部科目。
        """
        monday = self.week_monday(date.today())
        if progress:
            progress({"day": f"{monday} ~ {monday + timedelta(days=5)}",
                      "attempt": 1, "total": 2})
        plans = self.week_plans(monday, days=6)
        all_lessons = [l for ls in plans.values() for l in ls]
        if not all_lessons:  # 整周无课(假期) → 试下一周
            if progress:
                progress({"day": f"{monday + timedelta(days=7)} 起", "attempt": 2, "total": 2})
            plans = self.week_plans(monday + timedelta(days=7), days=6)
            all_lessons = [l for ls in plans.values() for l in ls]

        grouped: dict[str, dict[str, set]] = {}
        for l in all_lessons:
            if l.is_cancelled or not l.subject:
                continue
            subject = l.subject.name
            teacher = l.teachers[0].name if l.teachers else ""
            room = l.classrooms[0].name if l.classrooms else ""
            grouped.setdefault(subject, {}).setdefault(teacher, set()).add(room)

        result = []
        for subject in sorted(grouped):
            options = [
                {"teacher": t or "(未指定老师)", "rooms": sorted(rs)}
                for t, rs in sorted(grouped[subject].items())
            ]
            result.append({"subject": subject, "options": options})
        return result

    def personal(self, day: date) -> list[dict]:
        """按向导选课结果过滤出的个人课表(当天).

        带磁盘缓存(2 小时): 整周缓存缺这一天时 master_plan 要回退单日拉取,
        Edupage 高峰期可达 1 分钟以上 —— 缓存让重启/当天重复访问零等待。
        文件名带选课哈希, 改选课后自动失效。
        """
        import hashlib

        selected = self.cfg.selected_lessons or []
        if not selected:
            raise SchoolHubError("还没有选课: 请在向导或设置里选择自己的课")

        sel_key = hashlib.sha1(
            json.dumps(selected, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        cache_dir = Path.home() / ".schoolhub"
        cache_file = cache_dir / f"edupage_personal_{day.isoformat()}_{sel_key}.json"
        if cache_file.exists():
            age = _time.time() - cache_file.stat().st_mtime
            if age < 2 * 3600:
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass  # 缓存损坏则重新计算

        out = []
        for l in self.master_plan(day):
            subject = l.subject.name if l.subject else ""
            teacher = l.teachers[0].name if l.teachers else ""
            for sel in selected:
                if sel.get("subject") != subject:
                    continue
                want_teacher = (sel.get("teacher") or "").replace("(未指定老师)", "")
                if want_teacher and teacher != want_teacher:
                    continue
                out.append({
                    "start": l.start_time.strftime("%H:%M") if l.start_time else "",
                    "end": l.end_time.strftime("%H:%M") if l.end_time else "",
                    "subject": subject,
                    "teacher": teacher,
                    "room": l.classrooms[0].name if l.classrooms else "",
                    "groups": ",".join(l.groups) if l.groups else "",
                    "cancelled": bool(l.is_cancelled),
                    "curriculum": getattr(l, "curriculum", None) or "",
                })
                break
        out.sort(key=lambda x: (x["start"], x["subject"]))
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            for old in cache_dir.glob(f"edupage_personal_{day.isoformat()}_*.json"):
                if old != cache_file:
                    old.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return out


# ================================================================ 空闲教室
class FreeRoomsService:
    def __init__(self, ep: EdupageService):
        self.ep = ep

    def occupancy(self, day: date, at: dtime) -> dict:
        rooms = self.ep.rooms()
        occupied: dict[str, str] = {}
        for l in self.ep.master_plan(day):
            if l.is_cancelled or not l.start_time or not l.end_time:
                continue
            if l.start_time <= at < l.end_time:
                subject = l.subject.name if l.subject else "?"
                teacher = l.teachers[0].name if l.teachers else "-"
                span = f"{l.start_time.strftime('%H:%M')}-{l.end_time.strftime('%H:%M')}"
                for room in l.classrooms or []:
                    occupied.setdefault(room.name, f"{span} {subject} ({teacher})")
        free = sorted(set(rooms) - set(occupied))
        return {
            "total": len(rooms),
            "occupied": [
                {"room": k, "info": v} for k, v in sorted(occupied.items())
            ],
            "free": free,
        }


# ================================================================ 邮件
class MailService:
    """网易企业邮 IMAP 客户端(标准库 imaplib 直写命令).

    不用 imap_tools: 其 UID SEARCH 带 CHARSET 参数, 网易 Coremail 返回
    BAD "Could not parse command"。裸 imaplib 最兼容。
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._unread_cache: tuple[float, int] | None = None

    def _password(self) -> str:
        """获取 IMAP 登录密码。

        网易企业邮的 IMAP/SMTP 服务需要使用客户端授权码登录，
        而不是网页登录密码。优先获取授权码，若无则回退到网页登录密码。
        """
        authcode = secret_get(f"mail_authcode:{self.cfg.mail_email}")
        if authcode:
            return authcode
        pw = secret_get(f"mail:{self.cfg.mail_email}")
        if not pw or not self.cfg.mail_email:
            raise LoginRequiredError("mail")
        return pw

    def set_authcode(self, email: str, authcode: str) -> None:
        """保存客户端授权码并确保后续连接使用该授权码。"""
        if authcode.strip():
            secret_set(f"mail_authcode:{email.strip()}", authcode.strip())

    def _imap_error(self, exc: Exception) -> SchoolHubError:
        extra = ""
        if "ERR.ILLEGAL.EMAIL" in str(exc):
            extra = ("。ERR.ILLEGAL.EMAIL 通常表示该账号未开通 IMAP 客户端服务: "
                     "请登录 mail.shphschool.com → 设置 → 客户端设置 → 开启 IMAP 并生成客户端授权密码; "
                     "若已开启仍报错, 请联系学校管理员为你的账号开通客户端协议")
        elif "INVALID" in str(exc).upper() or "AUTH" in str(exc).upper():
            extra = ("。认证失败: 网易企业邮 IMAP/SMTP 服务需要使用「客户端授权码」登录, "
                     "而不是网页登录密码。请登录 mail.shphschool.com → 设置 → 客户端设置 → "
                     "开启 IMAP/SMTP 服务并生成客户端授权码, 然后在设置页面填写客户端授权码")
        return SchoolHubError(
            f"IMAP 连接/登录失败: {exc}{extra}。"
            "通用检查: ① 服务器 imap.qiye.163.com(网页入口 mail.shphschool.com);"
            "② 网页版开启 IMAP/SMTP 服务;"
            "③ 使用「客户端授权码」而不是网页登录密码"
        )

    def _conn(self):
        import imaplib

        try:
            M = imaplib.IMAP4_SSL(self.cfg.mail_imap_host, 993)
            M.login(self.cfg.mail_email, self._password())
            M.select("INBOX")
            return M
        except LoginRequiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._imap_error(exc) from exc

    def configure(self, email: str, password: str,
                  imap_host: str = "", smtp_host: str = "") -> None:
        if imap_host:
            self.cfg.mail_imap_host = imap_host
        if smtp_host:
            self.cfg.mail_smtp_host = smtp_host
        self.cfg.mail_email = email.strip()
        secret_set(f"mail:{email.strip()}", password.strip())

    @staticmethod
    def _decode(value) -> str:
        if not value:
            return ""
        from email.header import decode_header, make_header

        try:
            return str(make_header(decode_header(str(value))))
        except Exception:  # noqa: BLE001
            try:
                return str(value)
            except Exception:  # noqa: BLE001
                return ""

    def list_mail(self, unseen_only: bool = False, limit: int = 30) -> list[dict]:
        import email as _email
        import imaplib

        M = self._conn()
        try:
            typ, data = M.uid("SEARCH", "UNSEEN" if unseen_only else "ALL")
            if typ != "OK":
                raise SchoolHubError(f"SEARCH 失败: {data}")
            uids = (data[0] or b"").split()
            items = []
            for uid_bytes in reversed(uids[-limit:]):
                uid = uid_bytes.decode()
                typ, md = M.uid(
                    "FETCH", uid,
                    "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])",
                )
                flags_str, raw = "", b""
                for part in md:
                    if isinstance(part, tuple):
                        m = re.search(rb"FLAGS \(([^)]*)\)", part[0] or b"")
                        flags_str = m.group(1).decode(errors="replace") if m else ""
                        raw = part[1] or b""
                        break
                msg = _email.message_from_bytes(raw)
                subject = self._decode(msg["Subject"]) or "(无主题)"
                from_ = self._decode(msg["From"])
                try:
                    from email.utils import parsedate_to_datetime

                    date_str = parsedate_to_datetime(msg["Date"]).strftime("%m-%d %H:%M")
                except Exception:  # noqa: BLE001
                    date_str = (msg["Date"] or "")[:16]
                items.append({
                    "uid": uid,
                    "subject": subject[:120],
                    "from": from_[:120],
                    "date": date_str,
                    "seen": "\\Seen" in flags_str,
                })
            return items
        finally:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass

    def read(self, uid: str) -> dict:
        import email as _email
        import html as _html
        import imaplib

        M = self._conn()
        try:
            typ, md = M.uid("FETCH", uid, "(BODY.PEEK[])")
            raw = b""
            for part in md:
                if isinstance(part, tuple):
                    raw = part[1] or b""
                    break
            if not raw:
                raise SchoolHubError(f"邮件 {uid} 不存在")
            msg = _email.message_from_bytes(raw)
            body = ""
            body_is_html = False
            if msg.is_multipart():
                # 优先 text/plain, 其次 text/html
                html_part = None
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if ctype == "text/plain":
                        payload = part.get_payload(decode=True) or b""
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                        break
                    if ctype == "text/html" and html_part is None:
                        html_part = part
                if not body and html_part:
                    payload = html_part.get_payload(decode=True) or b""
                    charset = html_part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    body_is_html = True
            else:
                payload = msg.get_payload(decode=True) or b""
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
                body_is_html = (msg.get_content_type() == "text/html")

            # 标记已读
            try:
                M.uid("STORE", uid, "+FLAGS", "(\\Seen)")
            except Exception:  # noqa: BLE001
                pass
            return {
                "uid": uid,
                "subject": self._decode(msg["Subject"]) or "(无主题)",
                "from": self._decode(msg["From"]),
                "date": (msg["Date"] or "")[:24],
                "to": self._decode(msg["To"]),
                "body": body[:20000],
                "is_html": body_is_html,
            }
        finally:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass

    def unread_count(self) -> int:
        now = _time.monotonic()
        if self._unread_cache and now - self._unread_cache[0] < 300:
            return self._unread_cache[1]
        M = self._conn()
        try:
            typ, data = M.uid("SEARCH", "UNSEEN")
            if typ != "OK":
                raise SchoolHubError(f"SEARCH 失败: {data}")
            count = len((data[0] or b"").split())
            self._unread_cache = (now, count)
            return count
        finally:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass

    def _smtp_password(self) -> str:
        """获取 SMTP 登录密码（与 IMAP 相同逻辑）。"""
        return self._password()

    def send(self, to: str, subject: str, body: str) -> None:
        import smtplib
        from email.header import Header
        from email.mime.text import MIMEText

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = self.cfg.mail_email
        msg["To"] = to
        with smtplib.SMTP_SSL(self.cfg.mail_smtp_host, 994, timeout=30) as smtp:
            smtp.login(self.cfg.mail_email, self._smtp_password())
            smtp.sendmail(self.cfg.mail_email, [to], msg.as_string())


# ================================================================ 日程
class ScheduleService:
    def __init__(self, conn_factory):
        self._conn_factory = conn_factory

    def add(self, day: str, time_: str, title: str, note: str = "") -> int:
        _check_day(day)
        if time_ and not re.match(r"^\d{1,2}:\d{2}$", time_):
            raise SchoolHubError("时间格式应为 HH:MM")
        conn = self._conn_factory()
        return storage.events_add(conn, day, time_, title.strip(), note or "")

    def list_range(self, day_from: str, day_to: str) -> list[dict]:
        conn = self._conn_factory()
        return storage.events_list(conn, day_from, day_to)

    def month(self, month: str) -> list[dict]:
        if not re.match(r"^\d{4}-\d{2}$", month):
            raise SchoolHubError("月份格式应为 YYYY-MM")
        return self.list_range(f"{month}-01", f"{month}-31")

    def update(self, event_id: int, day: str, time_: str, title: str, note: str) -> None:
        _check_day(day)
        conn = self._conn_factory()
        storage.events_update(conn, int(event_id), day, time_, title.strip(), note or "")

    def delete(self, event_id: int) -> None:
        conn = self._conn_factory()
        storage.events_delete(conn, int(event_id))


def _check_day(day: str) -> None:
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise SchoolHubError("日期格式应为 YYYY-MM-DD")


# ================================================================ ManageBac
class CoursesService:
    def __init__(self, cfg: Config, conn_factory):
        self.cfg = cfg
        self._conn_factory = conn_factory
        self._client: ManageBacClient | None = None
        self._grades_cache: tuple[float, dict] | None = None

    def _client_ready(self) -> ManageBacClient:
        if self._client is None:
            client = ManageBacClient(self.cfg.managebac_base_url)
            host = self.cfg.managebac_base_url.split("//")[-1]
            session_file = Path.home() / ".schoolhub" / f"session_{host}.json"
            if session_file.exists():
                import json

                for k, v in json.loads(session_file.read_text(encoding="utf-8"))["cookies"].items():
                    client.session.cookies.set(k, v)
            self._client = client
        return self._client

    def ensure_login(self, password: str | None = None) -> None:
        client = self._client_ready()
        try:
            if client.is_logged_in():
                return
        except Exception:  # noqa: BLE001
            pass
        pw = password or secret_get(f"managebac:{self.cfg.managebac_base_url}")
        if not pw:
            raise LoginRequiredError("managebac")
        client.login(self.cfg.managebac_email, pw)
        secret_set(f"managebac:{self.cfg.managebac_base_url}", pw)

    def login(self, url: str, email: str, password: str) -> None:
        client = ManageBacClient(url)
        client.login(email, password)
        host = url.split("//")[-1]
        session_file = Path.home() / ".schoolhub" / f"session_{host}.json"
        import json

        session_file.parent.mkdir(parents=True, exist_ok=True)
        session_file.write_text(
            json.dumps({"cookies": client.session.cookies.get_dict()}, indent=2),
            encoding="utf-8",
        )
        secret_set(f"managebac:{url}", password)
        self.cfg.managebac_base_url = url
        self.cfg.managebac_email = email
        self._client = client

    def classes(self) -> dict[str, str]:
        return self._client_ready().get_classes()

    def deadlines(self, days: int = 21) -> list[dict]:
        items = self._client_ready().get_deadlines(days_ahead=days)
        return [
            {
                "title": it.title,
                "course": it.course or "",
                "due_at": it.due_at.isoformat(timespec="minutes") if it.due_at else "",
                "status": it.status or "",
                "category": it.category,
            }
            for it in items
        ]

    def all_tasks(self, force: bool = False) -> list[dict]:
        host = self.cfg.managebac_base_url.split("//")[-1]
        conn = self._conn_factory()
        if not force:
            updated = storage.tasks_cache_age(conn, host)
            if updated and datetime.now() - updated < timedelta(hours=6):
                return storage.load_tasks_cache(conn, host)
        tasks = self._client_ready().get_all_tasks()
        storage.save_tasks_cache(conn, host, tasks)
        return storage.load_tasks_cache(conn, host)

    def grades(self, force: bool = False) -> dict[str, str]:
        now = _time.monotonic()
        if not force and self._grades_cache and now - self._grades_cache[0] < 3600:
            return self._grades_cache[1]
        grades = self._client_ready().get_overall_grades()
        self._grades_cache = (now, grades)
        return grades

    def submit_task(self, class_id: str, task_id: str, file_path: str) -> str:
        """交作业(实验性): dropbox multipart 上传."""
        client = self._client_ready()
        page = client._get(
            f"/student/classes/{class_id}/core_tasks/{task_id}/dropbox"
        )
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(page.text, "html.parser")
        token_tag = soup.find("input", attrs={"name": "authenticity_token"})
        token = token_tag["value"] if token_tag else ""
        form = soup.find("form", action=re.compile("dropbox"))
        action = form.get("action") if form else (
            f"/student/classes/{class_id}/core_tasks/{task_id}/dropbox"
        )
        with open(file_path, "rb") as fh:
            resp = client.session.post(
                client._url(action),
                data={"authenticity_token": token, "commit": "Upload"},
                files={"dropbox_assets_attributes_0_file": (Path(file_path).name, fh)},
                timeout=60,
            )
        if resp.status_code >= 400:
            raise SchoolHubError(f"提交失败: HTTP {resp.status_code}")
        return "已提交(请到 ManageBac 网页确认)"


# ================================================================ 汇总
class Services:
    """所有服务的一次性组装(bridge 持有)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._db_conn = None

        self.edupage = EdupageService(cfg)
        self.free_rooms = FreeRoomsService(self.edupage)
        self.mail = MailService(cfg)
        self.schedule = ScheduleService(self._conn)
        self.courses = CoursesService(cfg, self._conn)

    def _conn(self):
        """每次调用返回全新连接 —— pywebview 的 js_api 调用来自不同线程,
        共享连接会触发 SQLite 的跨线程检查错误."""
        return storage.connect()
