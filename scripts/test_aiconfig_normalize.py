# -*- coding: utf-8 -*-
"""AI 服务商配置归一化自检(不需要网络、不需要真实 API Key)。

    python scripts/test_aiconfig_normalize.py

覆盖:
1. 四种历史形态的**读**: ①网页端规范形态 ②PLL 的 `{"ai":{...}}` 包装形态
   ③PHL 扁平单服务商 ④更早的 `{base_url,model,api_key}`;
2. 归一化 + 序列化: 读老形态 → 写规范形态(带 updated_at / updated_by);
3. 三方合并: 按服务商**名称**对齐、远端新增收下、两边都改取较新、删除传播;
4. 落回本地 (settings.yaml): 本地专有字段(id / models 列表 / notes / active_*)保留;
5. 走 **SyncEngine** 的 collect / merge / apply 三件套(在临时目录里, 不碰真实数据)。

**全部用假 key**(`sk-fake-*`), 不涉及任何真实凭据。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hellopinghe import aiconfig  # noqa: E402
from hellopinghe import config as ConfigModule  # noqa: E402
from hellopinghe import filestore as fs  # noqa: E402
from hellopinghe.cloudsync import SyncEngine  # noqa: E402

Config = ConfigModule.Config


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def names(payload) -> list[str]:
    return [p["name"] for p in aiconfig.providers_of(payload)]


WEB = {   # ① 网页端的规范形态
    "providers": [
        {"name": "DeepSeek", "protocol": "openai",
         "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
         "api_key": "sk-fake-web-1"},
        {"name": "备用", "protocol": "anthropic",
         "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2",
         "api_key": "sk-fake-web-2"},
    ],
    "default_index": 1,
}

PLL_WRAPPED = {   # ② PLL 现在同步出去的包装形态(多包了一层 ai)
    "ai": {
        "providers": [
            {"id": "p-1", "name": "DeepSeek", "protocol": "openai",
             "base_url": "https://api.deepseek.com", "api_key": "sk-fake-pll",
             "models": ["deepseek-chat", "deepseek-reasoner"], "notes": "n"},
        ],
        "active_provider_id": "p-1",
        "active_model": "deepseek-reasoner",
    }
}

PHL_FLAT = {   # ③ PHL 旧扁平单服务商形态
    "enabled": True, "provider": "api",
    "apiEndpoint": "https://api.example.test/v1", "apiModel": "gpt-fixture",
    "apiKey": "sk-fake-phl", "localEndpoint": "http://127.0.0.1:11434",
    "localModel": "qwen-fixture", "workspace": "/tmp/ws", "workspaces": ["/tmp/ws"],
    "launcherControlEnabled": False, "permissionMode": "chat",
}

ANCIENT = {   # ④ 更早的单个服务商形态
    "base_url": "https://old.example.test/v1", "model": "old-model",
    "api_key": "sk-fake-old",
}


def main() -> int:  # noqa: C901
    # ---------------------------------------------------------------- 1. 读取
    web = aiconfig.normalize(WEB)
    check(names(WEB) == ["DeepSeek", "备用"], "① 规范形态读得出两条")
    check(web["default_index"] == 1, "① default_index 保住了")

    wrapped = aiconfig.normalize(PLL_WRAPPED)
    check(names(PLL_WRAPPED) == ["DeepSeek"], "② PLL 包装形态要能拆开")
    check(wrapped["providers"][0]["model"] == "deepseek-chat", "② models 列表取第一个")
    check(wrapped["providers"][0]["base_url"] == "https://api.deepseek.com", "② base_url 保住了")

    flat = aiconfig.normalize(PHL_FLAT)
    check(names(PHL_FLAT) == ["来自客户端"], "③ PHL 扁平形态归一成一条")
    check(flat["providers"][0]["model"] == "gpt-fixture", "③ apiModel → model")
    check(flat["providers"][0]["base_url"] == "https://api.example.test/v1", "③ apiEndpoint → base_url")

    ancient = aiconfig.normalize(ANCIENT)
    check(names(ANCIENT) == [""], "④ 更早的形态也给得出一条")
    check(ancient["providers"][0]["model"] == "old-model", "④ model 保住了")

    check(aiconfig.providers_of({}) == [], "空对象 → 没有服务商")
    check(aiconfig.providers_of(None) == [], "None → 没有服务商")
    check(aiconfig.providers_of({"providers": "bad"}) == [], "坏 providers 不抛错")
    check(names({"ai": WEB}) == ["DeepSeek", "备用"], "包装里是规范形态也要读得出")

    # 2. 归一化 → 序列化(读老形态, 写规范形态)
    payload = aiconfig.serialize(wrapped, writer="pll")
    check(set(["providers", "default_index", "updated_at", "updated_by"]) <= set(payload),
          "写出的一定是规范形态(带 updated_at/updated_by)")
    check(payload["updated_by"] == "pll", "writer 记成 pll")
    check("id" not in payload["providers"][0], "规范形态里没有 PLL 专有的 id")
    check("models" not in payload["providers"][0], "规范形态里没有 models 列表")

    # PLL 本地有多个模型时, **本机在用的那个**才算 model
    local = dict(PLL_WRAPPED["ai"])
    local["updated_at"] = "2026-01-01T00:00:00+08:00"
    local["updated_by"] = "web"
    roundtrip = aiconfig.serialize(aiconfig.normalize_local(local), writer="pll")
    check(roundtrip["providers"][0]["model"] == "deepseek-reasoner",
          "本机选中的模型(active_model)要带上去, 不能用列表第一个")
    check(roundtrip["updated_by"] == "web", "本地读出来的不抢改 updated_by")
    check(roundtrip["updated_at"] == "2026-01-01T00:00:00+08:00", "updated_at 原样沿用")

    # 3. 三方合并
    base_payload = aiconfig.serialize(aiconfig.normalize(WEB), writer="web")
    remote_payload = json.loads(json.dumps(base_payload))
    remote_payload["providers"].append({"name": "新加的", "protocol": "openai",
                                        "base_url": "https://new.example.test/v1",
                                        "model": "new-model", "api_key": "sk-fake-new"})
    merged = aiconfig.merge_payload(base_payload, base_payload, remote_payload)
    check(names(merged) == ["DeepSeek", "备用", "新加的"], "远端新增的服务商要收下")

    local_changed = json.loads(json.dumps(base_payload))
    local_changed["providers"][0]["model"] = "deepseek-reasoner"
    merged_remote_wins = aiconfig.merge_payload(base_payload, local_changed, remote_payload)
    check(names(merged_remote_wins) == ["DeepSeek", "备用", "新加的"], "远端新增不丢")
    check(merged_remote_wins["providers"][0]["model"] == "deepseek-reasoner",
          "只有本地改过的那条留本地")

    # 两边都改了同一条 → 取 updated_at 较新的一份
    older = {"providers": [{"name": "D", "protocol": "openai",
                            "base_url": "https://api.deepseek.com/v1",
                            "model": "a", "api_key": "sk-fake-a"}],
             "default_index": 0, "updated_at": "2026-01-01T00:00:00+08:00", "updated_by": "web"}
    newer = {"providers": [{"name": "D", "protocol": "openai",
                            "base_url": "https://api.deepseek.com/v1",
                            "model": "b", "api_key": "sk-fake-b"}],
             "default_index": 0, "updated_at": "2026-02-01T00:00:00+08:00", "updated_by": "phl"}
    picked = aiconfig.merge_payload(None, older, newer)
    check(picked["providers"][0]["model"] == "b", "两边都改 → 取较新的那份")

    # 没有基版时: 两边都有且不同 → 保守留本地并报冲突
    conflicts: list[dict] = []
    guarded = aiconfig.merge_payload(None, older, newer, conflicts=conflicts)
    check(guarded["providers"][0]["model"] == "b", "有时间戳时仍按时间取新")
    check(aiconfig.merge_payload({"providers": []}, older, {"providers": []})["providers"][0]["model"] == "a",
          "远端是空的老形态 → 不许清掉本地配置")

    # 4. 落回本地文档: 本地专有字段一个都不能少
    local_doc = {
        "version": 1,
        "ai": {
            "providers": [{
                "id": "p-keep", "name": "DeepSeek", "protocol": "openai",
                "base_url": "https://api.deepseek.com", "api_key": "sk-fake-pll",
                "models": ["deepseek-chat", "deepseek-reasoner"], "notes": "我写的备注",
                "我的私有字段": 42,
            }],
            "active_provider_id": "p-keep",
            "active_model": "deepseek-reasoner",
            "updated_at": "2026-01-01T00:00:00+08:00",
            "updated_by": "web",
        },
        "agent": {"workspace": "/tmp/ws"},
    }
    out = aiconfig.apply_to_local_doc(local_doc, aiconfig.serialize(aiconfig.normalize(WEB), "web"))
    row = out["ai"]["providers"][0]
    check(row["id"] == "p-keep", "本地 id 保留")
    check(row["notes"] == "我写的备注", "本地 notes 保留")
    check(row["我的私有字段"] == 42, "本地未知字段保留")
    check("deepseek-reasoner" in row["models"], "本地 models 列表保留")
    check(row["models"][0] == "deepseek-chat", "规范形态里的 model 前插到列表最前")
    check(out["ai"]["active_model"] == "deepseek-chat", "active_model 跟默认那条走")
    check(out["agent"] == {"workspace": "/tmp/ws"}, "ai 段以外的内容原样不动")
    check(len(out["ai"]["providers"]) == 2, "远端新增的第二个服务商也落到本地")
    check(out["ai"]["providers"][1]["id"].startswith("p-"), "新服务商补一个本地 id")

    # 5. 走真 SyncEngine 的 collect / merge / apply(临时目录, 不碰真实数据)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings = {"version": 1, "ai": {
            "providers": [{"id": "p-1", "name": "DeepSeek", "protocol": "openai",
                           "base_url": "https://api.deepseek.com", "api_key": "sk-fake-pll",
                           "models": ["deepseek-chat"], "notes": "n"}],
            "active_provider_id": "p-1", "active_model": "deepseek-chat",
        }, "agent": {"workspace": "/tmp/ws"}}
        fs.save_yaml(root / fs.SETTINGS, settings)
        engine = SyncEngine(client=None, dek=b"0" * 32, user_id=1, username="tester",
                            data_dir=root)

        collected = engine.collect("settings.ai")
        check(collected["ai"]["providers"][0]["name"] == "DeepSeek", "collect 拿得到服务商")
        check("provider" not in collected["ai"], "collect 不再输出 PHL 扁平字段")
        check("id" not in collected["ai"]["providers"][0], "collect 输出规范形态")

        remote_doc = {"ai": {
            "providers": [
                {"name": "DeepSeek", "protocol": "anthropic",
                 "base_url": "https://api.deepseek.com/anthropic", "model": "deepseek-chat",
                 "api_key": "sk-fake-web"},
                {"name": "网页新增", "protocol": "openai",
                 "base_url": "https://web.example.test/v1", "model": "web-model",
                 "api_key": "sk-fake-web2"},
            ],
            "default_index": 0,
            # 远端时间戳用**未来**的时间: 本地文档里没有任何 updated_at, 只按"读到它的
            # 时刻"当上限, 所以只有确实更晚的远端更新才会被采纳(老配置压不住新配置)。
            "updated_at": "2099-03-01T00:00:00+08:00",
            "updated_by": "web",
        }}
        merged_doc, merged_conflicts = engine.merge("settings.ai", None, collected, remote_doc)
        engine.apply("settings.ai", merged_doc)
        after = fs.load_settings_at(root / fs.SETTINGS)
        check(len(after["ai"]["providers"]) == 2, "同步后本地有两条服务商")
        check(after["ai"]["providers"][0]["id"] == "p-1", "本地 id 仍在")
        check(after["ai"]["providers"][0]["notes"] == "n", "本地 notes 仍在")
        check(after["ai"]["providers"][0]["protocol"] == "anthropic", "协议跟云端走")
        check(after["ai"]["providers"][1]["name"] == "网页新增", "云端新增落到本地")
        check(after["agent"]["workspace"] == "/tmp/ws", "agent 段不许被同步碰到")
        # 第一次同步(还没有基版)是按"本地改过"处理的 → 写出去的时候署名 PLL;
        # 元信息本身没丢(网页端那份的 updated_at 就在本地文档里, 见下一条断言)。
        check(after["ai"]["updated_at"] == "2099-03-01T00:00:00+08:00",
              "远端的时间戳要落到本地, 供下一轮判断谁更新")
        check(after["ai"]["updated_by"] in ("web", "pll"), "写出去的署名必须是已知客户端")

        # 有了基版之后**再同步一轮**: 本地没动 → 不许再盖新时间戳, 内容也要与上一轮
        # 完全一致(收敛); updated_by 仍是"最后写这份载荷的客户端"。
        second_doc, _ = engine.merge("settings.ai", merged_doc, engine.collect("settings.ai"),
                                     remote_doc)
        check(second_doc["ai"]["updated_by"] == "pll",
              "updated_by 记的是最后写这份载荷的客户端")
        check(second_doc["ai"]["updated_at"] == "2099-03-01T00:00:00+08:00",
              "本地没改时不重盖时间戳(否则三端永远收敛不了)")
        check(second_doc["ai"]["updated_at"] == merged_doc["ai"]["updated_at"],
              "时间戳必须稳定")
        check(second_doc == merged_doc, "第二轮同步必须与第一轮完全一致(收敛)")

        # 远端时间戳**更老**(或干脆没有) → 不许覆盖本地这份更可信的配置
        stale = {"ai": {"providers": [{"name": "DeepSeek", "protocol": "openai",
                                       "base_url": "https://old.example.test/v1",
                                       "model": "old-model", "api_key": "sk-fake-old"}],
                        "default_index": 0,
                        "updated_at": "2000-01-01T00:00:00+08:00", "updated_by": "phl"}}
        kept, _ = engine.merge("settings.ai", None, engine.collect("settings.ai"), stale)
        check("网页新增" in names(kept["ai"]), "更老的远端不许把本地已有的服务商删掉")
        check(kept["ai"]["providers"][0]["base_url"] == "https://api.deepseek.com/anthropic",
              "更老的远端不许改本地那条")

        # 落盘后再 collect 一次: 内容必须与刚写下去的一致(否则每轮同步都会空推)
        again = engine.collect("settings.ai")
        check(again == merged_doc, "collect → merge → apply 一轮之后要收敛(内容完全一致)")

        # **时间戳自愈**: 本地文档里没有 updated_at 时, 第一次 collect 会盖一个并写回
        # 本地(只写这一个字段), 否则每轮 collect 都盖"现在" → 永远收敛不了。
        fresh_root = Path(tmp) / "fresh"
        fresh_root.mkdir(parents=True, exist_ok=True)
        fs.save_yaml(fresh_root / fs.SETTINGS, {
            "version": 1,
            "ai": {"providers": [{"id": "p-9", "name": "A", "protocol": "openai",
                                  "base_url": "https://a.example.test/v1",
                                  "api_key": "sk-fake-a", "models": ["m"]}],
                   "active_provider_id": "p-9", "active_model": "m"},
        })
        fresh = SyncEngine(client=None, dek=b"0" * 32, user_id=1, username="tester",
                           data_dir=fresh_root)
        one = fresh.collect("settings.ai")
        check((fs.load_settings_at(fresh_root / fs.SETTINGS)["ai"] or {}).get("updated_at")
              == one["ai"]["updated_at"], "第一次 collect 要把时间戳写回本地")
        check(fresh.collect("settings.ai") == one, "第二次 collect 必须完全一致")
        check(fresh.collect("settings.ai") == one, "第三次 collect 也必须完全一致")
        # 而且要真的让"本地没变"看得出来: 同一份快照当基版 → 合并结果就是原样
        snapshot = fresh.collect("settings.ai")
        settle, _ = fresh.merge("settings.ai", snapshot, snapshot, snapshot)
        check(settle == snapshot, "同一份内容合并三轮之后逐字节不变")

        # 本机改一条 → updated_by 变回 pll, 且时间戳会更新
        edited = fs.load_settings_at(root / fs.SETTINGS)
        edited["ai"]["providers"][0]["model"] = ""       # 清掉默认模型 = 用户改过
        edited["ai"]["providers"][0]["models"] = ["deepseek-reasoner"]
        edited["ai"]["active_model"] = "deepseek-reasoner"
        fs.save_yaml(root / fs.SETTINGS, edited)
        stamped = engine.collect("settings.ai")
        check(stamped["ai"]["providers"][0]["model"] == "deepseek-reasoner",
              "本机改过的模型要带上去")
        check(stamped["ai"]["updated_by"] == "pll", "本机改过就记成 pll 改的")

    # 6. 保存别的设置(工作区之类)不能把刚同步下来的服务商清空
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / fs.SETTINGS
        fs.save_yaml(path, {
            "version": 1,
            "ai": {"providers": [{"id": "p-1", "name": "网页同步来的", "protocol": "openai",
                                  "base_url": "https://x.example.test/v1",
                                  "api_key": "sk-fake-web", "models": ["m"]}],
                   "active_provider_id": "p-1", "active_model": "m",
                   "updated_at": "2030-01-01T00:00:00+08:00", "updated_by": "web"},
            "agent": {"workspace": "/old"},
        })
        load = Config.from_doc(fs.load_settings_at(path))
        # 模拟"内存里的 Config 没有服务商"(用户只是改了工作区, 没开过 AI 设置页)
        load.ai_providers = []
        load.agent_provider_id = ""
        load.agent_model = ""
        load.agent_workspace = "/new"
        real_update = fs.update_settings
        fs.update_settings = lambda mutate, *a, **k: fs.update_settings_at(path, mutate)  # noqa: E731
        try:
            load.save()
        finally:
            fs.update_settings = real_update
        after_save = fs.load_settings_at(path)
        check([p["name"] for p in (after_save["ai"].get("providers") or [])] == ["网页同步来的"],
              "保存别的设置时不许把同步下来的服务商清空")
        check(after_save["ai"].get("updated_by") == "web", "没改服务商就不许抢署名")
        check(after_save["agent"]["workspace"] == "/new", "别的设置照常保存")

    # 7. **密钥必须随对象上云**: 网页端没有本地配置, 同步对象里没有 api_key 它就用不了
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        fs.save_yaml(root / fs.SETTINGS, {
            "version": 1,
            "ai": {
                "providers": [
                    {"id": "p-1", "name": "DeepSeek", "protocol": "openai",
                     "base_url": "https://api.deepseek.com/v1", "api_key": "sk-fake-pll-1",
                     "models": ["deepseek-chat"]},
                    {"id": "p-2", "name": "备用", "protocol": "anthropic",
                     "base_url": "https://api.moonshot.cn/v1", "api_key": "sk-fake-pll-2",
                     "models": ["kimi-k2"]},
                ],
                "active_provider_id": "p-2",
                "active_model": "kimi-k2",
            },
        })
        engine = SyncEngine(client=None, dek=b"0" * 32, user_id=1, username="tester",
                            data_dir=root)
        doc = engine.collect("settings.ai")
        keys = [p.get("api_key") for p in doc["ai"]["providers"]]
        check(keys == ["sk-fake-pll-1", "sk-fake-pll-2"],
              f"两个服务商的 Key 都要进同步对象, 实际拿到 {keys}")
        # 规范形态的每一条都必须带 api_key 这个键（哪怕值是空串）
        for row in doc["ai"]["providers"]:
            check("api_key" in row, "规范形态的每条都要有 api_key 字段")
        # 本地专有字段不上云(但本地那份要留着)
        check("id" not in doc["ai"]["providers"][0], "规范形态不带本地 id")
        check("models" not in doc["ai"]["providers"][0], "规范形态不带本地 models 列表")
        check((fs.load_settings_at(root / fs.SETTINGS)["ai"]["providers"][0]["api_key"]
               == "sk-fake-pll-1"), "本地文件里的 Key 原样保留")
        # **只有单值的 model**（取本机在用的那个）: 网页端按 model 发请求
        check([p["model"] for p in doc["ai"]["providers"]] == ["deepseek-chat", "kimi-k2"],
              "规范形态给的是单值 model")

    print("aiconfig normalize self-check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
