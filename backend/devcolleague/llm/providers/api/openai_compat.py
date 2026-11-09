"""OpenAI Chat Completions 相容 adapter。

同時適用 OpenAI 與提供相容端點的本機模型服務（例如 Ollama：http://127.0.0.1:11434/v1）。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ...types import ChatEvent, ChatMessage, ChatRequest, ProviderCapabilities

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def to_openai_messages(system: str, messages: list[ChatMessage]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})
    for m in messages:
        if m.role == "user" and m.tool_results:
            result.extend(
                {"role": "tool", "tool_call_id": r.call_id, "content": r.content or "（無內容）"}
                for r in m.tool_results
            )
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                    }
                    for c in m.tool_calls
                ]
            if msg["content"] or m.tool_calls:
                result.append(msg)
        elif m.content:
            result.append({"role": "user", "content": m.content})
    return result


class OpenAICompatProvider:
    capabilities = ProviderCapabilities(custom_tools=True, local_workspace=False, streaming=True)

    def __init__(
        self,
        provider_id: str,
        api_key: str | None,
        default_model: str | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.id = provider_id
        self._default_model = default_model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=(base_url or DEFAULT_BASE_URL).rstrip("/") + "/",
            headers=headers,
            timeout=httpx.Timeout(600.0, connect=10.0),
            transport=transport,
        )

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        model = request.model or self._default_model
        if not model:
            yield ChatEvent.error("尚未指定模型，請在 Provider 設定中填寫預設模型")
            return
        body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(request.system, request.messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in request.tools
            ]

        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, Any] = {}
        try:
            async with self._client.stream("POST", "chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode(errors="replace")[:300]
                    yield ChatEvent.error(f"API 錯誤（HTTP {resp.status_code}）：{detail}")
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    if chunk.get("usage"):
                        usage = {
                            "input_tokens": chunk["usage"].get("prompt_tokens"),
                            "output_tokens": chunk["usage"].get("completion_tokens"),
                        }
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield ChatEvent.text(delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
                        finish_reason = choice.get("finish_reason") or finish_reason
        except httpx.HTTPError as exc:
            yield ChatEvent.error(f"無法連線到模型服務：{exc}")
            return

        for index, slot in sorted(calls.items()):
            try:
                args = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                yield ChatEvent.error(f"模型產生的工具參數不是有效的 JSON：{slot['name']}")
                return
            yield ChatEvent(
                "tool_call",
                {"id": slot["id"] or f"call_{index}", "name": slot["name"], "arguments": args},
            )
        yield ChatEvent(
            "done",
            {"stop_reason": "tool_use" if calls else (finish_reason or "stop"), "model": model, "usage": usage},
        )
