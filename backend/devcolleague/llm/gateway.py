"""LLM Gateway：依 Provider 能力決定流程，並負責 API 模式的 tool loop。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

from ..tools.base import ToolRegistry
from .types import ChatEvent, ChatMessage, ChatRequest, LLMProvider, ToolCall, ToolResult

MAX_TOOL_ROUNDS = 10
TOOL_RESULT_PREVIEW_CHARS = 1000


class Gateway:
    def __init__(self, tools: ToolRegistry) -> None:
        self._tools = tools

    async def run(self, provider: LLMProvider, request: ChatRequest, *, use_tools: bool = True) -> AsyncIterator[ChatEvent]:
        """執行一次完整的對話回合，事件會原樣轉給前端（provider_raw 除外）。"""
        caps = provider.capabilities
        if use_tools and caps.custom_tools:
            request = replace(request, tools=self._tools.specs())
        else:
            request = replace(request, tools=[])

        messages = list(request.messages)
        total_usage = {"input_tokens": 0, "output_tokens": 0}

        for _ in range(MAX_TOOL_ROUNDS):
            text_parts: list[str] = []
            calls: list[ToolCall] = []
            done: ChatEvent | None = None

            async for event in provider.chat(replace(request, messages=messages)):
                if event.type == "text_delta":
                    text_parts.append(event.data.get("text", ""))
                    yield event
                elif event.type == "tool_call" and caps.custom_tools:
                    calls.append(ToolCall(event.data["id"], event.data["name"], event.data.get("arguments") or {}))
                    yield event
                elif event.type == "done":
                    done = event
                elif event.type == "error":
                    yield event
                    return
                else:
                    yield event

            if done is None:
                yield ChatEvent.error("模型未正常結束回應")
                return
            usage = done.data.get("usage") or {}
            for key in total_usage:
                total_usage[key] += usage.get(key) or 0

            if done.data.get("stop_reason") != "tool_use" or not calls:
                public = {k: v for k, v in done.data.items() if k != "provider_raw"}
                public["usage"] = total_usage
                yield ChatEvent("done", public)
                return

            messages.append(ChatMessage(
                role="assistant",
                content="".join(text_parts),
                tool_calls=calls,
                provider_raw=done.data.get("provider_raw"),
            ))
            results = await asyncio.gather(*(self._tools.execute(c.name, c.arguments) for c in calls))
            tool_results: list[ToolResult] = []
            for call, (content, is_error) in zip(calls, results, strict=True):
                tool_results.append(ToolResult(call.id, content, is_error))
                preview = content if len(content) <= TOOL_RESULT_PREVIEW_CHARS else content[:TOOL_RESULT_PREVIEW_CHARS] + "…"
                yield ChatEvent("tool_result", {"call_id": call.id, "content": preview, "is_error": is_error})
            messages.append(ChatMessage(role="user", tool_results=tool_results))

        yield ChatEvent.error(f"工具呼叫超過 {MAX_TOOL_ROUNDS} 輪，已停止")
