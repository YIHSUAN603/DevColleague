"""LLM Gateway 統一介面。上層功能只依賴這裡定義的型別與 Provider 宣告的能力。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    # 是否支援由 DevColleague 定義的工具（API 模式通常為 True）
    custom_tools: bool
    # 是否能自行讀取本機工作目錄（CLI 模式通常為 True）
    local_workspace: bool
    # 是否支援串流輸出
    streaming: bool
    # 是否能直接連接 MCP Server（第四階段使用）
    native_mcp: bool = False
    # 是否原生支援 subagent（第四階段使用）
    native_subagents: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class ChatMessage:
    """與 Provider 無關的對話訊息。

    - user：content 為使用者文字；或 tool_results 不為空，代表工具執行結果
    - assistant：content 為回覆文字；tool_calls 為該輪要求的工具呼叫
    provider_raw 保存 Provider 原始內容（例如 Claude 的 thinking 區塊），
    僅在同一次 tool loop 內回傳給同一個 Provider，不寫入資料庫。
    """

    role: Literal["user", "assistant"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    provider_raw: Any = None


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ChatRequest:
    messages: list[ChatMessage]
    system: str = ""
    model: str | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    # CLI Provider 執行時的工作目錄
    cwd: str | None = None
    max_tokens: int = 32000


EventType = Literal[
    "text_delta",
    "tool_call",
    "tool_result",
    "delegate_start",
    "delegate_end",
    "confirm_required",
    # 工作摘要：送給 LLM 前先讓使用者看到彙整的資料
    "activity",
    "done",
    "error",
]


@dataclass
class ChatEvent:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def text(text: str) -> ChatEvent:
        return ChatEvent("text_delta", {"text": text})

    @staticmethod
    def error(message: str) -> ChatEvent:
        return ChatEvent("error", {"message": message})


class LLMProvider(Protocol):
    id: str
    capabilities: ProviderCapabilities

    def chat(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        """送出對話請求，以事件串流回傳文字片段、工具呼叫與最終結果。

        API Provider 每次只執行一輪：若模型要求呼叫工具，會送出 tool_call 事件，
        並以 done（stop_reason="tool_use"）結束，由 Gateway 執行工具後再呼叫下一輪。
        CLI Provider 自行執行完整的 agent loop，tool_call／tool_result 僅供顯示。
        """
        ...
