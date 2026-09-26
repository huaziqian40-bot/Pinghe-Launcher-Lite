"""本地配置: 平台账号 + 选课 + AI 供应商预设(全部存 ``data/settings.yaml``).

- 四平台**凭据**(密码/授权码/token)也在同一个文件的 ``accounts`` 段里,
  由 :mod:`hellopinghe.secrets` 读写, 不经过这里
- 旧版 ``data/config.json`` 会自动迁移进 settings.yaml, 旧文件由
  :mod:`hellopinghe.filestore` 挪进 ``data/_migrated_backup/``(保留不删)
- 保存时**只更新本文档负责的字段**, 绝不覆盖 ``accounts``/``secrets_extra``,
  否则一次保存就会把密码写没

字段与 YAML 的对应关系见仓库根 ``DATA-FORMAT.md``。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import filestore as fs
from . import paths, secrets

#: 兼容旧引用(旧版是 data/config.json, 现在是 settings.yaml)
CONFIG_DIR = paths.data_dir()
CONFIG_PATH = CONFIG_DIR / "settings.yaml"

#: 旧版产品(SchoolHub)的遗留目录, 仅用于一次性迁移
_LEGACY_DIRS = ("hellopinghe", "schoolhub")


@dataclass
class AgentProvider:
    """一个 LLM 接入点的完整描述."""

    name: str                      # 展示名,如 "DeepSeek"
    protocol: str = "openai"       # "openai" | "anthropic"(两种兼容协议)
    base_url: str = ""
    api_key: str = ""              # 用户自填; ollama 默认填占位符
    model: str = ""
    notes: str = ""                # 备注:模型名以各家文档为准,可自行修改


def _ai_content(ai: dict) -> str:
    """ai 段里"算配置"的那部分(排除同步元信息), 用来判断配置到底动没动。"""
    ai = ai if isinstance(ai, dict) else {}
    return json.dumps({
        "providers": ai.get("providers") or [],
        "active_provider_id": ai.get("active_provider_id") or "",
        "active_model": ai.get("active_model") or "",
    }, ensure_ascii=False, sort_keys=True)


def _stamp_ai(ai: dict, previous: dict | None) -> dict:
    """给 ai 段盖 ``updated_at`` / ``updated_by``。

    三端(网页端 / PLL / PHL)写同一个同步对象 ``settings.ai``, 合并时要靠"谁什么时候
    改的"判断该听谁的 —— 所以**真正改动配置的那一次**必须记下来。配置内容没变就
    **不刷新时间戳**: 否则每存一次设置都算"刚改过", 三端会互相追着改时间戳, 永远收敛
    不了(PLL 的同步引擎也会每轮都以为本地变了而空推一份)。

    ``updated_by`` 只在本地真的改了配置时写 ``pll``; 从云端拉下来的配置自带
    ``updated_by``(例如 ``web``/``phl``), 原样保留。
    """
    out = dict(ai or {})
    prev = previous if isinstance(previous, dict) else {}
    if _ai_content(out) != _ai_content(prev) or not out.get("updated_at"):
        out["updated_at"] = fs.now_iso()
        out["updated_by"] = "pll"
    return out


#: 内置供应商预设。model 只是合理默认值,全部可改。
PROVIDER_PRESETS: dict[str, AgentProvider] = {
    "deepseek": AgentProvider(
        name="DeepSeek",
        protocol="openai",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        notes="DeepSeek 官方 API,OpenAI 兼容。key: platform.deepseek.com",
    ),
    "kimi": AgentProvider(
        name="Kimi (Moonshot)",
        protocol="openai",
        base_url="https://api.moonshot.cn/v1",
        model="kimi-k2-turbo-preview",
        notes="Kimi K2 系列;亦提供 Anthropic 兼容端点(api.moonshot.ai/anthropic)。",
    ),
    "glm": AgentProvider(
        name="GLM (智谱)",
        protocol="openai",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4-plus",
        notes="模型名可换成 glm-4.6 / glm-4-flash 等当前在售型号。",
    ),
    "qwen": AgentProvider(
        name="通义千问 (阿里)",
        protocol="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        notes="DashScope OpenAI 兼容模式。",
    ),
    "ollama": AgentProvider(
        name="Ollama 本地模型",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
        model="qwen2.5:7b",
        notes="完全本地、零 API 费用;模型按你机器显存选择(7b/14b/...)。",
    ),
    "custom": AgentProvider(
        name="自定义",
        protocol="openai",
        notes="自填 base_url / model / key;协议可选 anthropic。",
    ),
}


@dataclass
class Config:
    # --- 平台账号(密码在 settings.yaml 的 accounts 段, 由 secrets 管) ---
    managebac_base_url: str = "https://shph.managebac.cn"
    managebac_email: str = ""
    edupage_username: str = ""
    edupage_subdomain: str = "pingheschool"

    # --- 选课(向导第 3 步的结果: [{"subject":..,"teacher":..,"group":..}]) ---
    selected_lessons: list = field(default_factory=list)
    wizard_done: bool = False

    # --- 邮件(网易企业邮默认) ---
    mail_email: str = ""
    mail_imap_host: str = "imap.qiye.163.com"
    mail_smtp_host: str = "smtp.qiye.163.com"

    # --- 心履(xin-lv.com 心情记录) ---
    xinlv_username: str = ""

    # --- AI 供应商(多提供商: 各自 key/base_url/协议/模型目录) ---
    ai_providers: list = field(default_factory=list)
    agent_provider_id: str = ""
    agent_model: str = ""

    # --- Agent 工作区与偏好 ---
    agent_workspace: str = ""
    agent_workspaces: list = field(default_factory=list)
    course_class_order: list = field(default_factory=list)
    task_order: list = field(default_factory=list)
    agent_mode: str = "confirm"   # readonly/confirm/workspace_write/full_access

    # --- 行为开关 ---
    send_grades_to_llm: bool = False
    ddl_notify_days: int = 3

    # --- 应用内更新 ---
    #: 用户在更新卡片上点过「跳过本版本」的那个版本号：该版本不再提示，
    #: 更高的新版本仍会照常弹卡片。（存独立的 update 段，不动与三端共享的同步对象）
    skipped_update_version: str = ""

    # ------------------------------------------------------------ 存取
    def to_doc(self, doc: dict | None = None) -> dict:
        """把配置写进 settings.yaml 文档(保留 accounts / secrets_extra / 未知段)."""
        doc = dict(doc or {})
        doc["version"] = 1
        doc["wizard_done"] = bool(self.wizard_done)
        accounts = doc.setdefault("accounts", {})
        accounts.setdefault("edupage", {})
        accounts["edupage"]["username"] = self.edupage_username
        accounts["edupage"]["subdomain"] = self.edupage_subdomain
        accounts.setdefault("managebac", {})
        accounts["managebac"]["base_url"] = self.managebac_base_url
        accounts["managebac"]["email"] = self.managebac_email
        accounts.setdefault("mail", {})
        accounts["mail"]["email"] = self.mail_email
        accounts["mail"]["imap_host"] = self.mail_imap_host
        accounts["mail"]["smtp_host"] = self.mail_smtp_host
        accounts.setdefault("xinlv", {})
        accounts["xinlv"]["username"] = self.xinlv_username
        doc["lessons"] = [dict(x) for x in (self.selected_lessons or [])]
        # `ai` 段**除了本文档负责的三个字段之外的东西必须留住**(2026-09-13 三端统一):
        # 同步对象 settings.ai 是规范形态, 落回本地时还会带 updated_at/updated_by
        # 这类同步元信息 —— 一次"保存其它设置"就把它抹掉的话, 下一轮同步又会看到
        # "本地变了"而空推一份配置。见 `_ai_raw`。
        ai_doc = dict(getattr(self, "_ai_raw", None) or {})
        ai_doc.update({
            "providers": [dict(p) for p in (self.ai_providers or [])],
            "active_provider_id": self.agent_provider_id,
            "active_model": self.agent_model,
        })
        doc["ai"] = ai_doc
        doc["agent"] = {
            "workspace": self.agent_workspace,
            "workspaces": list(self.agent_workspaces or []),
            "mode": self.agent_mode,
            "send_grades_to_llm": bool(self.send_grades_to_llm),
            "ddl_notify_days": int(self.ddl_notify_days or 3),
        }
        doc["ui"] = {
            "course_order": list(self.course_class_order or []),
            "task_order": list(self.task_order or []),
        }
        # 应用内更新：独立成段，避免混进与 PLL/网页端共享的同步对象
        doc["update"] = {"skipped_version": self.skipped_update_version or ""}
        return doc

    def save(self) -> None:
        def mutate(doc):
            # **保险**: 云同步(PLL 自己的同步引擎, 或同步时被别的端写进来的配置)会
            # 直接改磁盘上的 ai 段, 不会经过内存里这份 Config。若这时内存里
            # ai_providers 还是空的(用户只是改了个工作区), to_doc 会把刚同步下来的
            # 服务商列表清空 —— 那是**用户数据的静默丢失**。
            # 所以落盘前先看一眼文档里现有的 ai 段: 内存里没有服务商时就以文档为准。
            on_disk = doc.get("ai")
            if isinstance(on_disk, dict) and on_disk.get("providers") and not self.ai_providers:
                self.ai_providers = list(on_disk["providers"])
                self.agent_provider_id = on_disk.get("active_provider_id") or ""
                self.agent_model = on_disk.get("active_model") or ""
                self._ai_raw = dict(on_disk)
            out = self.to_doc(doc)
            out["ai"] = _stamp_ai(out.get("ai"), getattr(self, "_ai_raw", None))
            if isinstance(out.get("ai"), dict):
                # 把刚落盘的 ai 段记下来, 供下一次 save() 复用(其它字段不能因为
                # "保存别的设置"而消失)。
                self._ai_raw = dict(out["ai"])
            return out

        fs.update_settings(mutate)

    @classmethod
    def load(cls) -> "Config":
        doc = fs.load_settings()
        if not doc:
            cfg = cls()
            cfg.save()
            return cfg
        cfg = cls.from_doc(doc)
        if not cfg.managebac_base_url:
            cfg.managebac_base_url = "https://shph.managebac.cn"
        if not cfg.edupage_subdomain:
            cfg.edupage_subdomain = "pingheschool"
        return cfg

    @classmethod
    def from_doc(cls, doc: dict) -> "Config":
        accounts = doc.get("accounts") or {}
        ep = accounts.get("edupage") or {}
        mb = accounts.get("managebac") or {}
        ml = accounts.get("mail") or {}
        xl = accounts.get("xinlv") or {}
        ai = doc.get("ai") or {}
        ag = doc.get("agent") or {}
        ui = doc.get("ui") or {}
        cfg = cls(
            managebac_base_url=(mb.get("base_url") or "").strip(),
            managebac_email=(mb.get("email") or "").strip(),
            edupage_username=(ep.get("username") or "").strip(),
            edupage_subdomain=(ep.get("subdomain") or "").strip() or "pingheschool",
            selected_lessons=list(doc.get("lessons") or []),
            wizard_done=bool(doc.get("wizard_done")),
            mail_email=(ml.get("email") or "").strip(),
            mail_imap_host=(ml.get("imap_host") or "imap.qiye.163.com").strip(),
            mail_smtp_host=(ml.get("smtp_host") or "smtp.qiye.163.com").strip(),
            xinlv_username=(xl.get("username") or "").strip(),
            ai_providers=list(ai.get("providers") or []),
            agent_provider_id=ai.get("active_provider_id") or "",
            agent_model=ai.get("active_model") or "",
            agent_workspace=ag.get("workspace") or "",
            agent_workspaces=list(ag.get("workspaces") or []),
            course_class_order=list(ui.get("course_order") or []),
            task_order=list(ui.get("task_order") or []),
            agent_mode=ag.get("mode") or "confirm",
            send_grades_to_llm=bool(ag.get("send_grades_to_llm")),
            ddl_notify_days=int(ag.get("ddl_notify_days") or 3),
            skipped_update_version=str((doc.get("update") or {}).get("skipped_version") or "").strip(),
        )
        # 原样的 ai 段留一份: 同步元信息(updated_at/updated_by)与以后新增的字段
        # 不该因为"保存其它设置"被 to_doc 重新拼装时丢掉。
        cfg._ai_raw = dict(ai) if isinstance(ai, dict) else {}
        return cfg

    def active_provider(self) -> dict:
        for p in self.ai_providers:
            if p.get("id") == self.agent_provider_id:
                return p
        return self.ai_providers[0] if self.ai_providers else {}


# ================================================================ 一次性迁移
def _legacy_dirs() -> list[Path]:
    """旧版数据目录(非便携的 ~/.hellopinghe、更旧的 ~/.schoolhub)."""
    home = Path.home()
    return [home / f".{name}" for name in _LEGACY_DIRS]


def _migrate_legacy() -> None:
    """把旧布局搬到新布局(幂等, 静默失败不阻塞启动).

    顺序很重要:
    1. 更旧的目录(~/.schoolhub 等)先并进当前数据目录
    2. data/config.json → data/settings.yaml
    3. data/secrets.json(加密) → settings.yaml 的 accounts 段
    4. hellopinghe.db / 散落缓存 → Schedule + phll/
    5. 剩下的旧文件整体挪进 data/_migrated_backup/
    """
    import shutil

    # 测试/演示环境(fresh.flag): 与真实数据彻底隔离, 绝不迁移
    if paths.is_fresh():
        return

    root = paths.data_dir()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    # 1. 更旧的数据目录并进来(只补缺, 不覆盖)
    for legacy in _legacy_dirs():
        if not legacy.exists() or legacy.resolve() == root.resolve():
            continue
        try:
            for item in legacy.iterdir():
                dest = root / item.name
                if item.is_dir():
                    if not dest.exists():
                        shutil.copytree(item, dest)
                elif not dest.exists():
                    shutil.copy2(item, dest)
        except Exception:  # noqa: BLE001
            pass

    # 2. config.json → settings.yaml
    try:
        cfg_json = root / "config.json"
        if cfg_json.exists() and not fs.settings_path().exists():
            raw = json.loads(cfg_json.read_text(encoding="utf-8"))
            old_agent = raw.pop("agent", {}) or {}
            valid = {f.name for f in fields(Config)}
            cfg = Config(**{k: v for k, v in raw.items() if k in valid})
            if not cfg.ai_providers and old_agent:
                cfg.ai_providers = [{
                    "id": "p-migrated",
                    "name": old_agent.get("name", "默认"),
                    "protocol": old_agent.get("protocol", "openai"),
                    "base_url": old_agent.get("base_url", ""),
                    "api_key": old_agent.get("api_key", ""),
                    "models": [old_agent["model"]] if old_agent.get("model") else [],
                    "notes": old_agent.get("notes", ""),
                }]
                cfg.agent_provider_id = "p-migrated"
                cfg.agent_model = old_agent.get("model", "")
            cfg.save()
    except Exception:  # noqa: BLE001
        pass

    # 3. 旧凭据 → settings.yaml(必须在旧文件被挪走之前)
    try:
        secrets._migrate_once()
    except Exception:  # noqa: BLE001
        pass

    # 4. SQLite 与散落缓存 → Schedule + phll/
    try:
        from . import storage

        storage.migrate_legacy()
    except Exception:  # noqa: BLE001
        pass

    # 5. 旧布局剩下的文件(含 config.json/secrets.json/旧课表缓存)整体挪进
    #    data/_migrated_backup/ —— 保留不删, 用户数据一个不丢
    try:
        fs.move_all_legacy()
    except Exception:  # noqa: BLE001
        pass


_migrate_legacy()
