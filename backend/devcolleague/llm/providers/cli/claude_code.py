"""Claude Code CLI（`claude -p`）adapter。

輸出格式：`--output-format stream-json --verbose --include-partial-messages`，每行一個 JSON 物件：
- stream_event：原始 API 串流事件（取 text_delta 作為即時文字）
- assistant／user：完整訊息（取 tool_use／tool_result 顯示工具呼叫過程）
- result：最終結果與用量
"""

from __future__ import annotations

import json
from typing import Any

from ...types import ChatEvent, ChatRequest, ProviderCapabilities
from .base import CliProvider, CliStatus, run_capture, truncate

# 非互動模式下只開放唯讀工具，避免 CLI 未經確認修改檔案或執行指令
READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return json.dumps(content, ensure_ascii=False)


class ClaudeCodeProvider(CliProvider):
    executable_name = "claude"
    capabilities = ProviderCapabilities(
        custom_tools=False,
        local_workspace=True,
        streaming=True,
        native_mcp=True,
        native_subagents=True,
    )

    def build_args(self, exe: str, request: ChatRequest) -> list[str]:
        args = [
            exe,
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--allowedTools", *READ_ONLY_TOOLS, *(["mcp__devcolleague__*"] if request.mcp_url else []),
            "--tools", ",".join(READ_ONLY_TOOLS),
            "--strict-mcp-config",
        ]
        if request.mcp_url:
            config = {"mcpServers": {"devcolleague": {"type": "http", "url": request.mcp_url,
                      "headers": {"Authorization": "Bearer ${DEVCOLLEAGUE_MCP_TOKEN}"}}}}
            args += ["--mcp-config", json.dumps(config)]
        model = request.model or self._default_model
        if model:
            args += ["--model", model]
        if request.system:
            args += ["--append-system-prompt", request.system]
        return args

    def parse_line(self, obj: dict[str, Any], state: dict[str, Any]) -> list[ChatEvent]:
        kind = obj.get("type")
        # 子代理（subagent）內部的事件不直接顯示文字
        is_nested = obj.get("parent_tool_use_id") is not None

        if kind == "stream_event" and not is_nested:
            event = obj.get("event") or {}
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                state["has_text"] = True
                return [ChatEvent.text(delta.get("text", ""))]
            if event.get("type") == "message_start" and state.get("has_text"):
                # 工具呼叫後的新一段回覆，與前段文字分隔
                return [ChatEvent.text("\n\n")]
            return []

        if kind == "assistant":
            content = (obj.get("message") or {}).get("content") or []
            return [
                ChatEvent("tool_call", {"id": b.get("id"), "name": b.get("name"), "arguments": b.get("input") or {}})
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]

        if kind == "user":
            content = (obj.get("message") or {}).get("content") or []
            if not isinstance(content, list):
                return []
            return [
                ChatEvent(
                    "tool_result",
                    {
                        "call_id": b.get("tool_use_id"),
                        "content": truncate(_tool_result_text(b.get("content"))),
                        "is_error": bool(b.get("is_error")),
                    },
                )
                for b in content
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]

        if kind == "result":
            if obj.get("is_error"):
                return [ChatEvent.error(f"Claude Code 執行失敗：{obj.get('result') or obj.get('subtype')}")]
            usage = obj.get("usage") or {}
            return [
                ChatEvent(
                    "done",
                    {
                        "stop_reason": obj.get("stop_reason") or "end_turn",
                        "usage": {
                            "input_tokens": usage.get("input_tokens"),
                            "output_tokens": usage.get("output_tokens"),
                        },
                        "cost_usd": obj.get("total_cost_usd"),
                    },
                )
            ]
        return []

    async def status(self) -> CliStatus:
        exe = self.resolve_executable()
        if not exe:
            return CliStatus(installed=False, detail="找不到 claude 指令")
        try:
            code, out = await run_capture([exe, "--version"])
            version = out.strip().splitlines()[0] if code == 0 and out.strip() else None
            code, out = await run_capture([exe, "auth", "status"])
            logged_in: bool | None = None
            detail = None
            try:
                info = json.loads(out)
                logged_in = bool(info.get("loggedIn"))
                detail = info.get("authMethod")
            except json.JSONDecodeError:
                detail = truncate(out.strip(), 200) or None
            return CliStatus(installed=True, version=version, logged_in=logged_in, detail=detail)
        except (TimeoutError, OSError) as exc:
            return CliStatus(installed=True, detail=f"狀態檢查失敗：{exc}")
