"""Agent 引擎: 参考 DeepSeek Harness 的最小工具循环.

- 只读工具立即执行; 所有写操作(文件/日程/邮件/交作业)一律生成"提案",
  由用户在界面上确认后才真正执行(提案 10 分钟过期) —— 与 harness 的权限精神一致.
- 供应商: openai 兼容协议(DeepSeek/Kimi/GLM/通义/Ollama/自定义) + anthropic 协议.
- 文件读写严格限制在用户选择的 workspace 内.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import Config
from ..exceptions import PingheError

MAX_ROUNDS = 8
PROPOSAL_TTL = 600  # 10 分钟
#: 单次模型调用的超时（秒）。**必须有**：`agent_chat` 是同步 RPC，
#: 模型或网络卡住时界面会一直停在"正在思考…"，用户看到的就是"卡死"。
#: 超时后抛错让用户重试，而不是无限期挂着（流式响应也受它约束）。
AI_CALL_TIMEOUT = 180.0
#: SDK 自带重试会把这个超时乘上次数 —— 只留 1 次，失败得干脆些。
AI_MAX_RETRIES = 1
#: 单次回复的 token 预算。**必须给足**：现在的默认模型（如 deepseek-flash）
#: 是**推理模型**，思考 token 与正文共享这个预算；给太小会出现"思考吃光预算、
#: 正文空、finish_reason=length"——用户看到的就是"AI 没回答"，而 API 却返回 200。
AI_MAX_TOKENS = 8000
#: 推理模型把思考过程放在这些字段里（各家命名不一），都读一下。
_REASONING_FIELDS = ("reasoning_content", "reasoning")
from .. import filestore as _fs


def _sessions_dir():
    """AI 会话目录: ``data/agent/``(每次动态取, 便携/测试环境切换立即生效)."""
    return _fs.agent_dir()

# ---------------------------------------------------------------- 权限模式
# readonly        只读: 写工具全部禁用
# confirm         操作前确认(默认): 写操作全部走提案
# workspace_write 工作区写入: workspace 内写文档自动执行, 对外操作仍走提案
# full_access     完全访问: 所有写操作立即执行(切换时前端双重确认)
AGENT_MODES = ("readonly", "confirm", "workspace_write", "full_access")

#: 写工具的许可级别: workspace = 工作区内的写操作; external = 对外/全局操作
_TOOL_LEVEL = {
    "create_docx": "workspace",
    "append_to_docx": "workspace",
    "add_schedule_event": "external",
    "send_email": "external",
    "submit_managebac_task": "external",
    "reply_discussion": "external",
}


def _mode_of(cfg: Config) -> str:
    return cfg.agent_mode if cfg.agent_mode in AGENT_MODES else "confirm"


def _now_str() -> str:
    now = datetime.now()
    return f"{now.date()} 周{'一二三四五六日'[now.weekday()]} {now.strftime('%H:%M')}"


def _system_prompt(cfg: Config, workspace: str | None) -> str:
    subjects = "、".join(s["subject"] for s in (cfg.selected_lessons or [])) or "(未选课)"
    mode = _mode_of(cfg)
    lines = [
        "你是 Pinghe Launcher Lite! 学习助手, 运行在学生自己的电脑上.",
        f"当前时间: {_now_str()}.",
        f"学生已选科目: {subjects}.",
        f"工作目录(workspace): {workspace or '(未设置)'}",
        f"当前权限模式: {mode}.",
        "规则:",
        "1. 需要课表/DDL/成绩/邮件/日程信息时, 先调用工具查询, 不要编造.",
        "2. 写作业时用 read_docx 查看已有文档, 用 create_docx / append_to_docx 产出草稿.",
    ]
    if mode == "readonly":
        lines.append(
            "3. 当前为只读模式: 一切写工具(create_docx/append_to_docx/"
            "add_schedule_event/send_email/submit_managebac_task/reply_discussion)"
            "都被禁用, 不要尝试调用; 用户需要写操作时应提示他到 Agent 助手页切换权限模式.")
    elif mode == "confirm":
        lines.append(
            "3. 所有写操作(create_docx/append_to_docx/add_schedule_event/send_email/"
            "submit_managebac_task/reply_discussion)都只是提案, 由用户确认后执行, "
            "请在提案前说明你要做什么.")
    elif mode == "workspace_write":
        lines.append(
            "3. 当前为工作区写入模式: create_docx/append_to_docx 会直接执行不必确认;"
            " add_schedule_event/send_email/submit_managebac_task/reply_discussion 仍走提案, "
            "请在提案前说明你要做什么.")
    else:
        lines.append(
            "3. 当前为完全访问模式: 所有写操作都会立即执行, 不再有确认弹窗."
            " 请先向用户说明你要做什么再执行, 谨慎操作.")
    lines.append("4. 回答使用简体中文, 简洁直接.")
    if not cfg.send_grades_to_llm:
        lines.append("5. 用户关闭了成绩共享: get_grades 会返回错误, 不要反复尝试.")
    return "\n".join(lines)


# ---------------------------------------------------------------- 工具定义
def _tool(name: str, desc: str, props: dict, required: list | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


def build_tools() -> list[dict]:
    return [
        _tool("get_timetable", "查询未来 N 天的个人课表(按学生选课生成)",
              {"days": {"type": "integer", "minimum": 1, "maximum": 14}}),
        _tool("get_ddl", "查询未来 N 天的 ManageBac DDL/作业",
              {"days": {"type": "integer", "minimum": 1, "maximum": 60}}),
        _tool("get_class_tasks", "查询单门课或全部课的作业卡(含已截止)",
              {"class_name": {"type": "string", "maxLength": 80}}),
        _tool("get_grades", "查询各科总评成绩(需用户在设置中允许共享成绩)", {}),
        _tool("list_mail", "列出平和邮箱的邮件",
              {"unseen_only": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}),
        _tool("read_mail", "读取一封邮件的正文", {"uid": {"type": "string"}}, ["uid"]),
        _tool("get_schedule", "查询本地日程(YYYY-MM-DD 区间)",
              {"day_from": {"type": "string"}, "day_to": {"type": "string"}},
              ["day_from", "day_to"]),
        _tool("list_workspace", "列出 workspace 下的文件",
              {"subdir": {"type": "string", "maxLength": 200}}),
        _tool("read_docx", "读取 workspace 内 Word 文档的文本", {"path": {"type": "string"}}, ["path"]),
        _tool("read_text_file", "读取 workspace 内文本文件(≤8000字符)",
              {"path": {"type": "string"}}, ["path"]),
        _tool("create_docx", "提案: 在 workspace 新建 Word 文档",
              {"path": {"type": "string"}, "title": {"type": "string"},
               "paragraphs": {"type": "array", "items": {"type": "string"}}},
              ["path", "title", "paragraphs"]),
        _tool("append_to_docx", "提案: 向 workspace 内 Word 文档追加段落",
              {"path": {"type": "string"}, "paragraphs": {"type": "array", "items": {"type": "string"}}},
              ["path", "paragraphs"]),
        _tool("add_schedule_event", "提案: 新增本地日程",
              {"day": {"type": "string"}, "time": {"type": "string"},
               "title": {"type": "string"}, "note": {"type": "string"}},
              ["day", "title"]),
        _tool("search_contacts",
              "在邮箱通讯录里按姓名或邮箱片段搜索联系人。"
              "用户提到人名要发邮件/给谁写信时, 先用本工具把名字变成邮箱地址",
              {"query": {"type": "string", "maxLength": 80}}, ["query"]),
        _tool("send_email", "提案: 用平和邮箱发邮件。to 必须是完整邮箱地址, "
              "如果用户只说了名字, 先调用 search_contacts 查到邮箱",
              {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
              ["to", "subject", "body"]),
        _tool("submit_managebac_task", "提案: 把 workspace 里的文件提交到 ManageBac 作业",
              {"class_id": {"type": "string"}, "task_id": {"type": "string"},
               "file_path": {"type": "string"}},
              ["class_id", "task_id", "file_path"]),
        _tool("list_discussions", "列出某门课的 ManageBac 讨论(Discussion)主题列表",
              {"class_name": {"type": "string", "maxLength": 80}}, ["class_name"]),
        _tool("read_discussion", "读取一篇讨论的完整内容(主帖 + 全部评论)",
              {"class_id": {"type": "string"}, "discussion_id": {"type": "string"}},
              ["class_id", "discussion_id"]),
        _tool("reply_discussion", "提案: 以学生身份回复一篇 ManageBac 讨论",
              {"class_id": {"type": "string"}, "discussion_id": {"type": "string"},
               "body": {"type": "string", "maxLength": 4000},
               "private": {"type": "boolean"}},
              ["class_id", "discussion_id", "body"]),
    ]


# ---------------------------------------------------------------- 会话兼容
# PH Launcher 的会话可能带有本程序不支持的高级消息(工具调用/提案/附件引用)。
# 读取时把**真正读不懂**的消息替换成提示行, 保证历史永远可以直接喂给模型。
_UNSUPPORTED_HINT = "（这一条消息使用了 PH Launcher 的高级格式，当前这一条消息格式不支持，请使用 PH Launcher 查看。）"


def _text_of(content) -> str:
    """把各种 content 形态尽量抽成纯文本；抽不出就返回空串。

    认识的形态：字符串本体；内容块数组（取每块的 text / content 文本字段，
    `tool_use` / `tool_result` 这类非文本块跳过）；单个 {text|content} 字典。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str) and t:
                    parts.append(t)
                elif b.get("type") in (None, "text") and isinstance(b.get("content"), str):
                    parts.append(b["content"])
        return "\n".join(parts)
    if isinstance(content, dict):
        for k in ("text", "content"):
            v = content.get(k)
            if isinstance(v, str) and v:
                return v
    return ""


