"""Codex CLI（`codex exec --json`）adapter。

輸出為 JSON Lines，主要事件：
- thread.started／turn.started
- item.started／item.completed：item.type 為 agent_message、command_execution、reasoning 等
- turn.completed（含 usage）／turn.failed／error
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
import json
from typing import Any

from ...types import ChatEvent, ChatRequest, ProviderCapabilities
from .base import CliProvider, CliStatus, run_capture, truncate


class CodexProvider(CliProvider):
    executable_name = "codex"
    capabilities = ProviderCapabilities(
        custom_tools=False,
        local_workspace=True,
        streaming=True,
        native_mcp=True,
        native_subagents=False,
    )

    def build_args(self, exe: str, request: ChatRequest) -> list[str]:
        args = [exe, "exec", "--json", "--skip-git-repo-check", "-s", "read-only",
                "--ignore-user-config", "--ignore-rules", "--ephemeral"]
        args += ["-c", "features.shell_tool=false"]
        if request.mcp_url:
            config = '{devcolleague={url=' + json.dumps(request.mcp_url) + ',bearer_token_env_var="DEVCOLLEAGUE_MCP_TOKEN"}}'
            args += ["-c", "mcp_servers=" + config]
        model = request.model or self._default_model
        if model:
            args += ["-m", model]
        if request.cwd:
            args += ["-C", request.cwd]
        # 讀取 stdin 作為提示
        args.append("-")
        return args

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        # codex exec 沒有獨立的 system prompt 參數，將其放在提示開頭
        if request.system and request.messages:
            first, *rest = request.messages
            request = replace(
                request,
                messages=[replace(first, content=f"{request.system}\n\n{first.content}"), *rest],
                system="",
            )
        async for event in super().chat(request):
            yield event

    def parse_line(self, obj: dict[str, Any], state: dict[str, Any]) -> list[ChatEvent]:
        kind = obj.get("type")
        item = obj.get("item") or {}
        item_type = item.get("type")

        if kind == "item.started" and item_type == "command_execution":
            return [ChatEvent("tool_call", {"id": item.get("id"), "name": "shell", "arguments": {"command": item.get("command")}})]

        if kind == "item.completed":
            if item_type == "agent_message":
                text = item.get("text", "")
                prefix = "\n\n" if state.get("has_text") else ""
                state["has_text"] = True
                return [ChatEvent.text(prefix + text)]
            if item_type == "command_execution":
                exit_code = item.get("exit_code")
                return [
                    ChatEvent(
                        "tool_result",
                        {
                            "call_id": item.get("id"),
                            "content": truncate(item.get("aggregated_output") or ""),
                            "is_error": exit_code not in (0, None),
                        },
                    )
                ]
            if item_type in ("file_change", "mcp_tool_call", "web_search"):
                return [ChatEvent("tool_call", {"id": item.get("id"), "name": item_type, "arguments": {}})]
            if item_type == "error":
                return [ChatEvent.error(item.get("message", "Codex 發生錯誤"))]
            return []

        if kind == "turn.completed":
            usage = obj.get("usage") or {}
            return [
                ChatEvent(
                    "done",
                    {
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens")},
                    },
                )
            ]
        if kind == "turn.failed":
            return [ChatEvent.error(f"Codex 執行失敗：{(obj.get('error') or {}).get('message', '')}")]
        if kind == "error":
            return [ChatEvent.error(f"Codex 錯誤：{obj.get('message', '')}")]
        return []

    async def status(self) -> CliStatus:
        exe = self.resolve_executable()
        if not exe:
            return CliStatus(installed=False, detail="找不到 codex 指令")
        try:
            code, out = await run_capture([exe, "--version"])
            version = out.strip().splitlines()[0] if code == 0 and out.strip() else None
            code, out = await run_capture([exe, "login", "status"])
            text = out.strip()
            logged_in = code == 0 and "not logged in" not in text.lower()
            return CliStatus(installed=True, version=version, logged_in=logged_in, detail=truncate(text, 200) or None)
        except (TimeoutError, OSError) as exc:
            return CliStatus(installed=True, detail=f"狀態檢查失敗：{exc}")
