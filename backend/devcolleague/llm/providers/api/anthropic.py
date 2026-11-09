"""Claude（Anthropic API）adapter。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import anthropic

from ...types import ChatEvent, ChatMessage, ChatRequest, ProviderCapabilities, ToolCall

DEFAULT_MODEL = "claude-opus-5-5"
# 這些模型支援伺服器端的 refusal fallback（遇到安全分類拒絕時自動改由替代模型回覆）
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def to_anthropic_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user" and m.tool_results:
            result.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.call_id,
                        "content": r.content or "（無內容）",
                        "is_error": r.is_error,
                    }
                    for r in m.tool_results
                ],
            })
        elif m.role == "assistant":
            if m.provider_raw:
                result.append({"role": "assistant", "content": m.provider_raw})
                continue
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls
            )
            if blocks:
                result.append({"role": "assistant", "content": blocks})
        elif m.content:
            result.append({"role": "user", "content": m.content})
    return result


class AnthropicProvider:
    capabilities = ProviderCapabilities(custom_tools=True, local_workspace=False, streaming=True)

    def __init__(
        self,
        provider_id: str,
        api_key: str,
        default_model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.id = provider_id
        self._default_model = default_model or DEFAULT_MODEL
        self._use_fallbacks = not base_url
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url or None)

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        model = request.model or self._default_model
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": to_anthropic_messages(request.messages),
        }
        if request.system:
            params["system"] = request.system
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in request.tools
            ]
        if self._use_fallbacks and model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"

        try:
            async with self._client.beta.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "text":
                        yield ChatEvent.text(event.text)
                final = await stream.get_final_message()
        except anthropic.AuthenticationError:
            yield ChatEvent.error("Claude API Key 無效，請至設定確認")
            return
        except anthropic.RateLimitError:
            yield ChatEvent.error("Claude API 已達速率上限，請稍後再試")
            return
        except anthropic.APIStatusError as exc:
            yield ChatEvent.error(f"Claude API 錯誤（HTTP {exc.status_code}）：{exc.message}")
            return
        except anthropic.APIConnectionError:
            yield ChatEvent.error("無法連線到 Claude API，請確認網路狀態")
            return

        if final.stop_reason == "refusal":
            yield ChatEvent.error("模型拒絕回應此請求")
            return

        tool_calls = [
            ToolCall(id=b.id, name=b.name, arguments=b.input if isinstance(b.input, dict) else {})
            for b in final.content
            if b.type == "tool_use"
        ]
        for call in tool_calls:
            yield ChatEvent("tool_call", {"id": call.id, "name": call.name, "arguments": call.arguments})

        stop_reason = final.stop_reason
        if stop_reason == "max_tokens" and tool_calls:
            # 工具參數可能被截斷，不執行
            yield ChatEvent.error("回覆超過長度上限，工具呼叫未完成")
            return
        yield ChatEvent(
            "done",
            {
                "stop_reason": "tool_use" if tool_calls else stop_reason,
                "model": final.model,
                "usage": {
                    "input_tokens": final.usage.input_tokens,
                    "output_tokens": final.usage.output_tokens,
                },
                # 原樣保留（含 thinking 區塊），tool loop 下一輪送回
                "provider_raw": [b.model_dump(mode="json", exclude_none=True) for b in final.content],
            },
        )
