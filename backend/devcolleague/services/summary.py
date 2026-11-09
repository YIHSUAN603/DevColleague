"""工作摘要：彙整區間內的 commit、PR、Work Item 活動，交由 LLM 產生站會報告或週報。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from ..llm.gateway import Gateway
from ..llm.registry import ProviderService
from ..llm.types import ChatEvent, ChatMessage, ChatRequest
from .activity import ActivityService

RangeKind = Literal["today", "yesterday", "this_week", "custom"]
ReportKind = Literal["standup", "weekly"]

SUMMARY_SYSTEM_PROMPT = """你是協助開發者撰寫工作報告的助理。
根據使用者提供的工作活動資料（JSON）撰寫報告，規則：
- 使用繁體中文（台灣用語），輸出 Markdown。
- 只根據資料內容撰寫，不要編造資料中沒有的工作。
- 相關的 commit、PR、Work Item 合併成同一項工作描述，附上 PR 或 Work Item 連結。
- 資料不足以判斷的部分，以「（待補充）」標示讓使用者自行填寫。"""

REPORT_INSTRUCTIONS: dict[str, str] = {
    "standup": "請撰寫站會報告，分為三段：「昨天／這段期間完成」「今天預計」「卡關或需要協助」。"
    "「今天預計」可依進行中的 PR 與指派給我的 Work Item 推測。",
    "weekly": "請撰寫週報，分為：「本週完成」「進行中」「下週計畫」「風險與問題」，每項工作以一行條列。",
}


def resolve_range(kind: RangeKind, start: date | None = None, end: date | None = None) -> tuple[datetime, datetime]:
    """回傳本地時區的 [since, until) 區間。"""
    now = datetime.now().astimezone()
    tz = now.tzinfo
    today = datetime.combine(now.date(), time.min, tzinfo=tz)
    match kind:
        case "today":
            return today, now
        case "yesterday":
            return today - timedelta(days=1), today
        case "this_week":
            return today - timedelta(days=now.weekday()), now
        case "custom":
            if start is None or end is None:
                raise ValueError("自訂區間需要指定開始與結束日期")
            if end < start:
                raise ValueError("結束日期不可早於開始日期")
            return (
                datetime.combine(start, time.min, tzinfo=tz),
                datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz),
            )
    raise ValueError(f"不支援的區間：{kind}")


def _within(dt: datetime | None, since: datetime, until: datetime) -> bool:
    return dt is not None and since <= dt < until


class SummaryService:
    def __init__(self, activity: ActivityService, providers: ProviderService, gateway: Gateway) -> None:
        self._activity = activity
        self._providers = providers
        self._gateway = gateway

    async def collect(self, since: datetime, until: datetime) -> dict[str, Any]:
        commits, prs, changed, assigned = await asyncio.gather(
            self._activity.my_commits(since, until),
            self._activity.my_pull_requests(updated_since=since),
            self._activity.work_items_changed(since, until),
            self._activity.assigned_work_items(),
        )
        errors = {e["connection_id"]: e for r in (commits, prs, changed, assigned) for e in r.errors}
        return {
            "range": {"since": since.isoformat(), "until": until.isoformat()},
            "commits": [
                {"repo": c.repo, "message": c.message.strip(), "date": c.date.isoformat() if c.date else None, "url": c.url}
                for c in commits.items
                if c.date is None or _within(c.date, since, until)
            ],
            "pull_requests": [
                {"repo": p.repo, "title": p.title, "status": p.status, "url": p.url,
                 "branch": p.source_branch, "created": p.created_at.isoformat() if p.created_at else None}
                for p in prs.items
            ],
            "work_items_changed": [
                {"id": w.id, "title": w.title, "type": w.type, "state": w.state, "url": w.url}
                for w in changed.items
            ],
            "work_items_assigned_open": [
                {"id": w.id, "title": w.title, "type": w.type, "state": w.state, "url": w.url}
                for w in assigned.items
            ],
            "errors": list(errors.values()),
        }

    async def generate(
        self,
        range_kind: RangeKind,
        report_kind: ReportKind,
        *,
        start: date | None = None,
        end: date | None = None,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        try:
            since, until = resolve_range(range_kind, start, end)
            provider, _ = self._providers.build(provider_id)
        except (ValueError, LookupError) as exc:
            yield ChatEvent.error(str(exc))
            return

        activity = await self.collect(since, until)
        # 先讓使用者看到即將送給 LLM 的資料
        yield ChatEvent("activity", activity)

        payload = {k: v for k, v in activity.items() if k != "errors"}
        if not any(payload[k] for k in ("commits", "pull_requests", "work_items_changed")):
            yield ChatEvent.text("這段期間沒有找到任何 commit、PR 或 Work Item 活動。")
            yield ChatEvent("done", {"stop_reason": "end_turn", "usage": {}})
            return

        prompt = (
            f"{REPORT_INSTRUCTIONS[report_kind]}\n\n"
            f"報告區間：{since:%Y-%m-%d %H:%M} ～ {until:%Y-%m-%d %H:%M}\n\n"
            f"工作活動資料：\n```json\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n```"
        )
        request = ChatRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            system=SUMMARY_SYSTEM_PROMPT,
            model=model or None,
        )
        async for event in self._gateway.run(provider, request, use_tools=False):
            yield event
