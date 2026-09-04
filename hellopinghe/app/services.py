"""应用服务层: Edupage 会话 / 选课课表 / 邮件 / 日程 / ManageBac 课程.

所有服务对 bridge(AI 桥接层)暴露统一、可 JSON 化的返回值;
异常统一抛 PingheError 子类, 由 bridge 转成 {ok:False, error:...}.
"""
from __future__ import annotations

import json
import re
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from ..config import CONFIG_DIR, Config
from ..exceptions import LoginRequiredError, PingheError
from ..managebac.client import ManageBacClient
from .. import storage

KEYRING_SERVICE = "hellopinghe"


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
        """拉取整周课表并解析成 {day_iso: [Lesson...]}.

        Edupage gcall 的 loadData 会忽略 dateto, 只返回以 date 为中心的
        3 天窗口(前一天 + 当天 + 后一天, 实测确认), 因此按锚点分 3 次
        拉取(周一/周四/周日)再合并 dates, 才能覆盖完整一周 —— 之前单次
        请求只有周日~周二, 周三~周五落进 get_my_timetable 回退(残缺,
        周五下午整段丢失)。

        带磁盘缓存(6 小时过期, v2 文件名与旧截断缓存隔离)。
        """
        monday = self.week_monday(start)
        key = monday.isoformat()
        if key in self._week_cache:
            return self._week_cache[key]

        cache_file = Path.home() / ".hellopinghe" / f"edupage_week_v2_{key}.json"
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

        def fetch_window(anchor: date) -> dict:
            resp = ed.session.post(
                f"https://{ed.subdomain}.edupage.org/gcall",
                data=RequestUtil.encode_form_data({
                    "gpid": str(int(gpid) + 1),
                    "gsh": gsh,
                    "action": "loadData",
                    "user": ed.get_user_id(),
                    "changes": "{}",
                    "date": anchor.strftime("%Y-%m-%d"),
                    "dateto": (anchor + timedelta(days=2)).strftime("%Y-%m-%d"),
                    "_LJSL": "4096",
                }),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            uid = str(ed.get_user_id())
            payload = resp.text.split(f'{uid}",')[1].rsplit(",[", 1)[0]
            return json.loads(payload)

        merged: dict = {}
        for anchor_off in (0, 3, 6):   # 窗口: 周日~周二 / 周二~周四 / 周五~周日
            try:
                win = fetch_window(monday + timedelta(days=anchor_off))
                merged.update(win.get("dates") or {})
            except Exception:  # noqa: BLE001
                continue
        if not merged:
            raise PingheError("课表拉取失败: gcall 三个窗口均无返回")
        data = {"dates": merged}

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            # 清掉旧版(截断的)整周缓存
            for old in cache_file.parent.glob("edupage_week_2*.json"):
                old.unlink(missing_ok=True)
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
            raise PingheError("还没有选课: 请在向导或设置里选择自己的课")

        sel_key = hashlib.sha1(
            json.dumps(selected, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        cache_dir = Path.home() / ".hellopinghe"
        cache_file = cache_dir / f"edupage_personal_v2_{day.isoformat()}_{sel_key}.json"
        if cache_file.exists():
            age = _time.time() - cache_file.stat().st_mtime
            if age < 2 * 3600:
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass  # 缓存损坏则重新计算

        # 严格匹配: 课名与老师都必须与选课完全一致(仅去首尾空白),
        # 不做任何模糊/归一化 —— 选课里没有的课一律不进个人课表。
        # 选课时老师为 "(未指定老师)" 的, 该课任何老师的卡都算匹配。
        out = []
        for l in self.master_plan(day):
            subject = (l.subject.name if l.subject else "").strip()
            teacher = (l.teachers[0].name if l.teachers else "").strip()
            for sel in selected:
                if (sel.get("subject") or "").strip() != subject:
                    continue
                want_teacher = (sel.get("teacher") or "").replace(
                    "(未指定老师)", "").strip()
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
            for old in cache_dir.glob("edupage_personal_2*.json"):
                old.unlink(missing_ok=True)   # 旧版缓存(截断数据)直接清理
            for old in cache_dir.glob(f"edupage_personal_v2_{day.isoformat()}_*.json"):
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

    def _imap_error(self, exc: Exception) -> PingheError:
        extra = ""
        if "ERR.ILLEGAL.EMAIL" in str(exc):
            extra = ("。ERR.ILLEGAL.EMAIL 通常表示该账号未开通 IMAP 客户端服务: "
                     "请登录 mail.shphschool.com → 设置 → 客户端设置 → 开启 IMAP 并生成客户端授权密码; "
                     "若已开启仍报错, 请联系学校管理员为你的账号开通客户端协议")
        elif "INVALID" in str(exc).upper() or "AUTH" in str(exc).upper():
            extra = ("。认证失败: 网易企业邮 IMAP/SMTP 服务需要使用「客户端授权码」登录, "
                     "而不是网页登录密码。请登录 mail.shphschool.com → 设置 → 客户端设置 → "
                     "开启 IMAP/SMTP 服务并生成客户端授权码, 然后在设置页面填写客户端授权码")
        return PingheError(
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
                raise PingheError(f"SEARCH 失败: {data}")
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
                raise PingheError(f"邮件 {uid} 不存在")
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
                raise PingheError(f"SEARCH 失败: {data}")
            count = len((data[0] or b"").split())
            self._unread_cache = (now, count)
            return count
        finally:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass

    # ---- 通讯录 (lbdb 式: 从收件箱+已发送的邮件头收割联系人, 按频率排序) ----
    _CONTACTS_SKIP = re.compile(
        r"noreply|no-reply|donotreply|do-not-reply|mailer-daemon|postmaster"
        r"|bounce|notification|notice|system", re.I)

    @staticmethod
    def _mutf7_decode(name: str) -> str:
        """IMAP modified UTF-7 → UTF-8(Coremail 文件夹名如 &XfJT0ZAB- = 已发送)。"""
        import base64

        out, i = [], 0
        while i < len(name):
            ch = name[i]
            if ch != "&":
                out.append(ch)
                i += 1
                continue
            j = name.find("-", i + 1)
            if j < 0:
                out.append(name[i:])
                break
            b64 = name[i + 1:j].replace(",", "/")
            if not b64:
                out.append("&")
            else:
                try:
                    out.append(base64.b64decode(
                        b64 + "=" * (-len(b64) % 4)).decode("utf-16-be"))
                except Exception:  # noqa: BLE001
                    out.append(name[i:j + 1])
            i = j + 1
        return "".join(out)

    def _sent_folders(self, M) -> list[str]:
        """探测已发送文件夹(Coremail 常见命名, 含 modified UTF-7 中文名)。"""
        try:
            typ, data = M.list()
            if typ != "OK":
                return []
        except Exception:  # noqa: BLE001
            return []

        out = []
        for line in data:
            if not line:
                continue
            text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
            m = re.search(r'\s"([^"]+)"\s*$', text)
            name = m.group(1) if m else text.rsplit(" ", 1)[-1].strip('"')
            decoded = self._mutf7_decode(name)
            if "sent" in (name + " " + decoded).lower() or "已发送" in decoded:
                out.append(f'"{name}"' if " " in name else name)
        return out

    @staticmethod
    def _envelope_addresses(value: str) -> list[tuple[str, str]]:
        """解析 Coremail 信封式地址头。

        网易 Coremail 对 BODY[HEADER.FIELDS (FROM TO CC)] 返回的不是
        标准 RFC5322 头, 而是 IMAP ENVELOPE 地址列表序列化:
          (("显示名" NIL "local" "domain")) / ((NIL NIL "a" "x") (NIL NIL "b" "y"))
        email.utils.getaddresses 解析不了, 这里按括号+引号做小分词器。
        """
        stack: list[list] = [[]]
        buf, in_quote, esc = "", False, False

        def push_atom() -> None:
            nonlocal buf
            t = buf.strip()
            if t:
                stack[-1].append(None if t.upper() == "NIL" else t.strip('"'))
            buf = ""

        for ch in value:
            if esc:
                buf += ch
                esc = False
            elif in_quote:
                if ch == "\\":
                    esc = True
                elif ch == '"':
                    in_quote = False
                else:
                    buf += ch
            elif ch == '"':
                in_quote = True
            elif ch == "(":
                push_atom()
                stack.append([])
            elif ch == ")":
                push_atom()
                node = stack.pop()
                stack[-1].append(node)
            elif ch in " \t":
                push_atom()
            else:
                buf += ch
        push_atom()

        addrs: list[tuple[str, str]] = []

        def walk(node) -> None:
            if not isinstance(node, list):
                return
            # 地址元组 = [name, adl, mailbox, host], mailbox/host 必为字符串
            if (len(node) >= 4
                    and isinstance(node[2], str) and isinstance(node[3], str)):
                name = node[0] if isinstance(node[0], str) else ""
                addrs.append((name, f"{node[2]}@{node[3]}"))
                return
            for child in node:
                walk(child)

        walk(stack[0])
        return addrs

    def contacts(self, force: bool = False, limit: int = 300) -> list[dict]:
        """从 INBOX + 已发送文件夹收割联系人。

        网易企业邮个人账号没有 CardDAV/通讯录 API(那是管理员端能力),
        客户端方案与 mutt/lbdb、Gmail 相同: 解析 From/To/Cc 邮件头,
        (地址→姓名, 出现次数) 聚合, 按频率排序 → 自动补全与 AI 查询。
        磁盘缓存 24h。
        """
        import email as _email
        from email.utils import getaddresses

        cache = CONFIG_DIR / "mail_contacts.json"
        if not force and cache.exists():
            try:
                raw = json.loads(cache.read_text(encoding="utf-8"))
                if _time.time() - raw.get("ts", 0) < 86400:
                    return raw.get("contacts", [])
            except Exception:  # noqa: BLE001
                pass

        me = (self.cfg.mail_email or "").strip().lower()
        agg: dict[str, dict] = {}

        def harvest(header_bytes: bytes) -> None:
            msg = _email.message_from_bytes(header_bytes)
            for key, value in msg.items():
                if key.lower() not in ("from", "to", "cc"):
                    continue
                decoded = self._decode(value)
                pairs = [(n, a) for n, a in getaddresses([decoded])
                         if a and "@" in a]
                if not pairs and decoded.lstrip().startswith("("):
                    pairs = self._envelope_addresses(decoded)
                for _name, addr in pairs:
                    addr = (addr or "").strip().strip("<>").lower()
                    if "@" not in addr or addr == me or not addr.partition("@")[0]:
                        continue
                    if self._CONTACTS_SKIP.search(addr):
                        continue
                    entry = agg.setdefault(addr, {"names": {}, "count": 0})
                    entry["count"] += 1
                    if _name:
                        _name = _name.strip().strip("\"'").strip()
                        if _name:
                            entry["names"][_name] = entry["names"].get(_name, 0) + 1

        M = self._conn()
        try:
            folders = ["INBOX"] + self._sent_folders(M)
            for folder in folders:
                try:
                    typ, data = M.select(folder, readonly=True)
                    if typ != "OK" or not data or not data[0]:
                        continue
                    typ, data = M.uid("SEARCH", "ALL")
                    if typ != "OK":
                        continue
                    uids = (data[0] or b"").split()[-400:]
                    # 批量 FETCH(每批 100), 避免逐封往返拖慢
                    for i in range(0, len(uids), 100):
                        batch = b",".join(uids[i:i + 100])
                        typ, md = M.uid(
                            "FETCH", batch,
                            "(BODY.PEEK[HEADER.FIELDS (FROM TO CC)])")
                        if typ != "OK":
                            continue
                        for part in md:
                            if isinstance(part, tuple) and part[1]:
                                harvest(part[1])
                except Exception:  # noqa: BLE001
                    continue
        finally:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass

        contacts = []
        for addr, entry in sorted(
                agg.items(), key=lambda kv: -kv[1]["count"])[:limit]:
            best_name = (max(entry["names"], key=entry["names"].get)
                         if entry["names"] else "")
            contacts.append({"name": best_name, "email": addr,
                             "count": entry["count"]})
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(
                {"ts": _time.time(), "contacts": contacts},
                ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return contacts

    def contacts_search(self, query: str, limit: int = 8) -> list[dict]:
        """按姓名/邮箱模糊匹配联系人(供 AI 与前端自动补全)。"""
        q = (query or "").strip().lower()
        allc = self.contacts_merged()
        if not q:
            return allc[:limit]
        out = []
        for c in allc:
            hay = f"{c.get('name', '')} {c.get('email', '')}".lower()
            if q in hay:
                out.append(c)
                if len(out) >= limit:
                    break
        return out

    # ---- 自建联系人 / 隐藏标记(与收割结果合并, 存 contacts_custom.json) ----
    @staticmethod
    def _custom_file() -> Path:
        return CONFIG_DIR / "contacts_custom.json"

    def _custom(self) -> dict:
        try:
            raw = json.loads(self._custom_file().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {"custom": list(raw.get("custom") or []),
                        "hidden": [str(x).lower() for x in (raw.get("hidden") or [])]}
        except Exception:  # noqa: BLE001
            pass
        return {"custom": [], "hidden": []}

    def _save_custom(self, data: dict) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._custom_file().write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def contacts_merged(self) -> list[dict]:
        """收割通讯录 + 用户自建/隐藏 合并; 自建条目置顶并带 custom 标记。"""
        data = self._custom()
        hidden = set(data["hidden"])
        custom_emails = {str(c.get("email", "")).lower() for c in data["custom"]}
        out = [dict(c, custom=True) for c in data["custom"]]
        out += [c for c in self.contacts()
                if c["email"].lower() not in hidden
                and c["email"].lower() not in custom_emails]
        return out

    @staticmethod
    def _valid_email(email: str) -> bool:
        return ("@" in email and "." in email.rsplit("@", 1)[-1]
                and " " not in email)

    def contact_add(self, name: str, email: str) -> list[dict]:
        email = (email or "").strip().lower()
        name = (name or "").strip()
        if not self._valid_email(email):
            raise PingheError("邮箱地址不合法")
        if not name:
            raise PingheError("姓名不能为空")
        data = self._custom()
        data["custom"] = [c for c in data["custom"]
                          if str(c.get("email", "")).lower() != email]
        data["custom"].append({"name": name, "email": email})
        if email in data["hidden"]:
            data["hidden"].remove(email)
        self._save_custom(data)
        return self.contacts_merged()

    def contact_update(self, old_email: str, name: str, email: str) -> list[dict]:
        old_email = (old_email or "").strip().lower()
        email = (email or "").strip().lower()
        name = (name or "").strip()
        if not self._valid_email(email):
            raise PingheError("邮箱地址不合法")
        if not name:
            raise PingheError("姓名不能为空")
        data = self._custom()
        if any(str(c.get("email", "")).lower() == old_email for c in data["custom"]):
            data["custom"] = [c for c in data["custom"]
                              if str(c.get("email", "")).lower() != old_email]
        elif old_email not in data["hidden"]:
            data["hidden"].append(old_email)   # 收割条目改名 = 隐藏旧地址
        data["custom"] = [c for c in data["custom"]
                          if str(c.get("email", "")).lower() != email]
        data["custom"].append({"name": name, "email": email})
        # 换了新地址才解隐藏新地址; 只改名(新旧同址)时旧地址必须保持隐藏,
        # 否则收割原条目会重新出现, 出现同名重复
        if email != old_email and email in data["hidden"]:
            data["hidden"].remove(email)
        self._save_custom(data)
        return self.contacts_merged()

    def contact_delete(self, email: str) -> list[dict]:
        email = (email or "").strip().lower()
        data = self._custom()
        before = len(data["custom"])
        data["custom"] = [c for c in data["custom"]
                          if str(c.get("email", "")).lower() != email]
        if len(data["custom"]) == before and email not in data["hidden"]:
            data["hidden"].append(email)   # 收割条目: 打隐藏标记
        self._save_custom(data)
        return self.contacts_merged()

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
            raise PingheError("时间格式应为 HH:MM")
        conn = self._conn_factory()
        return storage.events_add(conn, day, time_, title.strip(), note or "")

    def list_range(self, day_from: str, day_to: str) -> list[dict]:
        conn = self._conn_factory()
        return storage.events_list(conn, day_from, day_to)

    def month(self, month: str) -> list[dict]:
        if not re.match(r"^\d{4}-\d{2}$", month):
            raise PingheError("月份格式应为 YYYY-MM")
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
        raise PingheError("日期格式应为 YYYY-MM-DD")


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
            session_file = Path.home() / ".hellopinghe" / f"session_{host}.json"
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
        session_file = Path.home() / ".hellopinghe" / f"session_{host}.json"
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
            raise PingheError(f"提交失败: HTTP {resp.status_code}")
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
