"""提供給 LLM 呼叫的工具。內建工具與日後的 MCP 工具共用同一個介面。"""

from __future__ import annotations

import json
from jsonschema import validate
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from ..llm.types import ToolSpec
from .context import execution_context

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]
RiskLevel = Literal["read_only", "local_write", "notification", "side_effect", "automation"]

MAX_RESULT_CHARS = 20000


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    risk: RiskLevel = "read_only"

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.input_schema)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        context = execution_context.get()
        result = await self._execute(name, arguments)
        if context is not None:
            context.audit.append({"name": name, "arguments": arguments, "content": result[0], "is_error": result[1]})
        return result

    async def _execute(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """執行工具，回傳（結果文字, 是否錯誤）。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"找不到工具：{name}", True
        context = execution_context.get()
        if context and context.unattended:
            if tool.risk == "automation" or (tool.risk == "side_effect" and name not in context.allow_side_effects):
                return f"無人值守執行不允許工具：{name}", True
        try:
            validate(arguments, tool.input_schema)
            result = await tool.handler(arguments)
        except Exception as exc:  # 工具錯誤回報給 LLM，而不是中斷對話
            return f"工具執行失敗：{exc}", True
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "…（結果過長已截斷）"
        return text, False
