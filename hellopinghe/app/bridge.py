"""js_api 桥接层: 前端通过 window.pywebview.api.<方法>(参数) 调用.

约定: 所有方法返回 {ok: bool, ...数据 | error}.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time as _time
import uuid

from ..config import Config
from ..exceptions import LoginRequiredError, PingheError
from ..logutil import warn as _log_warn, error as _log_error
from .. import sharedschool as _sharedschool
from .. import cloudsync as _cs
from .. import filestore as fs
from .. import phixsession as _phix
from .agent import AgentEngine, detect_ai_environment
from .services import Services, secret_set, secret_get


def _phix_call(fn, *args, **kwargs) -> dict:
    """phix 系列方法的错误包装：把 PhixError 翻成带 code 的友好中文。"""
    try:
        data = fn(*args, **kwargs)
        return {"ok": True, "data": data}
    except _cs.PhixError as exc:
        _log_warn(f"phix: {exc.code}: {exc.message}")
        return {"ok": False, "error": exc.message, "code": exc.code}
    except Exception as exc:  # noqa: BLE001
        _log_error(f"phix error: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

# ---------------------------------------------------------------- 数据快照缓存
# 进程内 TTL 缓存: 同一份数据(首页/课表/课程/邮件列表)短时间重复请求零等待,
# 前端"缓存优先 + 后台刷新"策略依赖这里的低延迟。
_SNAP: dict[str, tuple[float, object]] = {}
_SNAP_LOCK = threading.Lock()


def _snap_get(key: str, ttl: float):
    with _SNAP_LOCK:
        hit = _SNAP.get(key)
        if hit and _time.monotonic() - hit[0] < ttl:
            return hit[1]
    return None


def _snap_put(key: str, value) -> None:
    with _SNAP_LOCK:
        _SNAP[key] = (_time.monotonic(), value)


def _snap_drop(*keys: str) -> None:
    with _SNAP_LOCK:
        for k in keys:
            _SNAP.pop(k, None)


def _snap_drop_prefix(prefix: str) -> None:
    """按前缀整批失效(tt|0 / tt|-1 … 这种带参数的键没法逐个枚举)."""
    with _SNAP_LOCK:
        for k in [k for k in _SNAP if k.startswith(prefix)]:
            _SNAP.pop(k, None)


def _parse_attachment_json(text) -> list:
    """把前端传来的附件参数(JSON 文本)解析成列表.

    前端只传文本, 各种"没传/空串/传了对象/传了拼坏的两段 JSON"都可能出现 ——
    发信不该因为这些参数形态崩掉, 也**不许**静默把附件丢了: 只要里面真带了
    附件规格就一定解析出来, 实在解析不了才报中文错。

    以前这里直接用 `json.loads`, 遇到坏文本会冒出
    "附件参数解析失败: Expecting value: line 1 column 2 (char 1)" 这种看不懂的
    报错(日志里真的出现过), 现在按下面的顺序兜底。
    """
    if not text:
        return []
    if isinstance(text, list):
        return text
    if isinstance(text, dict):
        return [text]
    raw = str(text).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001  单段解析不了 → 试试"两段数组拼在一起"
        data = None
        # 浏览器/中间层偶发把两次调用拼成 "[] []" 这种; 逐段解析再合并
        parts = re.findall(r"\[[\s\S]*?\]", raw)
        if len(parts) > 1:
            merged: list = []
            ok_all = True
            for chunk in parts:
                try:
                    got = json.loads(chunk)
                except Exception:  # noqa: BLE001
                    ok_all = False
                    break
                if isinstance(got, list):
                    merged.extend(got)
                elif isinstance(got, dict):
                    merged.append(got)
            if ok_all:
                return merged
        raise PingheError(
            "附件参数看不懂(前端传来的不是合法的 JSON 数组)。"
            "请重新选一次附件再发; 若反复出现请把这一行日志发给开发者: "
            f"附件参数={raw[:200]!r}")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise PingheError(f"附件参数格式不对(应该是数组), 实际是 {type(data).__name__}")


def _wrap(fn, *args, **kwargs) -> dict:
    try:
        data = fn(*args, **kwargs)
        if isinstance(data, dict) and "ok" in data:
            return data
        return {"ok": True, "data": data}
    except LoginRequiredError as exc:
        _log_warn(f"LoginRequired: {exc}")
        return {"ok": False, "login_required": str(exc), "error": f"需要重新登录 {exc}"}
    except PingheError as exc:
        _log_error(f"PingheError: {exc}")
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        # 特殊处理 edupage-api 的 BadCredentialsException
        err_msg = str(exc) or type(exc).__name__
        exc_name = type(exc).__name__
        if "BadCredentials" in exc_name:
            err_msg = "账号或密码错误(BadCredentials)"
        elif not err_msg or err_msg == exc_name:
            # 某些异常 str() 为空，用类型名作为后备
            err_msg = f"未知错误({exc_name})"
        _log_error(f"bridge error: {exc_name}: {err_msg}")
        return {"ok": False, "error": f"{exc_name}: {err_msg}"}


class Api:
    #: phix 登录态只从本机令牌恢复一次（见 `phix_status`）
    _phix_restored = False

    def __init__(self):
        self.cfg = Config.load()
        self.svc = Services(self.cfg)
        self.agent = AgentEngine(self.cfg, self.svc)
        #: 主窗口引用（`__main__` 创建好后通过 attach_window 挂进来）——窗口控件要用它。
        #: **必须是下划线开头**：pywebview 会把 js_api 对象的公开属性递归展开成 JS 接口，
        #: 公开一个 Window 对象会让它去遍历 `window.native.*` 直接爆栈（实测踩过）。
        self._window = None

    def attach_window(self, window) -> None:
        """把 pywebview 的窗口对象挂进来（最小化/最大化/关闭按钮要用）。"""
        self._window = window

    # ================================================================ 窗口控件
    # 无边框窗口（用户 2026-09-21 要求：窗口控件要是软件自己的一部分）：
    # 界面自己画那三个按钮，动作全落到下面这几个方法上。
    def win_minimize(self) -> dict:
        try:
            if self._window is not None:
                self._window.minimize()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def win_maximize_toggle(self) -> dict:
        try:
            from . import window_chrome

            state = window_chrome.maximize_toggle(self._window)
            return {"ok": True, "maximized": state}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc), "maximized": False}

    def win_state(self) -> dict:
        try:
            from . import window_chrome

            return {"ok": True, "maximized": window_chrome.is_maximized()}
        except Exception:  # noqa: BLE001
            return {"ok": False, "maximized": False}

    def win_drag_start(self) -> dict:
        """自绘标题栏上按下鼠标 → 走系统原生拖动（带 Aero Snap）。"""
        try:
            from . import window_chrome

            return {"ok": bool(window_chrome.drag_start())}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def win_close(self) -> dict:
        """✕ = 关窗进托盘（与窗口右上角原来的 × 行为一致，见 __main__ 的 _on_closing）。"""
        try:
            if self._window is not None:
                self._window.hide()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def win_get_bounds(self) -> dict:
        """界面开始拖边缘把手时取一次当前位置/大小。"""
        from . import window_chrome

        return window_chrome.get_bounds()

    def win_set_bounds(self, x: int, y: int, width: int, height: int) -> dict:
        """界面的边缘把手用它缩放（无边框 + 不加 THICKFRAME，所以自己实现缩放）。"""
        from . import window_chrome

        return window_chrome.set_bounds(x, y, width, height)

    def _save_cfg(self) -> None:
        self.cfg.save()

    # ================================================================ 向导
    def wizard_status(self) -> dict:
        return _wrap(lambda: {
            "done": self.cfg.wizard_done,
            "edupage_ready": bool(self.cfg.edupage_username and self.cfg.edupage_subdomain),
            "managebac_ready": bool(self.cfg.managebac_base_url),
            "selected_count": len(self.cfg.selected_lessons),
            "ai_ready": bool(self.cfg.ai_providers and self.cfg.agent_model),
        })

    def wizard_edupage_login(self, username: str, password: str, subdomain: str) -> dict:
        def job():
            self.svc.edupage.login(username.strip(), password, subdomain.strip())
            self.cfg.edupage_username = username.strip()
            self.cfg.edupage_subdomain = subdomain.strip()
            self._save_cfg()
            return {"subdomain": self.cfg.edupage_subdomain}
        return _wrap(job)

    def wizard_managebac_login(self, url: str, email: str, password: str) -> dict:
        def job():
            base = url.strip()
            if not base.startswith("http"):
                base = f"https://{base}"
            self.svc.courses.login(base, email.strip(), password)
            self._save_cfg()
            return {"base_url": self.cfg.managebac_base_url}
        return _wrap(job)

    def wizard_subject_options(self) -> dict:
        def job():
            def progress(info):
                self._push_event({"type": "subjects_progress", **info})
            return self.svc.edupage.subject_options(progress=progress)
        return _wrap(job)

    def wizard_mail_save(self, email: str, password: str, authcode: str,
                         imap_host: str, smtp_host: str) -> dict:
        def job():
            email = email.strip()
            # 网页登录密码存入密钥存储(作为备份; 空值不覆盖已有密码)
            if password.strip():
                secret_set(f"mail:{email}", password.strip())
            # 客户端授权码用于 IMAP/SMTP 登录(网易企业邮的核心要求)
            self.svc.mail.set_authcode(email, authcode)
            self.svc.mail.configure(email, imap_host.strip(), smtp_host.strip())
            self._save_cfg()
            self.svc.mail._unread_cache = None
            _snap_drop("home")
            count = self.svc.mail.unread_count()  # 验证登录
            return {"verified": True, "unread": count}
        return _wrap(job)

    def wizard_save_selection(self, selection_json: str) -> dict:
        def job():
            items = json.loads(selection_json)
            cleaned = []
            seen = set()
            for it in items:
                subject = (it.get("subject") or "").strip()
                if not subject:
                    continue
                key = (subject, (it.get("teacher") or "").strip(),
                       (it.get("group") or "").strip())
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append({
                    "subject": key[0],
                    "teacher": key[1],
                    "group": key[2],
                })
            self.cfg.selected_lessons = cleaned
            self._save_cfg()
            # 选课一变, 个人课表/首页(含今日课)立刻作废 —— 不然改完选课
            # 切到课表页, 快照 TTL 内返回的还是旧课表。
            _snap_drop_prefix("tt|")
            _snap_drop("home")
            return {"selected": len(cleaned)}
        return _wrap(job)

    def wizard_ai_env(self) -> dict:
        return _wrap(detect_ai_environment)

    def wizard_ai_save(self, preset: str, api_key: str, base_url: str,
                       model: str, protocol: str, local: bool) -> dict:
        def job():
            from ..config import PROVIDER_PRESETS

            tpl = PROVIDER_PRESETS.get(preset)
            name = tpl.name if tpl else (preset or "自定义").capitalize()
            base = base_url.strip() or (tpl.base_url if tpl else "")
            if local:
                base = "http://localhost:11434/v1"
            models = [model.strip()] if model.strip() else (
                [tpl.model] if tpl and tpl.model else []
            )
            provider = {
                "id": f"p-{preset}-{uuid.uuid4().hex[:6]}",
                "name": name,
                "protocol": "openai",
                "base_url": base,
                "api_key": ("ollama" if local else api_key.strip()),
                "models": models,
                "notes": tpl.notes if tpl else "",
            }
            self.cfg.ai_providers = [
                p for p in self.cfg.ai_providers if p.get("id") != provider["id"]
            ] + [provider]
            self.cfg.agent_provider_id = provider["id"]
            self.cfg.agent_model = models[0] if models else ""
            self._save_cfg()
            return {"provider": name, "model": self.cfg.agent_model}
        return _wrap(job)

    def wizard_finish(self) -> dict:
        def job():
            self.cfg.wizard_done = True
            self._save_cfg()
            return {"done": True}
        return _wrap(job)

    # ================================================================ 首页
    def home_data(self) -> dict:
        def job():
            from datetime import date, datetime, time as dtime, timedelta

            from .. import storage

            cached = _snap_get("home", 60)
            if cached is not None:
                return cached

            now = datetime.now()
            data: dict = {
                "now": now.strftime("%Y-%m-%d"),
                "weekday": "周" + "一二三四五六日"[now.weekday()],
                "clock": now.strftime("%H:%M:%S"),
            }

            # 三路并行: edupage(课表) / managebac(DDL) / mail(未读),
            # 避免串行时相互拖慢首页首屏。
            def _edupage():
                try:
                    lessons = self.svc.edupage.personal(now.date())
                    data["today_lessons"] = lessons
                    hm = now.strftime("%H:%M")
                    current = next(
                        (l for l in lessons if l["start"] and l["end"]
                         and l["start"] <= hm < l["end"]),
                        None,
                    )
                    data["current_lesson"] = current
                    # 下一节课: 不限于今天 —— 今天还没开始的最近一节;
                    # 今天没了(如周五晚/周末)就往后找, 最多看 7 天,
                    # 比如周六看首页 → 周一第一节。跳过已取消的课。
                    nxt = None
                    for offset in range(8):
                        day = now.date() + timedelta(days=offset)
                        try:
                            day_lessons = (lessons if offset == 0
                                           else self.svc.edupage.personal(day))
                        except Exception:  # noqa: BLE001
                            continue
                        day_lessons = sorted(
                            (l for l in day_lessons
                             if l.get("start") and not l.get("cancelled")),
                            key=lambda l: l["start"])
                        cand = (next((l for l in day_lessons if l["start"] > hm), None)
                                if offset == 0
                                else (day_lessons[0] if day_lessons else None))
                        if cand:
                            nxt = dict(cand)
                            nxt["day"] = day.isoformat()
                            nxt["day_label"] = "周" + "一二三四五六日"[day.weekday()]
                            break
                    data["next_lesson"] = nxt
                except Exception as exc:  # noqa: BLE001
                    data["today_lessons"] = []
                    data["current_lesson"] = None
                    data["next_lesson"] = None
                    data["timetable_error"] = str(exc)

            def _ddl():
                try:
                    # 时间戳可能带本地时区偏移(规范 v1)也可能不带: 统一成 aware
                    # 再比较/相减, 否则会出现 offset-naive 与 offset-aware 的
                    # TypeError(管理册 DDL 那一行就是这么崩的)。
                    now2 = datetime.now().astimezone()

                    def _aware(value: str) -> datetime:
                        moment = datetime.fromisoformat(value)
                        return moment if moment.tzinfo else moment.replace(tzinfo=now2.tzinfo)

                    # 首页与"我的课程"页统一口径: 前 5 天 ~ 后 14 天
                    # (按日期粒度比较, 今天的作业整天都算在窗口内)
                    lo = (now2 - timedelta(days=5)).date().isoformat()
                    hi = now2 + timedelta(days=14)
                    ddl = [
                        it for it in self.svc.courses.deadlines(days=14)
                        if it["due_at"] and it["due_at"][:10] >= lo
                        and _aware(it["due_at"]) <= hi
                    ]
                    host = self.cfg.managebac_base_url.split("//")[-1]
                    dismissed = storage.ddl_dismissed_keys(self.svc._conn(), host)
                    out = []
                    for it in ddl:
                        key = f'{it["title"]}|{it["due_at"]}'
                        if key in dismissed:
                            continue
                        it = dict(it)
                        it["key"] = key
                        delta = abs((_aware(it["due_at"]) - now2).total_seconds())
                        it["urgent"] = delta <= 2 * 86400
                        out.append(it)
                    out.sort(key=lambda x: x["due_at"])
                    data["ddl"] = out
                except Exception as exc:  # noqa: BLE001
                    data["ddl"] = []
                    data["ddl_error"] = str(exc)

            def _mail():
                try:
                    data["unread_mail"] = self.svc.mail.unread_count()
                except Exception as exc:  # noqa: BLE001
                    data["unread_mail"] = None
                    data["mail_error"] = str(exc)

            # 今日日程(本地 SQLite, 主线程即可)
            data["today_events"] = self.svc.schedule.list_range(
                now.date().isoformat(), now.date().isoformat()
            )
            threads = [threading.Thread(target=f, daemon=True)
                       for f in (_edupage, _ddl, _mail)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            _snap_put("home", data)
            return data
        return _wrap(job)

    # ================================================================ 我的课表
    def timetable_week(self, offset_weeks: int = 0) -> dict:
        def job():
            from datetime import date, timedelta

            key = f"tt|{int(offset_weeks)}"
            cached = _snap_get(key, 120)
            if cached is not None:
                return cached

            today = date.today()
            monday = today - timedelta(days=today.weekday()) + timedelta(weeks=int(offset_weeks))
            week = []
            for offset in range(7):
                day = monday + timedelta(days=offset)
                try:
                    lessons = self.svc.edupage.personal(day)
                    error = ""
                except Exception as exc:  # noqa: BLE001
                    lessons, error = [], str(exc)
                week.append({
                    "day": day.isoformat(),
                    "label": f"周{'一二三四五六日'[day.weekday()]} {day.strftime('%m-%d')}",
                    "lessons": lessons,
                    "error": error,
                })
            out = {"week": week}
            _snap_put(key, out)
            return out
        return _wrap(job)

    # ================================================================ 我的日程
    def schedule_month(self, month: str) -> dict:
        return _wrap(lambda: self.svc.schedule.month(month))

    def schedule_range(self, day_from: str, day_to: str) -> dict:
        """日程视图(周/月/年)用: 一次取一段区间, 本地 SQLite 毫秒级."""
        return _wrap(lambda: {"events": self.svc.schedule.list_range(day_from, day_to)})

    def schedule_add(self, day: str, time_: str, title: str, note: str) -> dict:
        return _wrap(lambda: {"id": self.svc.schedule.add(day, time_, title, note)})

    def schedule_update(self, event_id: int, day: str, time_: str, title: str, note: str) -> dict:
        def job():
            self.svc.schedule.update(int(event_id), day, time_, title, note)
            return {"updated": int(event_id)}
        return _wrap(job)

    def schedule_delete(self, event_id: int) -> dict:
        def job():
            self.svc.schedule.delete(int(event_id))
            return {"deleted": int(event_id)}
        return _wrap(job)

    # ================================================================ 教室安排
    def classrooms_state(self, at: str = "now") -> dict:
        def job():
            from datetime import date, datetime, time as dtime

            if at == "now":
                moment = datetime.now().time().replace(microsecond=0)
            else:
                moment = dtime(int(at.split(":")[0]), int(at.split(":")[1]))
            occ = self.svc.free_rooms.occupancy(date.today(), moment)
            occ["at"] = moment.strftime("%H:%M")
            return occ
        return _wrap(job)

    # ================================================================ 年级课表
    def gradett_data(self, day_str: str = "") -> dict:
        def job():
            from datetime import date as _date

            day = _date.fromisoformat(day_str) if day_str else _date.today()
            key = f"gt|{day.isoformat()}"
            cached = _snap_get(key, 300)
            if cached is not None:
                return cached

            self._push_event({"type": "plan_loading", "day": str(day)})
            lessons = self.svc.edupage.master_plan(day)

            slots: dict[str, list] = {}
            for l in lessons:
                t = l.start_time.strftime("%H:%M") if l.start_time else "??"
                slots.setdefault(t, []).append({
                    "start": t, "end": (l.end_time.strftime("%H:%M") if l.end_time else ""),
                    "subject": l.subject.name if l.subject else "?",
                    "teacher": l.teachers[0].name if l.teachers else "-",
                    "room": l.classrooms[0].name if l.classrooms else "",
                    "groups": ",".join(l.groups) if l.groups else "",
                    "classes": [getattr(c, "name", "") for c in (l.classes or [])],
                    "cancelled": bool(l.is_cancelled),
                })
            # 国家理科：同一格里的国家物理/化学/生物是同一门课的轮换卡，班级课表也合并成一条
            # （用户 2026-09-21：「就显示一个」）。其它卡片原样不动。
            from .services import merge_native_science

            ordered = [{"time": t, "lessons": merge_native_science(slots[t])} for t in sorted(slots)]
            out = {"day": day.isoformat(), "count": len(lessons), "slots": ordered}
            _snap_put(key, out)
            return out
        return _wrap(job)

    def ddl_dismiss(self, key: str) -> dict:
        def job():
            from .. import storage

            host = self.cfg.managebac_base_url.split("//")[-1]
            storage.ddl_dismiss(self.svc._conn(), host, key)
            return {"dismissed": key}
        return _wrap(job)

    def ddl_dismissed_list(self) -> dict:
        """已移除的作业清单(设置页恢复用)."""
        def job():
            from .. import storage

            host = self.cfg.managebac_base_url.split("//")[-1]
            rows = storage.ddl_dismissed_rows(self.svc._conn(), host)
            items = []
            for r in rows:
                title, _, due = r["key"].partition("|")
                items.append({"key": r["key"], "title": title,
                              "due_at": due, "created": r["created"]})
            return {"items": items}
        return _wrap(job)

    def ddl_restore(self, key: str) -> dict:
        """恢复误移除的作业(撤销左滑删除)."""
        def job():
            from .. import storage

            host = self.cfg.managebac_base_url.split("//")[-1]
            storage.ddl_restore(self.svc._conn(), host, key)
            _snap_drop("home", "courses")
            return {"restored": key}
        return _wrap(job)

    # ================================================================ 心履
    def xinlv_status(self) -> dict:
        return _wrap(lambda: self.svc.xinlv.status())

    def xinlv_login(self, username: str, password: str) -> dict:
        return _wrap(lambda: self.svc.xinlv.login(username, password))

    def xinlv_register(self, username: str, password: str, agree: bool) -> dict:
        return _wrap(lambda: self.svc.xinlv.register(username, password, bool(agree)))

    def xinlv_logout(self) -> dict:
        def job():
            self.svc.xinlv.logout()
            return {"logged_out": True}
        return _wrap(job)

    def xinlv_sync(self) -> dict:
        return _wrap(lambda: self.svc.xinlv.sync())

    def xinlv_add(self, date: str, at: str, mood: str, note: str,
                  level: int, percent: int) -> dict:
        return _wrap(lambda: self.svc.xinlv.add_entry(
            date, at, mood, note, int(level or 2), int(percent or 50)))

    def xinlv_delete(self, uuid: str) -> dict:
        return _wrap(lambda: self.svc.xinlv.delete_entry(uuid))

    def xinlv_month(self, month: str) -> dict:
        return _wrap(lambda: self.svc.xinlv.month(month))

    def xinlv_recommend(self, mood: str) -> dict:
        return _wrap(lambda: self.svc.xinlv.recommend(mood))

    def xinlv_profile(self) -> dict:
        return _wrap(lambda: self.svc.xinlv.profile())

    # ================================================================ phix 统一账号 + 云同步
    #
    # 这一组方法与四平台无关：phix 是把「心履」和「PH Launcher / PLL」打通的**同一套账号**。
    # 登录后 DEK 只留在内存里（进程退出就没了），云端只存密文，服务端解不开。
    # 细节见 D:\phix\phix-协议规范.md。
    def phix_status(self) -> dict:
        def job():
            # 第一次问状态时先把本机存着的令牌接回来 —— 否则程序重启后
            # `logged_in` 恒为 False，设置页会**又显示登录框**（用户实测报的现象），
            # 自动同步也永远不启动。恢复不发网络请求，很便宜。
            if not Api._phix_restored:
                Api._phix_restored = True
                try:
                    _phix.SESSION.restore()
                except Exception as exc:  # noqa: BLE001  恢复失败不影响展示状态
                    _log_warn(f"phix 登录状态恢复失败（会显示成未登录）：{exc}")
            return _phix.SESSION.status()
        return _phix_call(job)

    def phix_ping(self, server: str = "") -> dict:
        def job():
            # 空地址 = 自动选择（内网自建 → phix.ing）——界面不让用户填服务器地址，
            # 见 phixsession.resolve_server() 的注释。
            client = _cs.PhixClient(_phix.resolve_server(server))
            info = client.ping()
            return {
                "server": client.server,
                "version": info.get("version"),
                "server_time": info.get("server_time"),
                "service_verify_enabled": info.get("service_verify_enabled"),
                "limits": info.get("limits"),
            }
        return _phix_call(job)

    def phix_resolve_server(self) -> dict:
        """界面用：把"会自动连哪台服务器"报回去（探测失败也不抛异常）。"""
        return _phix.server_probe()

    def phix_register(self, server: str, username: str, password: str,
                      key_mode: str = "password") -> dict:
        return _phix_call(lambda: _phix.SESSION.register(
            server, username, password, key_mode or "password"))

    def phix_login(self, server: str, username: str, password: str,
                   sync_passphrase: str = "") -> dict:
        return _phix_call(lambda: _phix.SESSION.login(
            server, username, password, (sync_passphrase or "").strip() or None))

    def phix_unlock(self, sync_passphrase: str) -> dict:
        return _phix_call(lambda: _phix.SESSION.unlock(sync_passphrase))

    def phix_logout(self) -> dict:
        return _phix_call(lambda: _phix.SESSION.logout())

    def phix_sync(self, force: bool = False, prefer: str = "merge") -> dict:
        """`prefer`：`merge` / `local`（本地覆盖云端）/ `remote`（云端覆盖本地）。"""
        def job():
            report = _phix.SESSION.sync(force=bool(force),
                                        prefer=(prefer or "merge"))
            return {"report": report, "summary": _phix._brief(report),
                    "status": _phix.SESSION.status()}
        return _phix_call(job)

    def phix_sync_probe(self) -> dict:
        """登录后判断要不要问用户"用本地覆盖云端 / 用云端覆盖本地"。

        返回 `{needs_choice, both:[对象名], objects:{名:{local,remote}}}`。
        只读，不写任何同步文件，登录成功后立刻调是安全的。
        """
        return _phix_call(lambda: _phix.SESSION.sync_probe())

    def phix_sync_preview(self, prefer: str = "merge") -> dict:
        """只算不写：让用户先看清楚这轮会拉什么、推什么。"""
        def job():
            report = _phix.SESSION.sync(force=True, dry_run=True,
                                        prefer=(prefer or "merge"))
            return {"report": report, "summary": _phix._brief(report)}
        return _phix_call(job)

    def phix_conflicts(self) -> dict:
        def job():
            st = _phix.SESSION.status()
            return {"conflicts": (st.get("state") or {}).get("conflicts") or []}
        return _phix_call(job)

    def phix_settings_save(self, payload_json: str) -> dict:
        def job():
            payload = json.loads(payload_json or "{}")
            changes = {}
            if "auto_sync" in payload:
                changes["auto_sync"] = bool(payload["auto_sync"])
            if payload.get("sync_interval_minutes"):
                changes["sync_interval_minutes"] = max(
                    2, int(payload["sync_interval_minutes"]))
            if isinstance(payload.get("objects"), list):
                changes["objects"] = [str(x) for x in payload["objects"]]
            if payload.get("device"):
                changes["device"] = str(payload["device"])[:100]
            if changes:
                _phix.save_config(**changes)
            if changes.get("auto_sync") is False:
                _phix.SESSION.stop_auto_sync()
            elif _phix.SESSION.dek is not None:
                _phix.SESSION.start_auto_sync()
            return _phix.SESSION.status()
        return _phix_call(job)

    def phix_devices(self) -> dict:
        """本账号的登录设备（会话）列表 —— P3 的 `/auth/devices`。

        返回 `{sessions, devices, access_ttl, refresh_ttl}`：
        `sessions` 是正式形态（一次登录 = 一个会话），`devices` 是兼容期的老式令牌。
        服务端**不下发** refresh 明文，这里只是转发。
        """
        return _phix_call(lambda: _phix.SESSION.devices())

    def phix_revoke_device(self, session_id: int = 0,
                           all_except_current: bool = False) -> dict:
        """注销某一台设备（会话）；`all_except_current=True` = 注销本机以外的全部。

        返回**注销后**的设备列表，界面直接重绘即可。
        """
        return _phix_call(lambda: _phix.SESSION.revoke_device(
            session_id=int(session_id) if session_id else None,
            all_except_current=bool(all_except_current)))

    def phix_set_passphrase(self, login_password: str, sync_passphrase: str) -> dict:
        """切到"独立同步口令"：以后连服务端都解不开你的数据。"""
        return _phix_call(lambda: _phix.SESSION.set_sync_passphrase(
            login_password, sync_passphrase))

    def phix_change_password(self, old_password: str, new_password: str) -> dict:
        return _phix_call(lambda: _phix.SESSION.change_password(
            old_password, new_password))

    def phix_use_login_password(self, login_password: str,
                                new_login_password: str = "") -> dict:
        """从"独立同步口令"切回"用登录密码包裹"。"""
        return _phix_call(lambda: _phix.SESSION.use_login_password(
            login_password, new_login_password))

    def phix_recover(self, server: str, username: str, recovery_code: str,
                     new_password: str) -> dict:
        """忘记密码：用恢复码重设。不需要旧密码、不需要先登录。"""
        return _phix_call(lambda: _phix.recover(
            server, username, recovery_code, new_password))

    def phix_open_data_dir(self) -> dict:
        def job():
            d = fs.root() / _cs.SYNC_DIR
            target = d if d.exists() else fs.root()
            try:
                os.startfile(str(target))  # noqa: S606  Windows 打开资源管理器
            except Exception as exc:  # noqa: BLE001
                return {"path": str(target), "opened": False, "error": str(exc)}
            return {"path": str(target), "opened": True}
        return _wrap(job)

    def phix_profile_get(self) -> dict:
        """读取 profile 同步对象（头像 / 显示名）。"""
        def job():
            import json as _json
            from datetime import datetime, timezone
            p = fs.root() / "Profile"
            doc = fs.load_json(p, None) or {}
            return {
                "display_name": doc.get("display_name") or "",
                "avatar": doc.get("avatar") or "",
                "updated_at": doc.get("updated_at") or "",
            }
        return _wrap(job)

    def phix_profile_save(self, payload_json: str) -> dict:
        """写入 profile 同步对象并推一次同步（若已登录）。"""
        def job():
            import json as _json
            from datetime import datetime, timezone
            payload = _json.loads(payload_json or "{}")
            p = fs.root() / "Profile"
            doc = fs.load_json(p, None) or {}
            if "display_name" in payload:
                doc["display_name"] = str(payload["display_name"])[:50]
            if "avatar" in payload:
                doc["avatar"] = str(payload["avatar"])
            doc["updated_at"] = datetime.now(timezone.utc).isoformat()
            fs.save_json(p, doc)
            # 若已登录 phix，推一次同步让 profile 上云
            if _phix.SESSION.dek is not None:
                try:
                    _phix.SESSION.sync(force=True, objects=["profile"])
                except Exception:  # noqa: BLE001
                    pass
            return {
                "display_name": doc.get("display_name") or "",
                "avatar": doc.get("avatar") or "",
                "updated_at": doc.get("updated_at") or "",
            }
        return _wrap(job)

    def xinlv_catalog(self, force: bool = False) -> dict:
        return _wrap(lambda: self.svc.xinlv.catalog(bool(force)))

    # ================================================================ 启动连接页
    def connect_edupage(self) -> dict:
        def job():
            from datetime import date, timedelta

            monday = date.today() - timedelta(days=date.today().weekday())
            plans = self.svc.edupage.week_plans(monday, days=6)
            return {"rooms": len(self.svc.edupage.rooms()), "days": len(plans)}
        return _wrap(job)

    def connect_managebac(self) -> dict:
        def job():
            return {"classes": len(self.svc.courses.classes())}
        return _wrap(job)

    def connect_mail(self) -> dict:
        def job():
            return {"unread": self.svc.mail.unread_count()}
        return _wrap(job)

    # ================================================================ 我的课程
    def courses_data(self) -> dict:
        def job():
            from .. import storage

            cached = _snap_get("courses", 180)
            if cached is not None:
                return cached
            classes = self.svc.courses.classes()
            tasks = self.svc.courses.all_tasks()
            grades = self.svc.courses.grades()
            # 与首页同一时间窗: 前 5 天 ~ 后 14 天(没有截止时间的留下当"随时可交")
            from datetime import datetime as _dt, timedelta as _td

            lo = ( _dt.now() - _td(days=5)).date().isoformat()
            hi = (_dt.now() + _td(days=14)).isoformat(timespec="minutes")
            tasks = [
                t for t in tasks
                if not t.get("due_at") or (t["due_at"][:10] >= lo and t["due_at"] <= hi)
            ]
            # 过滤掉用户左滑移除过的 DDL (与首页同一套 dismissed key)
            host = self.cfg.managebac_base_url.split("//")[-1]
            dismissed = storage.ddl_dismissed_keys(self.svc._conn(), host)
            tasks = [
                t for t in tasks
                if f'{t.get("title", "")}|{t.get("due_at") or ""}' not in dismissed
            ]
            # 未截止(按截止时间升序, 手动排序优先) + 已过期(最近的在前)
            # —— 首页只显示未截止的, 过期作业在本页完整可查
            # 缺字段的条目(对方程序写的旧数据)不能让整页报错: 一律用 get 取值。
            upcoming = [t for t in tasks if not t.get("past_due")]
            past = [t for t in tasks if t.get("past_due")]
            rank = {k: i for i, k in enumerate(self.cfg.task_order)}
            upcoming.sort(key=lambda t: (
                rank.get(f'{t.get("title", "")}|{t.get("due_at") or ""}', len(rank)),
                t.get("due_at") or "",
            ))
            past.sort(key=lambda t: t.get("due_at") or "", reverse=True)
            # 按用户拖拽保存的顺序排课程, 未出现的课程追加在后;
            # 总评直接并进行里(课程列表一行 = 课程名 + 总评徽章)
            order = list(self.cfg.course_class_order)
            rank = {cid: i for i, cid in enumerate(order)}
            class_list = [
                {"id": k, "name": v, "grade": grades.get(v)}
                for k, v in classes.items()
            ]
            class_list.sort(
                key=lambda c: (rank.get(c["id"], len(order)), c["name"]))
            out = {
                "classes": class_list,
                "tasks_upcoming": upcoming[:60],
                "tasks_past": past[:60],
                "grades": grades,
            }
            _snap_put("courses", out)
            return out
        return _wrap(job)

    def mail_contacts(self, force: bool = False) -> dict:
        """通讯录(自动补全 + AI 联系人查询共用)。磁盘缓存 24h。"""
        def job():
            key = f"contacts|{bool(force)}"
            cached = _snap_get(key, 600)
            if cached is not None:
                return cached
            out = {"contacts": self.svc.mail.contacts_merged()}
            _snap_put(key, out)
            return out
        return _wrap(job)

    def mail_contact_add(self, name: str, email: str) -> dict:
        def job():
            out = {"contacts": self.svc.mail.contact_add(name, email)}
            _snap_drop("contacts|True", "contacts|False")
            return out
        return _wrap(job)

    def mail_contact_update(self, old_email: str, name: str, email: str) -> dict:
        def job():
            out = {"contacts": self.svc.mail.contact_update(old_email, name, email)}
            _snap_drop("contacts|True", "contacts|False")
            return out
        return _wrap(job)

    def mail_contact_delete(self, email: str) -> dict:
        def job():
            out = {"contacts": self.svc.mail.contact_delete(email)}
            _snap_drop("contacts|True", "contacts|False")
            return out
        return _wrap(job)

    def course_save_order(self, order_json: str) -> dict:
        """保存"我的课程"里拖拽后的课程顺序。"""
        def job():
            raw = json.loads(order_json or "[]")
            order = [str(x) for x in raw if str(x)]
            self.cfg.course_class_order = order
            self._save_cfg()
            # 顺序一变就作废 courses 快照, 不然 TTL 内重进页面还是旧顺序
            _snap_drop("courses")
            return {"saved": len(order)}
        return _wrap(job)

    def task_save_order(self, order_json: str) -> dict:
        """保存"我的课程"作业条目的自定义顺序(▲▼ 箭头调整)。"""
        def job():
            raw = json.loads(order_json or "[]")
            order = [str(x) for x in raw if str(x)]
            self.cfg.task_order = order
            self._save_cfg()
            _snap_drop("courses")
            return {"saved": len(order)}
        return _wrap(job)

    def course_tasks(self, class_id: str) -> dict:
        def job():
            tasks = self.svc.courses.all_tasks()
            return {"tasks": [t for t in tasks if t["class_id"] == str(class_id)]}
        return _wrap(job)

    # ------------------- 课程详情页 / 任务详情 / CAS·EE -------------------
    def course_files(self, class_id: str) -> dict:
        def job():
            key = f"cfiles|{class_id}"
            cached = _snap_get(key, 300)   # 下载链接是短时效预签名, 别缓存太久
            if cached is not None:
                return cached
            out = {"files": self.svc.courses.class_files(class_id)}
            _snap_put(key, out)
            return out
        return _wrap(job)

    def course_events(self, class_id: str) -> dict:
        def job():
            key = f"cevents|{class_id}"
            cached = _snap_get(key, 300)
            if cached is not None:
                return cached
            out = {"events": self.svc.courses.class_events(class_id)}
            _snap_put(key, out)
            return out
        return _wrap(job)

    def course_units(self, class_id: str) -> dict:
        def job():
            key = f"cunits|{class_id}"
            cached = _snap_get(key, 600)
            if cached is not None:
                return cached
            out = self.svc.courses.class_units(class_id)
            _snap_put(key, out)
            return out
        return _wrap(job)

    def course_discussions(self, class_id: str) -> dict:
        def job():
            key = f"cdisc|{class_id}"
            cached = _snap_get(key, 180)
            if cached is not None:
                return cached
            out = {"discussions": self.svc.courses.class_discussions(class_id)}
            _snap_put(key, out)
            return out
        return _wrap(job)

    def discussion_detail(self, class_id: str, discussion_id: str) -> dict:
        def job():
            out = self.svc.courses.discussion_detail(class_id, discussion_id)
            return {"discussion": out}
        return _wrap(job)

    def discussion_reply(self, class_id: str, discussion_id: str,
                         body_html: str, private: bool = False) -> dict:
        def job():
            body = (body_html or "").strip()
            if not body:
                raise PingheError("回复内容不能为空")
            self.svc.courses.post_discussion_reply(
                class_id, discussion_id, body, private=private)
            _snap_drop_prefix("cdisc|")
            return {"posted": True}
        return _wrap(job)

    def task_detail(self, class_id: str, task_id: str) -> dict:
        def job():
            key = f"tdetail|{class_id}|{task_id}"
            cached = _snap_get(key, 300)
            if cached is not None:
                return cached
            out = self.svc.courses.task_detail(class_id, task_id)
            _snap_put(key, out)
            return out
        return _wrap(job)

    def cas_overview(self) -> dict:
        def job():
            cached = _snap_get("cas", 600)
            if cached is not None:
                return cached
            out = self.svc.courses.cas_overview()
            _snap_put("cas", out)
            return out
        return _wrap(job)

    def ee_overview(self) -> dict:
        def job():
            cached = _snap_get("ee", 600)
            if cached is not None:
                return cached
            out = self.svc.courses.ee_overview()
            _snap_put("ee", out)
            return out
        return _wrap(job)

    # ---- CAS / EE：软件内提交（表单字段由页面自己决定，不猜路由） ----
    def core_forms(self, kind: str = "cas") -> dict:
        """页面上真实存在的可提交表单（只读）。界面据此渲染真表单。"""
        return _wrap(lambda: self.svc.courses.core_forms(kind))

    def core_submit(self, kind: str, payload_json: str) -> dict:
        """提交一份 CAS / EE 记录；`payload_json` = {form_index, values, file_path}。"""
        def job():
            out = self.svc.courses.core_submit(kind, payload_json)
            # 概览快照作废，回列表时是最新的
            _snap_drop("cas" if kind != "ee" else "ee")
            return out
        return _wrap(job)

    def core_pick_file(self) -> dict:
        """为 CAS / EE 表单挑一个附件（只挑，不提交）。

        取消选择同样走 `{"ok": True, ...}` 信封 —— 前端 `call()` 只认 `ok===true`，
        否则"点了取消"会显示成"调用失败"。
        """
        import webview

        picked = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False)
        if not picked:
            return {"ok": True, "data": {"cancelled": True}}
        path = str(picked[0])
        return {"ok": True, "data": {"cancelled": False, "path": path,
                                     "name": os.path.basename(path)}}

    def open_external(self, url: str) -> dict:
        """用系统默认浏览器打开 ManageBac 页面/文件下载链接(只放行 http/https)."""
        def job():
            import webbrowser

            if not re.match(r"^https?://", str(url or "")):
                raise ValueError("只允许打开 http(s) 链接")
            webbrowser.open(str(url))
            return {"opened": str(url)}
        return _wrap(job)

    def task_pick_and_submit(self, class_id: str, task_id: str) -> dict:
        """作业详情弹卡的"提交作业": 先弹系统文件选择框(模态, 在 js_api
        调用线程上阻塞, 同 agent_pick_workspace 的模式), 用户选完文件后
        再进后台线程走 submit_task 的动态入口解析上传。取消则不动。"""
        import webview

        picked = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False)
        if not picked:
            # 必须是 `{"ok": True, ...}` 完整信封：前端 `call()` 只认 `ok===true`，
            # 之前直接返回 `{"cancelled": True}` 会让"用户点了取消"显示成
            # "调用失败"，前端那句 `if (r.cancelled)` 是永远走不到的死代码。
            return {"ok": True, "data": {"cancelled": True}}
        path = str(picked[0])

        def job():
            msg = self.svc.courses.submit_task(class_id, task_id, path)
            # 详情/列表缓存作废, 下次打开看到最新提交状态
            _snap_drop(f"tdetail|{class_id}|{task_id}")
            _snap_drop("courses", "home")
            return {"cancelled": False, "submitted": True, "message": msg,
                    "path": path}
        return _wrap(job)

    def refresh_tasks(self) -> dict:
        def job():
            _snap_drop("courses", "home")
            return {"tasks": self.svc.courses.all_tasks(force=True)[:80]}
        return _wrap(job)

    # ================================================================ 邮箱
    def mail_list(self, unseen_only: bool = False, limit: int = 30) -> dict:
        def job():
            key = f"mail|{bool(unseen_only)}|{int(limit)}"
            cached = _snap_get(key, 45)
            if cached is not None:
                return cached
            try:
                out = {"mails": self.svc.mail.list_mail(unseen_only, limit), "shared": False}
            except PingheError:
                # 本机没登录邮箱(或登不上)时，用另一个程序写进 data/School 的摘要兜底：
                # 只显示标题，读正文仍需在本机登录。真正的错误仍然抛出去，不吞。
                summary = _sharedschool.mail_summary()
                if unseen_only or not summary["items"]:
                    raise
                out = {
                    "mails": summary["items"],
                    "shared": True,
                    "note": "这是另一个程序上次同步到的邮箱摘要（只能看标题）"
                            + (f"，读取于 {summary['fetched_at']}" if summary["fetched_at"] else "")
                            + "。要读正文，请在本机登录邮箱。",
                }
            _snap_put(key, out)
            return out
        return _wrap(job)

    def mail_read(self, uid: str) -> dict:
        return _wrap(lambda: self.svc.mail.read(uid))

    def mail_download_attachment(self, uid: str, part_index: int, filename: str) -> dict:
        def job():
            import platform
            import subprocess

            path = self.svc.mail.read_attachment(uid, int(part_index), filename)
            system = platform.system()
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return {"path": path}
        return _wrap(job)

    def mail_unread(self) -> dict:
        return _wrap(lambda: {"count": self.svc.mail.unread_count()})

    def mail_send(self, to: str, subject: str, body: str, cc: str = "",
                  bcc: str = "", attachments_json: str = "") -> dict:
        """发信。`attachments_json` 是附件数组的 JSON 文本, 每项支持三种写法:
        `{"name":..,"path":..}` / `{"name":..,"data_base64":..}` / `"路径"`。
        老调用方 `mail_send(to, subject, body)` 照旧能用。"""
        def job():
            specs = _parse_attachment_json(attachments_json)
            result = self.svc.mail.send(to.strip(), subject, body,
                                        cc=cc, bcc=bcc, attachments=specs)
            self.svc.mail._unread_cache = None
            _snap_drop_prefix("mail|")
            _snap_drop("home")
            return result
        return _wrap(job)

    def mail_prefill(self, uid: str, mode: str = "reply") -> dict:
        """回复 / 转发的预填内容(前端"回复""转发"按钮用)。

        `mode="reply"`:  收件人 = Reply-To 优先, 没有才用 From; 主题 `Re: `;
                        正文引用块; **不带**原附件。
        `mode="forward"`: 收件人留空; 主题 `Fwd: `; 正文引用块;
                        **带上原附件**(按字节转 base64 交给前端, 超限的记在 skipped)。
        """
        import email as _email
        import imaplib

        m = (mode or "reply").strip().lower()
        if m not in ("reply", "forward"):
            return {"ok": False, "error": f"不认识的模式: {mode}"}

        def job():
            M = self.svc.mail._conn()
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
                # 回信/转发说明这封已经读过了, 与"点开就算已读"保持一致
                try:
                    M.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                except Exception:  # noqa: BLE001
                    pass
                data = (self.svc.mail.reply_prefill(msg) if m == "reply"
                        else self.svc.mail.forward_prefill(msg))
                data["uid"] = uid
                return data
            finally:
                try:
                    M.logout()
                except Exception:  # noqa: BLE001
                    pass
        return _wrap(job)

    def mail_pick_attachments(self) -> dict:
        """弹系统文件选择框(可多选), 返回选中的**真实路径**。

        与 task_pick_and_submit / agent_pick_workspace 同一模式: 模态对话框在
        js_api 调用线程上阻塞, 用户选完再返回。前端用这些路径去发信,
        Python 端直接读文件, 不经过 base64。
        """
        import webview

        try:
            picked = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=True)
        except Exception as exc:  # noqa: BLE001
            _log_warn(f"mail_pick_attachments: {exc}")
            return {"ok": False, "error": f"打不开文件选择框: {exc}"}
        if not picked:
            return {"ok": True, "data": {"cancelled": True, "files": []}}
        files = []
        for path in picked:
            try:
                size = os.path.getsize(str(path))
            except OSError:
                continue
            files.append({"name": os.path.basename(str(path)),
                          "path": str(path), "size": size})
        return {"ok": True, "data": {"cancelled": False, "files": files}}
        return _wrap(job)

    # ================================================================ Agent
    def _push_event(self, obj: dict) -> None:
        """把 agent 的流式增量/工具活动推给前端."""
        try:
            import webview

            if webview.windows:
                payload = json.dumps(obj, ensure_ascii=False)
                webview.windows[0].evaluate_js(
                    f"window.__agentEvent && window.__agentEvent({payload});"
                )
        except Exception:  # noqa: BLE001
            pass

    def agent_state(self) -> dict:
        def job():
            provider = self.cfg.active_provider()
            models = provider.get("models") or []
            return {
                "workspace": self.cfg.agent_workspace,
                "workspaces": self.agent.list_workspaces(),
                "mode": self.agent.mode,
                "provider": {
                    "name": provider.get("name", ""),
                    "model": self.cfg.agent_model or (models[0] if models else "?"),
                    "protocol": provider.get("protocol", "openai"),
                    "base_url": provider.get("base_url", ""),
                    "has_key": bool(provider.get("api_key")),
                },
                "proposals": self.agent.list_proposals(),
            }
        return _wrap(job)

    def agent_set_mode(self, mode: str) -> dict:
        """切换 Agent 权限模式。workspace_write/full_access 的双重确认
        警告由前端负责展示, 后端只校验取值并持久化。"""
        def job():
            if mode not in ("readonly", "confirm", "workspace_write", "full_access"):
                raise ValueError(f"未知的权限模式: {mode}")
            self.cfg.agent_mode = mode
            self._save_cfg()
            return {"mode": mode}
        return _wrap(job)

    def ai_get(self) -> dict:
        def job():
            # 供应商配置可能刚被**云同步**从别的端(网页端 / PHL)写进 settings.yaml。
            # 不重读的话界面显示的还是启动时那一份, 用户会以为"同步没生效"。
            # 只重读磁盘上这份文档, 不碰任何凭据。
            try:
                fresh = Config.load()
                self.cfg.ai_providers = fresh.ai_providers
                self.cfg.agent_provider_id = fresh.agent_provider_id
                self.cfg.agent_model = fresh.agent_model
                if isinstance(getattr(fresh, "_ai_raw", None), dict):
                    self.cfg._ai_raw = dict(fresh._ai_raw)
            except Exception:  # noqa: BLE001  读不到就用内存里这份, 绝不阻塞界面
                pass
            providers = []
            for p in self.cfg.ai_providers:
                providers.append({
                    "id": p.get("id", ""),
                    "name": p.get("name", ""),
                    "protocol": p.get("protocol", "openai"),
                    "base_url": p.get("base_url", ""),
                    "has_key": bool(p.get("api_key")),
                    "models": list(p.get("models") or []),
                    "notes": p.get("notes", ""),
                })
            return {
                "providers": providers,
                "active_provider_id": self.cfg.agent_provider_id,
                "active_model": self.cfg.agent_model,
            }
        return _wrap(job)

    def ai_save_all(self, payload_json: str) -> dict:
        """整体保存供应商列表(前端整卡编辑; api_key 留空 = 保留旧值)."""
        def job():
            data = json.loads(payload_json)
            old_keys = {
                p.get("id", ""): p.get("api_key", "") for p in self.cfg.ai_providers
            }
            cleaned = []
            for p in data.get("providers") or []:
                pid = (p.get("id") or f"p-{uuid.uuid4().hex[:6]}").strip()
                key = p.get("api_key") or old_keys.get(pid, "")
                cleaned.append({
                    "id": pid,
                    "name": (p.get("name") or "未命名").strip(),
                    "protocol": p.get("protocol") if p.get("protocol") in ("openai", "anthropic") else "openai",
                    "base_url": (p.get("base_url") or "").strip(),
                    "api_key": key,
                    "models": [m.strip() for m in (p.get("models") or []) if m.strip()],
                    "notes": p.get("notes", ""),
                })
            self.cfg.ai_providers = cleaned
            ids = {p["id"] for p in cleaned}
            act = data.get("active_provider_id")
            self.cfg.agent_provider_id = act if act in ids else (cleaned[0]["id"] if cleaned else "")
            self.cfg.agent_model = data.get("active_model") or ""
            self._save_cfg()
            return {"count": len(cleaned), "active": self.cfg.agent_provider_id}
        return _wrap(job)

    def ai_set_active(self, provider_id: str, model: str) -> dict:
        def job():
            if not any(p.get("id") == provider_id for p in self.cfg.ai_providers):
                raise PingheError("提供商不存在")
            self.cfg.agent_provider_id = provider_id
            self.cfg.agent_model = model
            self._save_cfg()
            return {"provider": provider_id, "model": model}
        return _wrap(job)

    def agent_set_workspace(self, path: str) -> dict:
        def job():
            result = self.agent.set_workspace(path)
            self._save_cfg()
            return result
        return _wrap(job)

    def agent_new_workspace(self, name: str) -> dict:
        def job():
            result = self.agent.new_workspace(name)
            self._save_cfg()
            return result
        return _wrap(job)

    def agent_pick_workspace(self) -> dict:
        """弹出系统文件夹选择对话框, 选中后设为 agent workspace。"""
        import webview

        picked = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG)
        if not picked:
            # 同 task_pick_and_submit：取消也要走 `ok` 信封，否则前端报"调用失败"
            return {"ok": True, "data": {"cancelled": True}}

        def job():
            path = str(picked[0])
            result = self.agent.set_workspace(path)
            self._save_cfg()
            result["workspace"] = path
            return result
        return _wrap(job)

    def agent_files(self) -> dict:
        return _wrap(lambda: self.agent._dispatch("list_workspace", {}))

    def agent_open_explorer(self) -> dict:
        def job():
            import platform
            import subprocess

            root = self.agent.workspace_root()
            if root is None:
                raise PingheError("未设置 workspace")
            system = platform.system()
            if system == "Windows":
                import os

                os.startfile(str(root))  # noqa: S606
            elif system == "Darwin":
                subprocess.Popen(["open", str(root)])
            else:
                subprocess.Popen(["xdg-open", str(root)])
            return {"opened": str(root)}
        return _wrap(job)

    def agent_sessions(self) -> dict:
        def job():
            return {"sessions": self.agent.list_sessions(), "current": self.agent.session_id}
        return _wrap(job)

    def agent_open_session(self, sid: str) -> dict:
        return _wrap(lambda: self.agent.load_session(sid))

    def agent_new_session(self) -> dict:
        return _wrap(lambda: self.agent.new_session())

    def agent_chat(self, message: str) -> dict:
        def job():
            self.agent.on_event = self._push_event
            try:
                return self.agent.chat(message)
            finally:
                self.agent.on_event = None
        return _wrap(job)

    def agent_reset(self) -> dict:
        return _wrap(self.agent.reset)

    def agent_proposals(self) -> dict:
        return _wrap(lambda: {"proposals": self.agent.list_proposals()})

    def agent_confirm(self, pid: str) -> dict:
        return _wrap(lambda: self.agent.confirm(pid))

    def agent_reject(self, pid: str) -> dict:
        return _wrap(lambda: self.agent.reject(pid))

    # ================================================================ 设置
    def settings_get(self) -> dict:
        def job():
            return {
                "managebac_base_url": self.cfg.managebac_base_url,
                "managebac_email": self.cfg.managebac_email,
                "edupage_username": self.cfg.edupage_username,
                "edupage_subdomain": self.cfg.edupage_subdomain,
                "mail_email": self.cfg.mail_email,
                "mail_imap_host": self.cfg.mail_imap_host,
                "mail_smtp_host": self.cfg.mail_smtp_host,
                "selected_lessons": self.cfg.selected_lessons,
                "send_grades_to_llm": self.cfg.send_grades_to_llm,
            }
        return _wrap(job)

    def settings_save(self, payload_json: str) -> dict:
        def job():
            payload = json.loads(payload_json)
            mb_url = payload.get("managebac_base_url")
            if mb_url:
                self.cfg.managebac_base_url = mb_url if mb_url.startswith("http") else f"https://{mb_url}"
            if payload.get("managebac_email"):
                self.cfg.managebac_email = payload["managebac_email"]
            if payload.get("edupage_username"):
                self.cfg.edupage_username = payload["edupage_username"]
            if payload.get("edupage_subdomain"):
                self.cfg.edupage_subdomain = payload["edupage_subdomain"]
            if payload.get("mail_email"):
                self.cfg.mail_email = payload["mail_email"].strip()
            if payload.get("mail_imap_host"):
                self.cfg.mail_imap_host = payload["mail_imap_host"]
            if payload.get("mail_smtp_host"):
                self.cfg.mail_smtp_host = payload["mail_smtp_host"]
            if payload.get("managebac_password"):
                self.svc.courses.ensure_login(payload["managebac_password"])
            if payload.get("edupage_password"):
                self.svc.edupage.login(
                    self.cfg.edupage_username, payload["edupage_password"],
                    self.cfg.edupage_subdomain,
                )
            if payload.get("mail_password"):
                # 网页登录密码存入 keyring（作为记录）
                if self.cfg.mail_email:
                    secret_set(f"mail:{self.cfg.mail_email}", payload["mail_password"])
            if payload.get("mail_authcode") and self.cfg.mail_email:
                # 客户端授权码用于 IMAP/SMTP 登录(set_authcode 会去空白)
                self.svc.mail.set_authcode(self.cfg.mail_email, payload["mail_authcode"])
                self.svc.mail.configure(
                    self.cfg.mail_email, self.cfg.mail_imap_host, self.cfg.mail_smtp_host,
                )
            elif payload.get("mail_password") and self.cfg.mail_email:
                # 仅修改密码时，如果没有授权码，尝试用密码重新配置
                self.svc.mail.configure(
                    self.cfg.mail_email, self.cfg.mail_imap_host, self.cfg.mail_smtp_host,
                )
            if "send_grades_to_llm" in payload:
                self.cfg.send_grades_to_llm = bool(payload["send_grades_to_llm"])
            self._save_cfg()
            _snap_drop("home")
            return {"saved": True}
        return _wrap(job)

    def connection_test(self) -> dict:
        def job():
            result = {}
            try:
                self.svc.courses.classes()
                result["managebac"] = "✓"
            except Exception as exc:  # noqa: BLE001
                result["managebac"] = f"✗ {exc}"
            try:
                self.svc.edupage.rooms()
                result["edupage"] = "✓"
            except Exception as exc:  # noqa: BLE001
                result["edupage"] = f"✗ {exc}"
            try:
                self.svc.mail.unread_count()
                result["mail"] = "✓"
            except Exception as exc:  # noqa: BLE001
                result["mail"] = f"✗ {exc}"
            return result
        return _wrap(job)
