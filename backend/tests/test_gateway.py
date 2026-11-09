from __future__ import annotations

from devcolleague.llm.gateway import Gateway
from devcolleague.llm.providers.api.anthropic import to_anthropic_messages
from devcolleague.llm.providers.api.openai_compat import to_openai_messages
from devcolleague.llm.types import ChatEvent, ChatMessage, ChatRequest, ToolCall, ToolResult
from devcolleague.tools.base import Tool, ToolRegistry

from .conftest import FakeProvider


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def echo(args: dict) -> dict:
        return {"echo": args.get("value")}

    async def boom(args: dict) -> dict:
        raise RuntimeError("壞掉了")

    registry.register(Tool("echo", "回傳輸入", {"type": "object"}, echo))
    registry.register(Tool("boom", "一定失敗", {"type": "object"}, boom))
    return registry


async def collect(gen) -> list[ChatEvent]:
    return [e async for e in gen]


async def test_tool_loop_executes_tools_and_continues() -> None:
    provider = FakeProvider([
        [
            ChatEvent.text("查一下"),
            ChatEvent("tool_call", {"id": "t1", "name": "echo", "arguments": {"value": 1}}),
            ChatEvent("tool_call", {"id": "t2", "name": "boom", "arguments": {}}),
            ChatEvent("done", {"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5}, "provider_raw": ["raw"]}),
        ],
        [
            ChatEvent.text("完成"),
            ChatEvent("done", {"stop_reason": "end_turn", "usage": {"input_tokens": 20, "output_tokens": 3}}),
        ],
    ])
    events = await collect(Gateway(make_registry()).run(provider, ChatRequest(messages=[ChatMessage("user", "hi")])))

    types = [e.type for e in events]
    assert types == ["text_delta", "tool_call", "tool_call", "tool_result", "tool_result", "text_delta", "done"]
    results = [e.data for e in events if e.type == "tool_result"]
    assert results[0] == {"call_id": "t1", "content": '{"echo": 1}', "is_error": False}
    assert results[1]["is_error"] is True and "壞掉了" in results[1]["content"]

    done = events[-1].data
    assert done["usage"] == {"input_tokens": 30, "output_tokens": 8}
    assert "provider_raw" not in done

    # 第一輪有帶工具定義；第二輪收到 assistant（含 provider_raw）與 tool_result
    assert [t.name for t in provider.requests[0].tools] == ["echo", "boom"]
    second = provider.requests[1].messages
    assert second[1].role == "assistant" and second[1].provider_raw == ["raw"]
    assert [r.call_id for r in second[2].tool_results] == ["t1", "t2"]


async def test_no_tools_for_cli_provider() -> None:
    provider = FakeProvider(
        [[ChatEvent("tool_call", {"id": "x", "name": "Read", "arguments": {}}), ChatEvent("done", {"stop_reason": "end_turn"})]],
        custom_tools=False,
    )
    events = await collect(Gateway(make_registry()).run(provider, ChatRequest(messages=[ChatMessage("user", "hi")])))
    assert provider.requests[0].tools == []
    assert [e.type for e in events] == ["tool_call", "done"]


async def test_error_stops_loop() -> None:
    provider = FakeProvider([[ChatEvent.error("壞了")]])
    events = await collect(Gateway(make_registry()).run(provider, ChatRequest(messages=[ChatMessage("user", "hi")])))
    assert [e.type for e in events] == ["error"]


def _tool_history() -> list[ChatMessage]:
    return [
        ChatMessage("user", "問題"),
        ChatMessage("assistant", "我查一下", tool_calls=[ToolCall("t1", "echo", {"value": 1})]),
        ChatMessage("user", tool_results=[ToolResult("t1", "結果")]),
    ]


def test_anthropic_message_conversion() -> None:
    msgs = to_anthropic_messages(_tool_history())
    assert msgs[0] == {"role": "user", "content": "問題"}
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "echo", "input": {"value": 1}}
    assert msgs[2]["content"][0]["type"] == "tool_result"

    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    assert to_anthropic_messages([ChatMessage("assistant", "x", provider_raw=raw)])[0]["content"] == raw


def test_openai_message_conversion() -> None:
    msgs = to_openai_messages("系統", _tool_history())
    assert msgs[0] == {"role": "system", "content": "系統"}
    assert msgs[2]["tool_calls"][0]["function"]["arguments"] == '{"value": 1}'
    assert msgs[3] == {"role": "tool", "tool_call_id": "t1", "content": "結果"}
