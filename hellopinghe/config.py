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
        doc["ai"] = {
            "providers": [dict(p) for p in (self.ai_providers or [])],
            "active_provider_id": self.agent_provider_id,
            "active_model": self.agent_model,
        }
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
        return doc

    def save(self) -> None:
        def mutate(doc):
            return self.to_doc(doc)

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
        return cls(
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
        )

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
