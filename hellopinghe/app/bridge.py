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
from ..logutil import log as _log, error as _log_error
from .agent import AgentEngine, detect_ai_environment
from .services import Services, secret_set, secret_get

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


def _wrap(fn, *args, **kwargs) -> dict:
    try:
        data = fn(*args, **kwargs)
        if isinstance(data, dict) and "ok" in data:
            return data
        return {"ok": True, "data": data}
    except LoginRequiredError as exc:
        _log.warn(f"LoginRequired: {exc}")
        return {"ok": False, "login_required": str(exc), "error": f"需要重新登录 {exc}"}
    except PingheError as exc:
        _log.error(f"PingheError: {exc}")
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
    def __init__(self):
        self.cfg = Config.load()
        self.svc = Services(self.cfg)
        self.agent = AgentEngine(self.cfg, self.svc)

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
                    now2 = datetime.now()
                    lo = (now2 - timedelta(days=14)).isoformat(timespec="minutes")
                    hi = (now2 + timedelta(days=14)).isoformat(timespec="minutes")
                    ddl = [
                        it for it in self.svc.courses.deadlines(days=14)
                        if it["due_at"] and lo <= it["due_at"] <= hi
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
                        delta = abs(
                            (datetime.fromisoformat(it["due_at"]) - now2).total_seconds()
                        )
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
                    "subject": l.subject.name if l.subject else "?",
                    "teacher": l.teachers[0].name if l.teachers else "-",
                    "room": l.classrooms[0].name if l.classrooms else "",
                    "groups": ",".join(l.groups) if l.groups else "",
                    "classes": [getattr(c, "name", "") for c in (l.classes or [])],
                    "cancelled": bool(l.is_cancelled),
                })
            ordered = [{"time": t, "lessons": slots[t]} for t in sorted(slots)]
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
            upcoming = [t for t in tasks if not t["past_due"]]
            # 过滤掉用户左滑移除过的 DDL (与首页同一套 dismissed key)
            host = self.cfg.managebac_base_url.split("//")[-1]
            dismissed = storage.ddl_dismissed_keys(self.svc._conn(), host)
            upcoming = [
                t for t in upcoming
                if f'{t["title"]}|{t["due_at"] or ""}' not in dismissed
            ]
            # ① 用户用箭头/手动排过序的作业按保存的顺序排前面
            # ② 其余(含新增的)按截止时间排后面
            rank = {k: i for i, k in enumerate(self.cfg.task_order)}
            upcoming.sort(key=lambda t: (
                rank.get(f'{t["title"]}|{t["due_at"] or ""}', len(rank)),
                t["due_at"] or "",
            ))
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
                "tasks_upcoming": upcoming[:40],
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
            return {"cancelled": True}
        path = str(picked[0])

        def job():
            msg = self.svc.courses.submit_task(class_id, task_id, path)
            # 详情/列表缓存作废, 下次打开看到最新提交状态
            _snap_drop(f"tdetail|{class_id}|{task_id}")
            _snap_drop("courses", "home")
            return {"submitted": True, "message": msg, "path": path}
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
            out = {"mails": self.svc.mail.list_mail(unseen_only, limit)}
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

    def mail_send(self, to: str, subject: str, body: str) -> dict:
        def job():
            self.svc.mail.send(to.strip(), subject, body)
            self.svc.mail._unread_cache = None
            _snap_drop("home")
            return {"sent": True}
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
            return {"cancelled": True}

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
