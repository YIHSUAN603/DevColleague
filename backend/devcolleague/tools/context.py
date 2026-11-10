"""每次執行獨立的工具權限與稽核資料，不共用可變的全域狀態。"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    unattended: bool = False
    conversation_id: str | None = None
    allow_side_effects: tuple[str, ...] = ()
    audit: list[dict[str, Any]] = field(default_factory=list)


execution_context: ContextVar[ExecutionContext | None] = ContextVar("execution_context", default=None)
