"""本地配置:平台账号 + Agent 供应商预设.

设计要求(用户约定):
- 用户自选协议(openai 兼容 / anthropic 兼容)、自填 base_url 与 api_key
- 内置知名预设: DeepSeek / Kimi(Moonshot) / GLM(智谱) / 通义千问(阿里) / Ollama(本地小模型) / custom
- 所有配置存数据目录(便携安装时在安装文件夹内),密钥永不外传
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from . import paths, secrets

# 数据目录(便携安装 → <安装目录>\data; 普通运行 → ~/.hellopinghe)
CONFIG_DIR = paths.data_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"

# 旧版产品(SchoolHub)的钥匙串服务名; 仅用于一次性密钥迁移。
_LEGACY_SERVICE = "schoolhub"


def _migrate_legacy() -> None:
    """旧数据一次性迁移(幂等, 静默失败不阻塞启动).

    - 便携模式: 把 ~/.hellopinghe(及更旧的 ~/.schoolhub)整个目录
      (config.json / SQLite / 缓存 / Agent 会话 / 通讯录 / 会话 cookie)
      搬进安装目录的 data 文件夹
    - 旧钥匙串条目(schoolhub / hellopinghe 服务) → 新密钥存储(secrets.json)
      (密码本身不经过聊天/日志, 直接搬运)
    """
    import shutil

    for legacy in paths.legacy_candidates():
        if not legacy.exists():
            continue
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            for item in legacy.iterdir():
                dest = CONFIG_DIR / item.name
                if item.name == _KEYFILE:      # 新密钥存储不被旧目录覆盖
                    continue
                if item.is_dir():
                    if not dest.exists():
                        shutil.copytree(item, dest)
                elif not dest.exists():
                    shutil.copy2(item, dest)
        except Exception:  # noqa: BLE001
            pass

    # 旧 keyring 条目 → 新密钥存储(按迁移后的 config 推导旧密钥名)
    # 只在 Windows 上执行(凭据管理器不弹窗); macOS Keychain 每次读取都会
    # 弹安全确认, 且 macOS 从未有 keyring 旧数据, 直接跳过。
    # 全平台只尝试一次(标记文件), 避免每次启动都碰 keyring。
    marker = CONFIG_DIR / ".keyring_migrated"
    if sys.platform == "win32" and not marker.exists():
        try:
            marker.write_text("", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        try:
            import keyring

            cfg: dict = {}
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
            pairs = []
            if cfg.get("managebac_base_url"):
                pairs.append(f"managebac:{cfg['managebac_base_url']}")
            if cfg.get("mail_email"):
                pairs += [f"mail:{cfg['mail_email']}", f"mail_authcode:{cfg['mail_email']}"]
            if cfg.get("edupage_username") and cfg.get("edupage_subdomain"):
                pairs.append(f"edupage:{cfg['edupage_subdomain']}:{cfg['edupage_username']}")
            for service in (_LEGACY_SERVICE, "hellopinghe"):
                for key in pairs:
                    try:
                        val = keyring.get_password(service, key)
                        if val and not secrets.get(key):
                            secrets.set(key, val)
                    except Exception:  # noqa: BLE001
                        continue
        except Exception:  # noqa: BLE001
            pass


_migrate_legacy()


@dataclass
class AgentProvider:
    """一个 LLM 接入点的完整描述."""

    name: str                      # 展示名,如 "DeepSeek"
    protocol: str = "openai"       # "openai" | "anthropic"(两种兼容协议)
    base_url: str = ""
    api_key: str = ""              # 用户自填;ollama 默认填占位符
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
    # --- 平台账号(密码绝不写入配置文件,只在登录瞬间使用) ---
    # 默认值 = 平和学校的端点: 即使向导被跳过, 连接测试也是"缺账号"
    # 而不是"Invalid URL / No scheme"
    managebac_base_url: str = "https://shph.managebac.cn"
    managebac_email: str = ""
    edupage_username: str = ""            # Edupage 子域名登录时自动解析,无需手填
    edupage_subdomain: str = "pingheschool"  # 学校 Edupage 子域名(login_auto 可自动识别)

    # --- 选课(向导第 3 步的结果: [{"subject":..,"teacher":..}]) ---
    selected_lessons: list = field(default_factory=list)
    wizard_done: bool = False

    # --- 邮件(网易企业邮默认) ---
    mail_email: str = ""
    mail_imap_host: str = "imap.qiye.163.com"
    mail_smtp_host: str = "smtp.qiye.163.com"

    # --- AI 供应商(多提供商: 各自 key/base_url/协议/模型目录) ---
    ai_providers: list = field(default_factory=list)
    # 元素: {"id","name","protocol","base_url","api_key","models":[...],"notes"}
    agent_provider_id: str = ""           # 当前激活的提供商
    agent_model: str = ""                 # 当前激活的模型

    # --- Agent 工作区 ---
    agent_workspace: str = ""             # agent 的读写根目录
    agent_workspaces: list = field(default_factory=list)   # 历史工作区列表
    course_class_order: list = field(default_factory=list)  # 我的课程课程排序
    task_order: list = field(default_factory=list)  # 我的课程作业条目排序(title|due_at 键)
    agent_mode: str = "confirm"  # agent 权限: readonly/confirm/workspace_write/full_access

    # --- 行为开关 ---
    send_grades_to_llm: bool = False      # 隐私: 默认不把成绩发给 LLM
    ddl_notify_days: int = 3              # DDL 提前提醒天数

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        raw: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        old_agent = raw.pop("agent", {})
        valid = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in raw.items() if k in valid})
        # 旧配置自愈: base_url 为空(老版本默认值/向导被跳过)时补学校端点,
        # 否则所有连接测试都是 "Invalid URL / No scheme supplied"
        if not cfg.managebac_base_url:
            cfg.managebac_base_url = "https://shph.managebac.cn"
        if not cfg.edupage_subdomain:
            cfg.edupage_subdomain = "pingheschool"
        # 旧配置迁移: 单一 agent → 多提供商列表
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
        return cfg

    def active_provider(self) -> dict:
        for p in self.ai_providers:
            if p.get("id") == self.agent_provider_id:
                return p
        return self.ai_providers[0] if self.ai_providers else {}
