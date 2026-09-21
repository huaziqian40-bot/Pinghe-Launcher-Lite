# -*- coding: utf-8 -*-
"""Agent 会话兼容：纯 PLL 会话不许被说成"PH Launcher 高级格式不支持"。

用户 2026-09-21 报的现象：**纯 phl lite 的对话**里也出现
「（这一条消息使用了 PH Launcher 的高级格式，……请使用 PH Launcher 查看。）」，
而且会卡住。

根因（用 testenv 里的真实会话复现）：
  `_compatible_history()` 原来写成
      if 正文非空 and 没有 tool_calls: 保留
      else: 换成提示串
  —— 「assistant 正文为空」这种**再正常不过**的轮次（被中断、只调工具、模型没吐字）
  会掉进 else，被替换成那句提示。更糟的是提示串会被 save_session 写回会话文件：
  用户那两个只聊了一句的会话，assistant 回复**就是这串提示本身**（61 字符），
  之后模型还会照着它回答 —— 自我固化。

    python -X utf8 scripts/test_agent_history.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from hellopinghe.app.agent import (  # noqa: E402
    AI_CALL_TIMEOUT, AI_MAX_RETRIES, MAX_ROUNDS,
    _UNSUPPORTED_HINT, _compatible_history, _is_placeholder, _text_of,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("  OK", message)


def hints(history) -> int:
    return sum(1 for m in _compatible_history(history) if m["content"] == _UNSUPPORTED_HINT)


def main() -> int:
    print("[1] 回归：纯 PLL 会话不该出现「格式不支持」提示")
    # 这两个形态就是用户真实会话的样子：system + user + assistant(正文为空/就是提示串)
    check(hints([{"role": "system", "content": "你是助手"},
                 {"role": "user", "content": "你好"},
                 {"role": "assistant", "content": ""}]) == 0,
          "assistant 正文为空 → 不再误报提示")
    check(hints([{"role": "user", "content": "嗨"},
                 {"role": "assistant", "content": "   "}]) == 0,
          "assistant 只有空白 → 不再误报提示")
    check(hints([{"role": "assistant", "content": None}]) == 0,
          "assistant 正文为 None → 不再误报提示")
    check(hints([{"role": "user", "content": None}]) == 0,
          "user 正文为 None（空轮）→ 也不误报")

    print("\n[2] 自愈：历史里已经被写进去的提示串要被丢掉")
    check(_is_placeholder(_UNSUPPORTED_HINT), "提示串能被识别为占位符")
    check(len(_compatible_history([{"role": "assistant", "content": _UNSUPPORTED_HINT}])) == 0,
          "整条就是提示串 → 丢弃（旧会话下次保存即被清理）")
    check(len(_compatible_history([{"role": "user", "content": _UNSUPPORTED_HINT}])) == 0,
          "user 侧的历史污染同样丢弃")
    check(len(_compatible_history([
        {"role": "system", "content": _UNSUPPORTED_HINT},
        {"role": "user", "content": "真问题"},
        {"role": "assistant", "content": "真回答"},
    ])) == 2, "只丢占位符，真正的对话照常保留")

    print("\n[3] 工具轮与正常对话")
    check(len(_compatible_history([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "t"}]},
        {"role": "tool", "tool_call_id": "1", "content": "结果"},
    ])) == 0, "工具调用轮 + 工具结果都不是对话内容 → 整段跳过（不是报错）")
    out = _compatible_history([
        {"role": "user", "content": "查一下课表"},
        {"role": "assistant", "content": "今天三节"},
    ])
    check([m["content"] for m in out] == ["查一下课表", "今天三节"], "正常问答原样保留")
    check(all(m["role"] in ("user", "assistant", "system") for m in out),
          "产出里只有对话角色（tool 不进去）")

    print("\n[4] 真正读不懂的内容 —— 提示仍然要给（别把门关死）")
    check(hints([{"role": "user", "content": {"weird": 1}}]) == 1,
          "抽不出文本的字典 → 给出提示")
    check(hints([{"role": "user", "content": [{"type": "tool_use", "id": "a"}]}]) == 1,
          "只有非文本块 → 给出提示")

    print("\n[5] 内容块数组尽量抽出文本（PHL 的 Anthropic 风格消息）")
    check(_text_of([{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}])
          == "第一段\n第二段", "多个 text 块按行拼接")
    check(_text_of([{"type": "tool_use", "id": "x"}, {"type": "text", "text": "只有这段"}])
          == "只有这段", "非文本块跳过")
    check(_text_of({"text": "字典里的文本"}) == "字典里的文本", "单块字典")
    check(_text_of(None) == "" and _text_of(123) == "", "读不出就是空串，不抛异常")
    check(hints([{"role": "user", "content": [{"type": "text", "text": "能读出来的"}]}]) == 0,
          "能抽出文本的块 → 不必给提示")

    print("\n[6] 卡死防护：模型调用必须有超时")
    check(isinstance(AI_CALL_TIMEOUT, (int, float)) and 10 <= AI_CALL_TIMEOUT <= 600,
          f"AI_CALL_TIMEOUT={AI_CALL_TIMEOUT}s 在合理区间")
    check(AI_MAX_RETRIES <= 2,
          f"AI_MAX_RETRIES={AI_MAX_RETRIES}（重试会把超时乘上去，不能太大）")
    check(isinstance(MAX_ROUNDS, int) and 1 <= MAX_ROUNDS <= 20,
          f"MAX_ROUNDS={MAX_ROUNDS} 有上限，工具循环不会无限跑")

    print("\n[7] 若本机有真实会话，抽样确认不会再被误判")
    sess_dir = ROOT / "testenv" / "data" / "agent"
    n = 0
    if sess_dir.is_dir():
        for p in sorted(sess_dir.glob("*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            got = hints(doc.get("history", []))
            check(got == 0, f"{p.name}: 误报提示 {got} 条（应为 0）")
            n += 1
    if not n:
        print("  （testenv 里没有会话文件，跳过 —— 不算失败）")

    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
