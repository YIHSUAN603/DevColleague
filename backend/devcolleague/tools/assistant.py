"""例行流程、記憶、草稿與通知的共用工具。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from ..routines.definitions import Routine
from ..routines.service import RoutineService
from ..services.daily_report import DailyReportService
from ..services.events import NotificationService, NotificationTarget
from ..services.memory import MemoryInput, MemoryService
from ..services.summary import SummaryService, resolve_range
from .base import Tool, ToolRegistry
from .context import execution_context


def register_assistant_tools(registry: ToolRegistry, memory: MemoryService, daily: DailyReportService,
                             routines: RoutineService, notifications: NotificationService, summary: SummaryService) -> None:
    async def search_memory(args: dict) -> Any:
        return memory.search(args["query"])

    async def save_memory(args: dict) -> Any:
        context = execution_context.get()
        return memory.save(MemoryInput(**args), memory_id=args.get("id"),
                           source=f"conversation:{context.conversation_id}" if context else "assistant")

    async def collect_daily_report(args: dict) -> Any:
        day = _report_day(args)
        return {**await daily.collect(day), "previous_report": daily.previous(day), "existing_report": daily.get(day)}

    async def read_daily_report(args: dict) -> Any:
        return daily.get(_report_day(args))

    async def collect_work_summary(args: dict) -> Any:
        start = date.fromisoformat(args["start"]) if args.get("start") else None
        end = date.fromisoformat(args["end"]) if args.get("end") else None
        return await summary.collect(*resolve_range(args.get("range", "yesterday"), start, end))

    async def save_draft(args: dict) -> Any:
        return daily.save_draft(_report_day(args), args["content"])

    async def notify_user(args: dict) -> Any:
        context = execution_context.get()
        notifications.notify(args["title"], args["body"],
                             NotificationTarget("chat", context.conversation_id) if context else None)
        return {"sent": True}

    async def list_routines(args: dict) -> Any:
        return [r.model_dump() for r in routines.store.list()]

    async def save_routine(args: dict) -> Any:
        context = execution_context.get()
        if not context or not context.conversation_id or context.unattended:
            raise ValueError("只能在使用者參與的對話中提出 Routine 變更")
        return routines.propose(Routine(**args), context.conversation_id)

    definitions = [
        ("search_memory", "搜尋個人偏好、工作進度、設計決策與筆記。", search_memory,
         {"query": {"type": "string"}}, ["query"], "read_only"),
        ("save_memory", "記下個人記憶。不得儲存 Token、API Key 或密碼。", save_memory,
         {"id": {"type": "string"}, "kind": {"type": "string", "enum": ["preference", "work", "decision", "note"]},
          "content": {"type": "string"}}, ["content"], "local_write"),
        ("collect_daily_report", "查詢日報告活動、系統計算的進度與前一份報告；省略日期為上一個工作日。", collect_daily_report,
         {"date": {"type": "string", "description": "YYYY-MM-DD"}}, [], "read_only"),
        ("collect_work_summary", "依本機日曆區間查詢站會報告或週報所需的工作活動。", collect_work_summary,
         {"range": {"type": "string", "enum": ["today", "yesterday", "this_week", "custom"]},
          "start": {"type": "string"}, "end": {"type": "string"}}, [], "read_only"),
        ("read_daily_report", "讀取已儲存的日報告。", read_daily_report,
         {"date": {"type": "string"}}, [], "read_only"),
        ("save_daily_report_draft", "儲存日報告草稿，不覆蓋任何既有內容；須先 collect_daily_report，使用其中的真實進度。", save_draft,
         {"date": {"type": "string"}, "content": {"type": "string"}}, ["content"], "local_write"),
        ("notify_user", "有值得打擾的資訊時發送系統通知，點擊可接續目前對話。", notify_user,
         {"title": {"type": "string"}, "body": {"type": "string"}}, ["title", "body"], "notification"),
        ("list_routines", "查看目前的例行流程設定。", list_routines, {}, [], "read_only"),
    ]
    for name, description, handler, properties, required, risk in definitions:
        registry.register(Tool(name, description, {"type": "object", "properties": properties, "required": required}, handler, risk))
    registry.register(Tool("save_routine", "提出建立、修改或停用例行流程的完整設定。只建立待確認提案，使用者須按套用才生效。",
                           Routine.model_json_schema(), save_routine, "automation"))


def _report_day(args: dict) -> date:
    if args.get("date"):
        return date.fromisoformat(args["date"])
    day = datetime.now().astimezone().date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day
