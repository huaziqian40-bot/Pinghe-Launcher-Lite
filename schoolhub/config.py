"""本地配置:平台账号 + Agent 供应商预设.

设计要求(用户约定):
- 用户自选协议(openai 兼容 / anthropic 兼容)、自填 base_url 与 api_key
- 内置知名预设: DeepSeek / Kimi(Moonshot) / GLM(智谱) / 通义千问(阿里) / Ollama(本地小模型) / custom
- 所有配置存本地 ~/.schoolhub/config.json,密钥永不外传
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".schoolhub"
CONFIG_PATH = CONFIG_DIR / "config.json"


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
    managebac_base_url: str = ""          # 如 https://shph.managebac.cn
    managebac_email: str = ""
    edupage_username: str = ""            # Edupage 子域名登录时自动解析,无需手填
    edupage_subdomain: str = ""           # 学校 Edupage 子域名(login_auto 可自动识别)

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
