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
from .. import paths, storage
from ..logutil import log as _log

KEYRING_SERVICE = "hellopinghe"

# HL/SL 层级后缀(HL1/SL2/HL/SL1), 可后跟 (G3) 这类班别括号
_LEVEL_RE = re.compile(r"\s*(?:HL\s*/\s*SL|HL|SL)\s*\d?\s*(\([^)]*\))?\s*$")


def subject_family(name: str) -> str:
    """科目族: 去掉课名里的 HL/SL 层级后缀(保留 (G3) 这类班别括号).

    同一个教学组一周内的课卡会换名(History HL/SL2 ↔ History HL2),
    按课名精确匹配会丢卡 —— 选课匹配一律用科目族, 层级由 组号+老师 区分。
    """
    n = (name or "").strip()
    return _LEVEL_RE.sub(r"\1", n).strip()


# ---------------------------------------------------------------- 密钥存储
# Windows: DPAPI 加密 JSON 存数据目录(便携要求); 其他平台: keyring。
# 统一从 secrets 模块走, 本文件保留旧名字供 bridge 等处引用。
from ..secrets import (  # noqa: E402
    delete as secret_del,
    get as secret_get,
    set as secret_set,
)


# ================================================================ Edupage
class EdupageService:
    """持有 edupage-api 会话; 提供全校总课表、选课个人课表、科目选项."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._ed = None
        self._lock = threading.Lock()
        # Edupage 的 gcall 课卡是"按登录账号可见"的 —— 换账号登录后数据完全
        # 不同, 所以所有缓存键必须带班级 id(跨账号隔离), 否则 B 账号会读到
        # A 账号缓存的课卡, _for_my_class 过滤后几乎全部消失。
        self._plan_cache: dict[tuple, list] = {}
        self._week_cache: dict[tuple, dict[str, list]] = {}
        self._rooms_cache: list | None = None

    def _ed_caches_clear(self) -> None:
        """登录账号切换后清空 Edupage 相关缓存."""
        self._plan_cache.clear()
        self._week_cache.clear()

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

        _log(f"Edupage login: {username} @ {subdomain}")
        ed = Edupage()
        self._patch(ed)
        ed.login(username, password, subdomain)
        self._speed_patch(ed)
        self._ed = ed
        self._ed_caches_clear()   # 换账号: 旧账号的课卡缓存全部作废
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

        带磁盘缓存(6 小时过期, v4 文件名带班级 id —— gcall 课卡按登录
        账号可见, 不同账号的数据完全不同, 缓存绝不能跨账号共用)。
        """
        monday = self.week_monday(start)
        cid = self.my_class_id()
        key = (cid, monday)
        if key in self._week_cache:
            return self._week_cache[key]

        cache_file = paths.data_dir() / f"edupage_week_v4_{monday.isoformat()}_{cid or 'all'}.json"
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
        last = monday + timedelta(days=days)
        for anchor_off in (0, 3, 6):   # 窗口: 周日~周二 / 周二~周四 / 周五~周日
            try:
                win = fetch_window(monday + timedelta(days=anchor_off))
                for day_key, day_data in (win.get("dates") or {}).items():
                    # 周日锚点的窗口会带出下周周一, 裁剪到请求区间内
                    try:
                        if monday <= date.fromisoformat(day_key) < last:
                            merged[day_key] = day_data
                    except ValueError:
                        continue   # 非 ISO 日期键, 忽略
            except Exception:  # noqa: BLE001
                continue
        if not merged:
            raise PingheError("课表拉取失败: gcall 三个窗口均无返回")
        data = {"dates": merged}

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            # 清掉旧版缓存(v2 截断 / v3 泄漏日 / v3 跨账号共用)
            for pattern in ("edupage_week_2*.json", "edupage_week_v2_*.json",
                            "edupage_week_v3_*.json"):
                for old in cache_file.parent.glob(pattern):
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
        # 回退: 单日拉取(缓存键带班级 id, 跨账号隔离)
        cid = self.my_class_id()
        mkey = (cid, day)
        if mkey in self._plan_cache:
            return self._plan_cache[mkey]
        ed = self._ensure()
        tt = ed.get_my_timetable(day)
        lessons = list(tt or [])
        self._plan_cache[mkey] = lessons
        return lessons

    def rooms(self) -> list[str]:
        if self._rooms_cache is None:
            ed = self._ensure()
            self._rooms_cache = [r.name for r in (ed.get_classrooms() or [])]
        return self._rooms_cache

    # ---- 课程来源: 只取本班的课 ----
    def my_class_id(self) -> int | None:
        """当前账号的班级 dbi id(负数, 如 -359 = IB grade 11 class 9).

        学校的 Edupage 服务端不做按班/按人过滤(gcall 只按登录人的可见班级
        出课卡, 班级课表接口对学生返回权限错误), 过滤只能客户端做。
        班级 id 优先取 userrow.TriedaID, 回退解析 userGroups 的 "Trieda-N";
        都取不到时返回 None → 不过滤, 退回整年级行为。
        """
        if self._ed is None:
            return None
        data = self._ed.data or {}
        raw = (data.get("userrow") or {}).get("TriedaID")
        try:
            if raw:
                return int(raw)
        except (TypeError, ValueError):
            pass
        groups = data.get("userGroups")
        if isinstance(groups, dict):
            keys = list(groups)
        elif isinstance(groups, (list, tuple)):
            keys = list(groups)
        else:
            return None
        for key in keys:
            m = re.fullmatch(r"Trieda-(\d+)", str(key))
            if m:
                return -int(m.group(1))
        return None

    def _for_my_class(self, lesson) -> bool:
        """课程来源过滤: 别的班级的课不算进来.

        课卡 classids 的三种情况:
        - 只含本班 id        → 班级专属课(各班的会考课), 保留
        - 含本班 + 其他班    → 本班参与的合班/走班课, 保留
        - 不含本班 id        → 纯别的班的课, 丢弃
        - 无班级标注         → 全年级性的课, 保守保留
        """
        cid = self.my_class_id()
        if cid is None:
            return True
        classes = getattr(lesson, "classes", None) or []
        if not classes:
            return True
        return any(getattr(c, "class_id", None) == cid for c in classes)

    def subject_options(self, progress=None) -> list[dict]:
        """选课来源: 按"教学组"聚合 (科目族 + 组号 + 老师 = 一个选项).

        学校自己的课表页每个时段列的就是 组|教室|老师|课名; 同一个教学组
        全周多张课卡 = 同一个选项, 勾一次全周生效。
        聚合键是 (科目族, 组号, 老师): 组号会被复用(Psychology 组F 有两个
        教学组、TOK 组O 有两个), 所以老师参与构成身份; 课名会换
        (History HL/SL2 ↔ History HL2), 所以用科目族; 教室只展示不参与。
        """
        monday = self.week_monday(date.today())
        if progress:
            progress({"day": f"{monday} ~ {monday + timedelta(days=5)}",
                      "attempt": 1, "total": 2})
        plans = self.week_plans(monday, days=6)
        if not any(plans.values()):  # 整周无课(假期) → 试下一周
            if progress:
                progress({"day": f"{monday + timedelta(days=7)} 起", "attempt": 2, "total": 2})
            plans = self.week_plans(monday + timedelta(days=7), days=6)

        # (family, group, teacher) -> {display, rooms, times}
        grouped: dict[tuple, dict] = {}
        for day_iso in sorted(plans):
            day_name = f"周{'一二三四五六日'[date.fromisoformat(day_iso).weekday()]}"
            for l in plans[day_iso]:
                if l.is_cancelled or not l.subject or not l.start_time:
                    continue
                if not self._for_my_class(l):
                    continue
                fam = subject_family(l.subject.name)
                group = ",".join(l.groups) if l.groups else ""
                teacher = l.teachers[0].name.strip() if l.teachers else ""
                room = l.classrooms[0].name if l.classrooms else ""
                ent = grouped.setdefault((fam, group, teacher), {
                    "subject": l.subject.name, "group": group,
                    "teacher": teacher or "(未指定老师)",
                    "rooms": set(), "times": []})
                ent["rooms"].add(room)
                ent["times"].append({
                    "_iso": day_iso, "day": day_name,
                    "start": l.start_time.strftime("%H:%M"),
                    "end": l.end_time.strftime("%H:%M") if l.end_time else ""})

        by_subject: dict[str, list] = {}
        for (fam, _g, _t), ent in grouped.items():
            times = sorted(ent["times"], key=lambda x: (x["_iso"], x["start"]))
            by_subject.setdefault(fam, []).append({
                "id": f"{fam}|{ent['teacher']}|{ent['group']}",
                "group": ent["group"],
                "teacher": ent["teacher"],
                "subject": ent["subject"],
                "rooms": sorted(x for x in ent["rooms"] if x),
                "times": [{"day": t["day"], "start": t["start"], "end": t["end"]}
                          for t in times]})
        return [
            {"subject": fam,
             "groups": sorted(rows, key=lambda r: (r["group"] == "", r["group"],
                                                   r["teacher"]))}
            for fam, rows in sorted(by_subject.items())
        ]

    def personal(self, day: date) -> list[dict]:
        """按选课结果过滤出的个人课表(当天) —— 对所有账号一视同仁.

        通用两层规则(没有任何账号特判, 全部由 Edupage 课卡数据驱动):
        ① 选课命中(科目族+组号+老师) → 显示;
        ② 课卡无教学组 = 全班必修(班会/国家课程这类 Edupage 不打组的课) → 显示;
        其余(有组但未选) = 年级里其他同学的并行选项, 不显示。
        未跑向导的新账号会先看到必修课, 选完课后选修课自动出现。

        带磁盘缓存(2 小时, v6 文件名带班级 id + 选课哈希 —— 课卡按登录
        账号可见, 缓存绝不能跨账号共用)。
        """
        import hashlib

        selected = self.cfg.selected_lessons or []
        sel_key = hashlib.sha1(
            json.dumps(selected, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        cid = self.my_class_id()
        cache_dir = paths.data_dir()
        cache_file = (cache_dir /
                      f"edupage_personal_v6_{day.isoformat()}_{cid or 'all'}_{sel_key}.json")
        if cache_file.exists():
            age = _time.time() - cache_file.stat().st_mtime
            if age < 2 * 3600:
                try:
                    return json.loads(cache_file.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass  # 缓存损坏则重新计算

        out = []
        for l in self.master_plan(day):
            if not self._for_my_class(l):
                continue
            subject = (l.subject.name if l.subject else "").strip()
            card_teachers = {t.name.strip() for t in (l.teachers or [])}
            group = ",".join(l.groups) if l.groups else ""
            card_groups = [g.strip() for g in group.split(",") if g.strip()]
            if card_groups:  # 有组 → 必须命中选课之一
                if not any(
                    subject_family(subject) == subject_family(s.get("subject") or "")
                    and (not s.get("teacher") or s["teacher"] in card_teachers)
                    and (not s.get("group") or s["group"] in card_groups)
                    for s in selected
                ):
                    continue
            out.append({
                "start": l.start_time.strftime("%H:%M") if l.start_time else "",
                "end": l.end_time.strftime("%H:%M") if l.end_time else "",
                "subject": subject,
                "teacher": l.teachers[0].name.strip() if l.teachers else "",
                "room": l.classrooms[0].name if l.classrooms else "",
                "group": group,
                "cancelled": bool(l.is_cancelled),
                "curriculum": getattr(l, "curriculum", None) or "",
            })
        out.sort(key=lambda x: (x["start"], x["subject"]))
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            for pattern in ("edupage_personal_2*.json", "edupage_personal_v2_*.json",
                            "edupage_personal_v3_*.json", "edupage_personal_v4_*.json",
                            "edupage_personal_v5_*.json"):
                for old in cache_dir.glob(pattern):
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
        email = (self.cfg.mail_email or "").strip()
        authcode = secret_get(f"mail_authcode:{email}")
        if authcode:
            return authcode.strip()
        pw = secret_get(f"mail:{email}")
        if not pw or not email:
            raise LoginRequiredError("mail")
        return pw.strip()

    def set_authcode(self, email: str, authcode: str) -> None:
        """保存客户端授权码并确保后续连接使用该授权码。

        授权码统一去掉全部空白(网页上复制时可能带空格/换行)。
        """
        code = "".join((authcode or "").split())
        if code:
            secret_set(f"mail_authcode:{email.strip()}", code)

    def _imap_error(self, exc: Exception) -> PingheError:
        extra = ""
        err_str = str(exc)
        if "ERR.ILLEGAL.EMAIL" in err_str:
            extra = ("。ERR.ILLEGAL.EMAIL 通常表示该账号未开通 IMAP 客户端服务: "
                     "请登录 mail.shphschool.com → 设置 → 客户端设置 → 开启 IMAP 并生成客户端授权密码; "
                     "若已开启仍报错, 请联系学校管理员为你的账号开通客户端协议")
        elif "ERR.LOGIN.REQCODE" in err_str:
            extra = ("。ERR.LOGIN.REQCODE = 服务器要求客户端授权码, 但提供的凭据无效或已过期。\n"
                     "解决方法:\n"
                     "  1. 登录 mail.shphschool.com\n"
                     "  2. 进入 设置 → 客户端设置\n"
                     "  3. 确认 IMAP/SMTP 服务已开启\n"
                     "  4. 删除旧授权码 → 新增授权码(会生成一个新的授权码字符串)\n"
                     "  5. 把新授权码填到本应用的设置页里")
        elif "INVALID" in err_str.upper() or "AUTH" in err_str.upper():
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

        email = (self.cfg.mail_email or "").strip()
        try:
            M = imaplib.IMAP4_SSL(self.cfg.mail_imap_host, 993)
            pw = self._password()
            try:
                M.login(email, pw)
            except Exception as auth_exc:
                # 授权码登录失败 → 尝试存的网页密码(两者都有时互为备份)
                webpw = secret_get(f"mail:{email}")
                if not webpw or webpw.strip() == pw:
                    raise self._imap_error(auth_exc) from auth_exc
                try:
                    M.login(email, webpw.strip())
                except Exception as exc2:  # noqa: BLE001
                    raise self._imap_error(auth_exc) from exc2
            M.select("INBOX")
            return M
        except LoginRequiredError:
            raise
        except PingheError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._imap_error(exc) from exc

    def configure(self, email: str, imap_host: str = "", smtp_host: str = "") -> None:
        """只更新邮箱连接配置; 密码/授权码由调用方经 set_authcode/secret_set 存储."""
        if imap_host:
            self.cfg.mail_imap_host = imap_host
        if smtp_host:
            self.cfg.mail_smtp_host = smtp_host
        self.cfg.mail_email = email.strip()

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
            # 提取附件列表
            attachments = []
            for part in msg.walk():
                cd = str(part.get("Content-Disposition") or "")
                if "attachment" not in cd and not part.get_filename():
                    continue
                fname = part.get_filename() or "unnamed"
                if fname.startswith("=?"):
                    fname = self._decode(fname)
                attachments.append({
                    "index": len(attachments),
                    "filename": fname[:200],
                    "size": len(part.get_payload(decode=True) or b""),
                })

            return {
                "uid": uid,
                "subject": self._decode(msg["Subject"]) or "(无主题)",
                "from": self._decode(msg["From"]),
                "date": (msg["Date"] or "")[:24],
                "to": self._decode(msg["To"]),
                "cc": self._decode(msg.get("Cc") or ""),
                "body": body[:20000],
                "is_html": body_is_html,
                "attachments": attachments,
            }
        finally:
            try:
                M.logout()
            except Exception:  # noqa: BLE001
                pass

    def read_attachment(self, uid: str, part_index: int, filename: str) -> str:
        """提取邮件附件内容, 保存到临时文件, 返回文件路径."""
        import email as _email
        import imaplib
        import tempfile

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
            atts = [p for p in msg.walk()
                    if "attachment" in str(p.get("Content-Disposition") or "")
                    or p.get_filename()]
            if part_index >= len(atts):
                raise PingheError("附件不存在")
            payload = atts[part_index].get_payload(decode=True) or b""
            safe_name = re.sub(r'[<>:"/\\|?*]', "_", filename)
            out = Path(os.path.expanduser("~")) / "Downloads" / safe_name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(payload)
            return str(out)
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

        email = (self.cfg.mail_email or "").strip()
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = email
        msg["To"] = to
        with smtplib.SMTP_SSL(self.cfg.mail_smtp_host, 994, timeout=30) as smtp:
            smtp.login(email, self._smtp_password())
            smtp.sendmail(email, [to], msg.as_string())


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
            session_file = paths.data_dir() / f"session_{host}.json"
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
        session_file = paths.data_dir() / f"session_{host}.json"
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

    # ---------- 课程详情页(Files/Calendar/Units/任务详情)与 CAS/EE ----------
    def class_files(self, class_id: str) -> list[dict]:
        self.ensure_login()
        return self._client_ready().get_class_files(class_id)

    def class_events(self, class_id: str) -> list:
        self.ensure_login()
        return self._client_ready().get_class_events(class_id)

    def class_units(self, class_id: str) -> dict:
        self.ensure_login()
        return self._client_ready().get_class_units(class_id)

    def task_detail(self, class_id: str, task_id: str) -> dict:
        self.ensure_login()
        return self._client_ready().get_task_detail(class_id, task_id)

    def cas_overview(self) -> dict:
        self.ensure_login()
        return self._client_ready().get_cas_overview()

    def ee_overview(self) -> dict:
        self.ensure_login()
        return self._client_ready().get_ee_overview()

    def submit_task(self, class_id: str, task_id: str, file_path: str) -> str:
        """交作业: 从任务页动态解析提交入口再 multipart 上传.

        实测 shph 契约(2026-09-05 只读探测): Dropbox 页的上传表单
        method=post action=`.../dropbox/upload`, 文件字段
        `dropbox[assets_attributes][0][file]`, 且带隐藏域
        `_method=patch`(Rails 伪装 PATCH —— 路由只认 PATCH, 纯 POST
        会 404) 和 `dropbox[assets_attributes][0][file_cache]`。
        所以 POST 必须**原样照抄表单全部隐藏域**, 不能自己拼 data。
        给的 id 打不开任务页时, 扫当前全部课程按 task_id 重新定位。
        """
        client = self._client_ready()
        from bs4 import BeautifulSoup

        def _csrf(soup) -> str:
            tag = soup.find("input", attrs={"name": "authenticity_token"})
            if tag and tag.get("value"):
                return tag["value"]
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            return meta.get("content") if meta else ""

        def _form_entry(form, soup):
            """从单个 form 提取 (file字段名, action, 全部隐藏域 dict)."""
            fi = form.find("input", attrs={"type": "file"})
            if fi is None:
                return None
            hidden: dict = {}
            for inp in form.find_all("input", attrs={"type": "hidden"}):
                name = inp.get("name")
                if name:
                    hidden[name] = inp.get("value") or ""
            # 提交按钮的 name/value 也是表单数据(如 commit=Upload Files)
            btn = (form.find("input", attrs={"type": "submit"})
                   or form.find("button", attrs={"name": True}))
            if btn is not None and btn.get("name"):
                hidden[btn.get("name")] = btn.get("value") or ""
            # data-remote 表单的 token 在 meta csrf-token 里(浏览器走
            # X-CSRF-Token 头); 复刻时表单域 + 请求头双保险
            tok = hidden.get("authenticity_token") or _csrf(soup)
            if tok:
                hidden["authenticity_token"] = tok
            return (fi.get("name") or "dropbox_assets_attributes_0_file",
                    form.get("action") or "",
                    hidden)

        def _find_entry(soup):
            """找提交入口 → (file 字段名, action, 隐藏域 dict)."""
            # ① 当前页面里就有 dropbox 上传表单(必须 post + dropbox action)
            for form in soup.find_all("form"):
                action = form.get("action") or ""
                if "dropbox" not in action.lower():
                    continue
                if (form.get("method") or "get").lower() != "post":
                    continue
                hit = _form_entry(form, soup)
                if hit:
                    return hit
            # ② 带本任务 id 的 dropbox 链接 → 打开子页面找上传表单
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if task_id not in href or "dropbox" not in href.lower():
                    continue
                try:
                    sub = client._get(href)
                except Exception as exc:  # noqa: BLE001
                    raise PingheError(f"提交页打不开: {exc}") from exc
                sub_soup = BeautifulSoup(sub.text, "html.parser")
                for form in sub_soup.find_all("form"):
                    action = form.get("action") or ""
                    if "dropbox" not in action.lower():
                        continue
                    if (form.get("method") or "get").lower() != "post":
                        continue
                    hit = _form_entry(form, sub_soup)
                    if hit:
                        # 空 action = Rails 提交到当前页路径
                        action = action or (
                            str(sub.url).replace(client.base_url, "") or href)
                        return (hit[0], action, hit[2])
                raise PingheError(
                    "提交页里没有找到上传表单, 请到 ManageBac 网页手动提交")
            return None, None, None

        task_path = f"/student/classes/{class_id}/core_tasks/{task_id}"
        try:
            page = client._get(task_path)
        except Exception:  # noqa: BLE001
            page = None
        if page is None:
            # 给的 class/task id 可能来自上学期缓存或旧会话(任务页 404)。
            # 扫一遍当前课程的 core_tasks, 用卡片里的真实 href 重新定位。
            try:
                for cid in client.get_classes():
                    try:
                        listing = client._get(f"/student/classes/{cid}/core_tasks")
                    except Exception:  # noqa: BLE001
                        continue
                    m = re.search(
                        r'href="([^"]*/core_tasks/%s\b[^"]*)"' % re.escape(str(task_id)),
                        listing.text)
                    if m:
                        task_path = m.group(1)
                        page = client._get(task_path)
                        break
                    _time.sleep(0.2)
            except Exception:  # noqa: BLE001
                pass
            if page is None:
                raise PingheError(
                    f"找不到任务 {task_id}(在课程 {class_id} 和当前所有课程里都没有): "
                    "任务可能已被删除/归档, 请到 ManageBac 网页确认后再试")
        soup = BeautifulSoup(page.text, "html.parser")

        field, action, hidden = _find_entry(soup)
        if not action:
            # 兜底: 试几个历史版本的常见路由
            for cand in (
                f"{task_path}/dropbox",
                f"{task_path}/dropbox_files/new",
                task_path.replace("/core_tasks/", "/tasks/") + "/dropbox",
            ):
                try:
                    page = client._get(cand)
                except Exception:  # noqa: BLE001
                    continue
                soup = BeautifulSoup(page.text, "html.parser")
                field, action, hidden = _find_entry(soup)
                if action:
                    break
        if not action:
            raise PingheError(
                "这个任务的页面上没有找到可用的提交入口(可能已截止、类型不支持"
                "网上提交, 或需要老师开放), 请到 ManageBac 网页手动提交")

        post_url = client._url(action)
        headers = {"X-CSRF-Token": hidden["authenticity_token"]} \
            if hidden.get("authenticity_token") else {}
        with open(file_path, "rb") as fh:
            resp = client.session.post(
                post_url,
                data=hidden,   # 含 _method=patch / file_cache / commit / token
                files={field: (Path(file_path).name, fh)},
                headers=headers,
                timeout=180,
            )
        if resp.status_code >= 400:
            raise PingheError(
                f"提交失败: HTTP {resp.status_code} (POST {post_url}) — "
                "请到 ManageBac 网页手动提交, 若反复出现请把此提示反馈给开发者")
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