def _is_placeholder(text: str) -> bool:
    """这条内容是不是**我们自己**塞进去的占位提示（不是用户或模型真正说的话）。

    旧版本在「assistant 正文为空」时会写进这条提示，于是**纯 PLL 的会话**也被说成
    "PH Launcher 高级格式不支持"，而且它会被存回会话、越滚越多，模型还会照着它回答
    （2026-09-21 用户实测：两个只聊了一句的会话，assistant 回复就是这串提示）。
    这里把它当成"没有内容"，让被污染的旧会话能自愈。
    """
    return text.strip() == _UNSUPPORTED_HINT


def _compatible_history(history):
    out = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            t = _text_of(msg.get("content"))
            if t.strip() and not _is_placeholder(t):
                out.append({"role": "system", "content": t})
            continue
        if role not in ("user", "assistant"):
            continue  # tool 等过程消息不是对话内容, 整条跳过

        raw = msg.get("content")
        text = _text_of(raw)
        if _is_placeholder(text):
            continue  # 旧版本误写的占位提示 → 丢掉, 不让它继续污染会话
        if text.strip():
            kept: dict = {"role": role, "content": text}
            # 思考过程也留着：重新打开会话时能一并回放（默认折叠，见前端）
            think = _text_of(msg.get("reasoning"))
            if think.strip():
                kept["reasoning"] = think
            out.append(kept)
            continue
        if isinstance(raw, str) or raw is None:
            # 正文为空：只是"这一轮没话说"（被中断、只调工具、模型没吐字），
            # **不是**格式不支持 —— 静默跳过，绝不能吓唬用户
            continue
        if msg.get("tool_calls"):
            continue  # 工具调用轮本来就没有正文
        # content 是个我们抽不出文本的结构 → 这才算真的读不懂
        out.append({"role": role, "content": _UNSUPPORTED_HINT})
    return out


#: 发给 provider 的字段白名单。历史里还带 `reasoning` 这类本程序自己的键，
#: 直接塞过去可能被严格校验的 provider 拒掉，发之前统一过滤。
_API_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")


def _for_api(messages, pass_reasoning: bool = False):
    """把内部历史翻成 **OpenAI 线格式**再发出去。

    内部历史里 tool_calls 是给自己用的简写 ``{id, name, arguments}``；
    OpenAI 规范要的是 ``{id, type:"function", function:{name, arguments}}``。
    不翻译就直接发，第二轮请求会被 422 拒掉：

        messages[2]: missing field `type`

    后果是**只要 AI 调用了工具就永远走不到第二轮** —— 用户看到的现象是
    "工具跑完了、然后就一直转圈没反应"（2026-09-22 实测复现）。

    `pass_reasoning=True` 时，工具轮的 assistant 消息会带上 ``reasoning_content``：
    思考模式的 provider（DeepSeek 等）**强制要求回传**，否则同样是 400：

        The `reasoning_content` in the thinking mode must be passed back to the API.

    实测该字段**必须是字符串** —— ``""`` 可以，``null`` 与"字段缺失"都会被拒，
    所以这里在拿不到思考时补空串，而不是省略字段。
    """
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                calls.append({
                    "id": tc.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": tc.get("name") or "",
                        "arguments": tc.get("arguments") or "{}",
                    },
                })
            msg = {"role": "assistant",
                   "content": m.get("content") or "",
                   "tool_calls": calls}
            if pass_reasoning:
                think = m.get("reasoning")
                msg["reasoning_content"] = think if isinstance(think, str) else ""
            out.append(msg)
            continue
        if role == "tool":
            # tool 消息只需要 role / tool_call_id / content；内部那层 `name`
            # 不是规范字段，一并去掉
            out.append({"role": "tool",
                        "tool_call_id": m.get("tool_call_id") or "",
                        "content": m.get("content") or ""})
            continue
        out.append({k: v for k, v in m.items() if k in _API_KEYS})
    return out


def _needs_reasoning_retry(exc: Exception) -> bool:
    """这个报错是不是"必须把思考传回来"引起的？

    只在第一次工具轮会被撞到；撞到就补上重发一次，并把结论记住，
    后续不再多花一次请求。这样对不需要该字段的 provider 零影响。
    """
    return "reasoning_content" in str(exc)


class AgentEngine:
    def __init__(self, cfg: Config, services):
        self.cfg = cfg
        self.svc = services
        self.tools = build_tools()
        self.history: list[dict] = []
        self.proposals: dict[str, dict] = {}
        self._pid = 0
        #: 这个 provider 是否要求把思考内容回传给 API（DeepSeek 思考模式要求）。
        #: None=还不知道（首次工具轮撞到 400 就自动开启），True/False=已确定。
        self._reasoning_passthrough: bool | None = None
        self.on_event = None          # 流式回调: bridge 注入, 把增量推给前端
        self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S")

    # ------------------------------------------------------------ 会话持久化
    def save_session(self) -> str | None:
        if not self.history:
            return None
        try:
            _sessions_dir().mkdir(parents=True, exist_ok=True)
            path = _sessions_dir() / f"{self.session_id}.json"
            title = next(
                (m["content"][:30] for m in self.history if m["role"] == "user"),
                "会话",
            )
            # 读-改-写: 这个文件可能是 PH Launcher 写的(带 version/kind/app/updated_at
            # 等本程序不认识的字段), 未知字段一律原样保留, 只更新自己负责的部分。
            doc: dict = {}
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    doc = existing
            except Exception:  # noqa: BLE001  (缺失/损坏都当新文件)
                doc = {}
            doc.update({
                "id": self.session_id,
                "title": title,
                "history": self.history,
                "app": "Pinghe Launcher Lite",
                "updated_at": _fs.now_iso(),
            })
            doc.setdefault("version", 1)
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return self.session_id
        except Exception:  # noqa: BLE001
            return None

    def list_sessions(self) -> list[dict]:
        if not _sessions_dir().exists():
            return []
        out = []
        for p in _sessions_dir().glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "id": data.get("id", p.stem),
                    "title": data.get("title", "会话"),
                    "updated": p.stat().st_mtime,
                })
            except Exception:  # noqa: BLE001
                continue
        out.sort(key=lambda s: s["updated"], reverse=True)
        return out[:30]

    def load_session(self, sid: str) -> dict:
        self.save_session()
        data = json.loads((_sessions_dir() / f"{sid}.json").read_text(encoding="utf-8"))
        self.history = _compatible_history(data.get("history", []))
        self.proposals.clear()
        self.session_id = sid
        return {"session": sid, "title": data.get("title", ""), "history": self.history}

    def new_session(self) -> dict:
        self.save_session()
        self.history = []
        self.proposals.clear()
        self.session_id = self._fresh_session_id()
        # **立刻落盘**：否则新会话在列表里看不到 —— 列表读的是磁盘上的 *.json，
        # 而原来只有"AI 回过一次话"才会写文件。用户看到的现象就是
        # "新建会话不显示，要再新建一个才显示上一个"。
        # 先放系统提示，这样即使一条都没问，文件也在、也能点进去。
        self.history.append({
            "role": "system", "content": _system_prompt(self.cfg, self.cfg.agent_workspace)
        })
        self.save_session()
        return {"session": self.session_id}

    def _fresh_session_id(self) -> str:
        """秒级时间戳做 id；同一秒内再建就加后缀，避免互相覆盖。"""
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        sid, n = base, 1
        while (_sessions_dir() / f"{sid}.json").exists():
            n += 1
            sid = f"{base}-{n}"
        return sid

    def emit(self, obj: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(obj)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ workspace
    def workspace_root(self) -> Path | None:
        if not self.cfg.agent_workspace:
            return None
        return Path(self.cfg.agent_workspace)

    def _remember_workspace(self, path: str) -> None:
        if path and path not in (self.cfg.agent_workspaces or []):
            self.cfg.agent_workspaces = (self.cfg.agent_workspaces or []) + [path]

    def set_workspace(self, path: str) -> dict:
        p = Path(path).expanduser()
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
        self.cfg.agent_workspace = str(p)
        self._remember_workspace(str(p))
        return {"workspace": str(p)}

    def new_workspace(self, name: str) -> dict:
        name = re.sub(r'[\\/:*?"<>|]', "_", name or "workspace").strip() or "workspace"
        base = Path.home() / "Documents" / "Pinghe Launcher Lite"
        p = base / name
        p.mkdir(parents=True, exist_ok=True)
        self.cfg.agent_workspace = str(p)
        self._remember_workspace(str(p))
        return {"workspace": str(p)}

    def list_workspaces(self) -> list[str]:
        return list(self.cfg.agent_workspaces or [])

    def _resolve(self, rel: str) -> Path:
        root = self.workspace_root()
        if root is None:
            raise PingheError("未设置 workspace, 请先在 Agent 页面选择")
        p = (root / rel).resolve() if rel else root
        if root.resolve() not in p.parents and p != root.resolve():
            raise PingheError(f"路径越出 workspace: {rel}")
        return p

    # ------------------------------------------------------------ 权限模式
    @property
    def mode(self) -> str:
        return _mode_of(self.cfg)

    # ------------------------------------------------------------ 提案
    def _propose(self, title: str, detail: str, fn, level: str = "external") -> dict:
        mode = self.mode
        if mode == "full_access" or (mode == "workspace_write" and level == "workspace"):
            # 高权限模式(开启时已经过双重确认): 直接执行, 不再逐次弹确认
            return {"executed": True, "title": title, "result": fn()}
        self._gc_proposals()
        self._pid += 1
        pid = f"p{int(time.time())}-{self._pid}"
        self.proposals[pid] = {
            "id": pid, "title": title, "detail": detail, "fn": fn,
            "created": time.monotonic(),
        }
        return {"proposal": True, "proposal_id": pid, "title": title, "detail": detail}

    def _gc_proposals(self) -> None:
        now = time.monotonic()
        for pid in [k for k, v in self.proposals.items() if now - v["created"] > PROPOSAL_TTL]:
            self.proposals.pop(pid, None)

    def list_proposals(self) -> list[dict]:
        self._gc_proposals()
        return [
            {"id": v["id"], "title": v["title"], "detail": v["detail"]}
            for v in self.proposals.values()
        ]

    def confirm(self, pid: str) -> dict:
        item = self.proposals.pop(pid, None)
        if not item:
            raise PingheError(f"提案 {pid} 不存在或已过期")
        result = item["fn"]()
        return {"ok": True, "result": result, "title": item["title"]}

    def reject(self, pid: str) -> dict:
        self.proposals.pop(pid, None)
        return {"ok": True}

    # ------------------------------------------------------------ 工具执行
    def _exec_tool(self, name: str, args: dict) -> str:
        try:
            # 只读模式: 一切写工具直接拒绝(LLM 会看到错误并向用户解释)
            if name in _TOOL_LEVEL and self.mode == "readonly":
                raise PingheError(
                    f"当前 Agent 为只读模式, 无法执行写操作({name})。"
                    "请告诉用户: 到 Agent 助手页把权限模式切换为"
                    "「操作前确认/工作区写入/完全访问」后再试。")
            return json.dumps({"ok": True, **self._dispatch(name, args)}, ensure_ascii=False)
        except PingheError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    def _dispatch(self, name: str, args: dict):
        if name == "get_timetable":
            days = int(args.get("days") or 1)
            out = []
            for offset in range(days):
                day = date.today() + timedelta(days=offset)
                out.append({"day": str(day), "lessons": self.svc.edupage.personal(day)})
            return {"days": out}

        if name == "get_ddl":
            days = int(args.get("days") or 14)
            return {"deadlines": self.svc.courses.deadlines(days=days)}

        if name == "get_class_tasks":
            wanted = (args.get("class_name") or "").strip().lower()
            tasks = self.svc.courses.all_tasks()
            if wanted:
                tasks = [t for t in tasks if wanted in (t["class_name"] or "").lower()]
            return {"count": len(tasks), "tasks": tasks[:60]}

        if name == "get_grades":
            if not self.cfg.send_grades_to_llm:
                raise PingheError("用户未允许把成绩发给 AI(设置里可开启)")
            return {"grades": self.svc.courses.grades()}

        if name == "list_mail":
            return {"mails": self.svc.mail.list_mail(
                unseen_only=bool(args.get("unseen_only")),
                limit=int(args.get("limit") or 20),
            )}

        if name == "read_mail":
            return self.svc.mail.read(str(args["uid"]))

        if name == "search_contacts":
            return {"contacts": self.svc.mail.contacts_search(
                str(args.get("query") or ""), limit=8)}

        if name == "get_schedule":
            return {"events": self.svc.schedule.list_range(args["day_from"], args["day_to"])}

        if name == "list_workspace":
            root = self.workspace_root()
            if root is None:
                raise PingheError("未设置 workspace")
            base = root / (args.get("subdir") or "")
            if not str(base.resolve()).startswith(str(root.resolve())):
                raise PingheError("路径越出 workspace")
            files = []
            for p in sorted(base.rglob("*")):
                if p.is_file() and ".git" not in p.parts:
                    files.append(str(p.relative_to(root)))
                if len(files) >= 200:
                    break
            return {"workspace": str(root), "files": files}

        if name == "read_docx":
            p = self._resolve(args["path"])
            import docx

            doc = docx.Document(str(p))
            paras = [para.text for para in doc.paragraphs if para.text.strip()]
            return {"path": str(p), "paragraphs": paras[:200]}

        if name == "read_text_file":
            p = self._resolve(args["path"])
            text = p.read_text(encoding="utf-8", errors="replace")
            return {"path": str(p), "text": text[:8000]}

        if name == "create_docx":
            path, title = args["path"], args["title"]
            paras = [str(x) for x in (args.get("paragraphs") or [])]

            def fn():
                p = self._resolve(path)
                if p.suffix.lower() != ".docx":
                    p = p.with_suffix(".docx")
                import docx

                doc = docx.Document()
                doc.add_heading(title, level=1)
                for para in paras:
                    doc.add_paragraph(para)
                doc.save(str(p))
                return f"已创建 {p}"

            return self._propose(f"新建 Word: {path}", f"标题: {title}\n段落数: {len(paras)}", fn,
                                 level="workspace")

        if name == "append_to_docx":
            path = args["path"]
            paras = [str(x) for x in (args.get("paragraphs") or [])]

            def fn():
                p = self._resolve(path)
                import docx

                doc = docx.Document(str(p))
                for para in paras:
                    doc.add_paragraph(para)
                doc.save(str(p))
                return f"已追加 {len(paras)} 段到 {p}"

            return self._propose(f"追加 Word: {path}", f"追加 {len(paras)} 段", fn,
                                 level="workspace")

        if name == "add_schedule_event":
            def fn():
                event_id = self.svc.schedule.add(
                    args["day"], args.get("time") or "", args["title"], args.get("note") or ""
                )
                return f"已添加日程 #{event_id}: {args['title']}"

            return self._propose(
                f"新增日程: {args['title']}",
                f"{args['day']} {args.get('time') or ''} {args.get('note') or ''}", fn
            )

        if name == "send_email":
            def fn():
                self.svc.mail.send(args["to"], args["subject"], args["body"])
                return f"已发送给 {args['to']}: {args['subject']}"

            return self._propose(
                f"发邮件给 {args['to']}", f"主题: {args['subject']}\n---\n{args['body'][:500]}", fn
            )

        if name == "submit_managebac_task":
            def fn():
                p = self._resolve(args["file_path"])
                return self.svc.courses.submit_task(
                    args["class_id"], args["task_id"], str(p)
                )

            return self._propose(
                f"提交作业 {args['task_id']}",
                f"课程 {args['class_id']} ← {args['file_path']}", fn
            )

        if name == "list_discussions":
            wanted = (args.get("class_name") or "").strip().lower()
            classes = self.svc.courses.classes()   # {id: name}
            hits = [(cid, name) for cid, name in classes.items()
                    if not wanted or wanted in (name or "").lower()]
            if not hits:
                return {"count": 0, "note": f"没有匹配到包含 {wanted!r} 的课程"}
            out = []
            for cid, name in hits[:4]:
                try:
                    for d in self.svc.courses.class_discussions(cid):
                        d["class_id"] = cid
                        d["class_name"] = name
                        out.append(d)
                except Exception:  # noqa: BLE001
                    continue
            return {"count": len(out), "discussions": out[:40]}

        if name == "read_discussion":
            return self.svc.courses.discussion_detail(
                str(args["class_id"]), str(args["discussion_id"]))

        if name == "reply_discussion":
            body = (args.get("body") or "").strip()
            if not body:
                raise PingheError("回复内容不能为空")

            def fn():
                self.svc.courses.post_discussion_reply(
                    args["class_id"], args["discussion_id"],
                    body.replace("\n", "<br>"),
                    private=bool(args.get("private")))
                return "回复已发布"

            return self._propose(
                f"回复讨论 {args['discussion_id']}",
                f"---\n{body[:500]}", fn
            )

        raise PingheError(f"未知工具: {name}")

    # ------------------------------------------------------------ LLM 调用
    def _active(self) -> tuple[dict, str]:
        provider = self.cfg.active_provider()
        model = self.cfg.agent_model or (provider.get("models") or [""])[0]
        return provider, model

    def _call_llm(self, messages: list[dict], on_delta=None, on_reasoning=None):
        provider, model = self._active()
        if provider.get("protocol") == "anthropic":
            return self._call_anthropic(provider, model, messages, on_delta, on_reasoning)
        return self._call_openai(provider, model, messages, on_delta, on_reasoning)

    def _call_openai(self, provider: dict, model: str,
                     messages: list[dict], on_delta=None, on_reasoning=None):
        from openai import OpenAI

        client = OpenAI(
            api_key=provider.get("api_key") or "EMPTY",
            base_url=provider.get("base_url") or None,
            timeout=AI_CALL_TIMEOUT,
            max_retries=AI_MAX_RETRIES,
        )
        # 第一轮按常规发；若 provider 是思考模式且要求回传 reasoning_content，
        # 会被 400 拒 —— 这时补上重发一次，并把结论记在本会话上（后续不再多花请求）。
        for attempt in (0, 1):
            pass_reasoning = bool(self._reasoning_passthrough) or attempt == 1
            try:
                return self._stream_openai(client, model, messages,
                                           on_delta, on_reasoning, pass_reasoning)
            except Exception as exc:  # noqa: BLE001
                if attempt == 0 and _needs_reasoning_retry(exc):
                    self._reasoning_passthrough = True
                    self.emit({"type": "info",
                               "text": "该模型要求回传思考内容，已自动适配并重试。"})
                    continue
                raise

    def _stream_openai(self, client, model: str, messages: list[dict],
                       on_delta, on_reasoning, pass_reasoning: bool):
        content_parts: list[str] = []
        think_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        finish_reason = ""

        stream = client.chat.completions.create(
            model=model,
            messages=_for_api(messages, pass_reasoning=pass_reasoning),
            tools=self.tools,
            max_tokens=AI_MAX_TOKENS,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta is None:
                continue
            # 推理模型（deepseek-flash 等）把"思考过程"放在 reasoning_content 里，
            # 与正文分开。以前只取 content，于是**思考完全看不到**。
            for field in _REASONING_FIELDS:
                piece = getattr(delta, field, None)
                if piece:
                    think_parts.append(piece)
                    if on_reasoning:
                        on_reasoning(piece)
                    break
            if delta.content:
                content_parts.append(delta.content)
                if on_delta:
                    on_delta(delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if tc.index is not None else len(tool_calls)
                    slot = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

        calls = [tool_calls[i] for i in sorted(tool_calls)]
        return {"content": "".join(content_parts), "tool_calls": calls,
                "reasoning": "".join(think_parts), "finish_reason": finish_reason}

    def _call_anthropic(self, provider: dict, model: str,
                        messages: list[dict], on_delta=None, on_reasoning=None):
        import anthropic

        base_url = (provider.get("base_url") or "").strip().rstrip("/") or None
        client = anthropic.Anthropic(
            api_key=provider.get("api_key"),
            base_url=base_url,
            timeout=AI_CALL_TIMEOUT,
            max_retries=AI_MAX_RETRIES,
        )
        # 内部历史是 openai 风格, 转成 anthropic 风格
        conv, pending_tool_results = [], []
        for m in messages:
            role, content = m["role"], m.get("content") or ""
            if m["role"] == "assistant" and m.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in m["tool_calls"]:
                    blocks.append({
                        "type": "tool_use", "id": tc["id"], "name": tc["name"],
                        "input": json.loads(tc["arguments"] or "{}"),
                    })
                conv.append({"role": "assistant", "content": blocks})
                continue
            if m["role"] == "tool":
                pending_tool_results.append({
                    "type": "tool_result", "tool_use_id": m["tool_call_id"],
                    "content": str(content),
                })
                continue
            if pending_tool_results:
                conv.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []
            conv.append({"role": role, "content": content or "(空)"})
        if pending_tool_results:
            conv.append({"role": "user", "content": pending_tool_results})

        tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"],
                "input_schema": t["function"]["parameters"],
            }
            for t in self.tools
        ]
        content_parts: list[str] = []
        with client.messages.stream(
            model=model, max_tokens=AI_MAX_TOKENS,
            system=_system_prompt(self.cfg, self.cfg.agent_workspace),
            messages=conv, tools=tools,
        ) as stream:
            for text in stream.text_stream:
                content_parts.append(text)
                if on_delta:
                    on_delta(text)
            final = stream.get_final_message()

        content = "".join(content_parts)
        calls = [
            {
                "id": block.id, "name": block.name,
                "arguments": json.dumps(block.input, ensure_ascii=False),
            }
            for block in final.content if block.type == "tool_use"
        ]
        # Anthropic 的扩展思考是 content 里 type="thinking" 的块（流式的 thinking
        # 增量走 thinking_stream，这里从 final 里取全文即可）
        think_parts = [
            block.thinking for block in final.content
            if getattr(block, "type", "") == "thinking" and getattr(block, "thinking", None)
        ]
        return {"content": content, "tool_calls": calls,
                "reasoning": "".join(think_parts),
                "finish_reason": getattr(final, "stop_reason", "") or ""}

    # ------------------------------------------------------------ 主循环
    def _empty_reply_error(self, resp: dict) -> str:
        """回复为空时给一句**能照着修**的话，而不是默默存下一条空消息。

        为什么要有这个：模型名不对、或推理模型的思考把 max_tokens 吃光时，
        API 会返回 **HTTP 200 + 空 content + finish_reason=length**，不抛异常。
        以前会当成"正常回复"存下来，用户看到的就是"AI 没回答"。
        """
        reason = (resp.get("finish_reason") or "").lower()
        provider, model = self._active()
        if reason == "length":
            return (f"模型把 {AI_MAX_TOKENS} token 的输出预算用完了，没能留下正文"
                    f"（finish_reason=length）。当前模型「{model}」如果是推理模型，"
                    f"思考会很占预算 —— 换个小一点的模型，或把问题问短一点再试。")
        if reason == "content_filter":
            return "模型判定这条请求被内容策略拦截了（finish_reason=content_filter），没有产出正文。"
        return (f"模型「{model}」没有返回任何内容（finish_reason={reason or '未知'}）。"
                f"常见原因：模型名写错、额度用尽、或该 provider 不支持流式。"
                f"可在「设置 → AI」里核对模型名。")

    def chat(self, message: str) -> dict:
        if not self.history:
            self.history.append({
                "role": "system", "content": _system_prompt(self.cfg, self.cfg.agent_workspace)
            })
        self.history.append({"role": "user", "content": message})

        reply = ""
        for _ in range(MAX_ROUNDS):
            try:
                resp = self._call_llm(
                    self.history,
                    on_delta=lambda t: self.emit({"type": "delta", "text": t}),
                    on_reasoning=lambda t: self.emit({"type": "thinking", "text": t}),
                )
            except Exception:
                # 报错也要把用户这句话存下来，否则整个会话凭空消失
                self.save_session()
                raise
            calls = resp["tool_calls"]
            thinking = resp.get("reasoning") or ""
            if not calls:
                reply = resp["content"]
                if not reply.strip():
                    # 空回复：**不要**存成 assistant 消息（那会污染会话、
                    # 也会被历史清洗逻辑当成占位符丢掉），只留用户提问
                    self.save_session()
                    return {"ok": False, "error": self._empty_reply_error(resp), "reply": ""}
                assistant = {"role": "assistant", "content": reply}
                if thinking:
                    assistant["reasoning"] = thinking
                self.history.append(assistant)
                self.save_session()
                return {"ok": True, "reply": reply, "reasoning": thinking}

            assistant = {"role": "assistant", "content": resp["content"],
                         "tool_calls": calls}
            if thinking:
                assistant["reasoning"] = thinking
            self.history.append(assistant)
            for call in calls:
                try:
                    args = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                self.emit({"type": "tool", "name": call["name"]})
                result = self._exec_tool(call["name"], args)
                self.emit({"type": "tool_result", "name": call["name"], "preview": result[:240]})
                try:
                    parsed = json.loads(result)
                    if parsed.get("proposal"):
                        self.emit({"type": "proposal"})
                except json.JSONDecodeError:
                    pass
                self.history.append({
                    "role": "tool", "tool_call_id": call["id"],
                    "name": call["name"], "content": result,
                })

        self.save_session()
        return {"ok": False, "error": f"达到最大工具轮数({MAX_ROUNDS}), 已暂停", "reply": reply}

    def reset(self) -> None:
        self.new_session()


def detect_ai_environment() -> dict:
    """粗略检测硬件, 给出 Ollama 模型或 API 建议. (Windows/macOS/Linux 兼容)"""
    import os
    import platform

    system = platform.system()
    ram_gb = 0.0
    gpu = ""
    if system == "Windows":
        try:
            import ctypes


            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            ram_gb = stat.ullTotalPhys / (1024 ** 3)
        except Exception:  # noqa: BLE001
            pass
    else:
        try:
            # POSIX(macOS/Linux): 页大小 × 物理页数
            page = os.sysconf("SC_PAGE_SIZE")
            phys = os.sysconf("SC_PHYS_PAGES")
            ram_gb = page * phys / (1024 ** 3)
        except Exception:  # noqa: BLE001
            pass
    if system == "Windows":
        try:
            import subprocess

            out = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            candidates = [
                line.strip() for line in out.splitlines()
                if line.strip() and "VideoController" not in line and "Name" not in line
            ]
            # 优先真实显卡, 过滤虚拟显示适配器(OrayIdd/向日葵 等)
            def rank(name: str) -> int:
                upper = name.upper()
                if any(k in upper for k in ("NVIDIA", "GEFORCE", "RADEON", "AMD")):
                    return 0
                if "INTEL" in upper and "IDD" not in upper and "VIRTUAL" not in upper:
                    return 1
                if "IDD" in upper or "VIRTUAL" in upper or "MIRROR" in upper:
                    return 9
                return 5
            if candidates:
                gpu = sorted(candidates, key=rank)[0]
        except Exception:  # noqa: BLE001
            pass
    elif system == "Darwin":
        try:
            import subprocess

            out = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            m = re.search(r"Chipset Model:\s*(.+)", out)
            gpu = m.group(1).strip() if m else ""
        except Exception:  # noqa: BLE001
            pass

    has_nvidia = "NVIDIA" in (gpu or "").upper()
    if ram_gb >= 16 or has_nvidia:
        model = "qwen2.5:14b" if (ram_gb >= 24 or has_nvidia) else "qwen2.5:7b"
        advice = "local"
    else:
        model = "qwen2.5:3b"
        advice = "api"
    return {
        "platform": platform.platform(),
        "cpu": os.cpu_count(),
        "ram_gb": round(ram_gb, 1),
        "gpu": gpu or "(未检测到)",
        "advice": advice,          # local = 推荐本地模型, api = 建议 API
        "recommended_model": model,
    }
