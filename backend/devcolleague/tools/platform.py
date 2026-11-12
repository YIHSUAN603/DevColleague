"""查詢 ADS/TFS、GitHub 的內建工具（皆為唯讀）。

工具只回傳 LLM 需要的欄位，避免浪費 token。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..services.activity import ActivityService
from .base import Tool, ToolRegistry

_CONNECTION_FILTER = {
    "connection": {"type": "string", "description": "只查詢指定的連線名稱；省略時查詢所有連線"},
}


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M") if dt else None


def _age_days(dt: datetime | None) -> int | None:
    if not dt:
        return None
    return (datetime.now(dt.tzinfo) - dt).days


def register_platform_tools(registry: ToolRegistry, activity: ActivityService) -> None:
    async def search_pull_requests(args: dict[str, Any]) -> Any:
        role = args.get("role", "author")
        conn = args.get("connection")
        if role == "reviewer":
            result = await activity.review_requests(conn)
        else:
            result = await activity.my_pull_requests(conn)
        min_age = args.get("min_age_days")
        items = []
        for pr in result.items:
            age = _age_days(pr.created_at)
            if min_age is not None and (age is None or age < min_age):
                continue
            items.append({
                "connection": activity.connection_name(pr.connection_id),
                "connection_id": pr.connection_id,
                "platform": pr.platform,
                "id": pr.id,
                "title": pr.title,
                "repo": pr.repo,
                "project": pr.project,
                "author": pr.author,
                "status": pr.status,
                "draft": pr.is_draft,
                "branch": f"{pr.source_branch} → {pr.target_branch}" if pr.source_branch else None,
                "created": _fmt_dt(pr.created_at),
                "age_days": age,
                "url": pr.url,
            })
        return {"pull_requests": items, "errors": result.errors}

    async def list_work_items(args: dict[str, Any]) -> Any:
        conn = args.get("connection")
        days = args.get("changed_within_days")
        if days:
            now = datetime.now().astimezone()
            result = await activity.work_items_changed(now - timedelta(days=int(days)), now, conn)
        else:
            result = await activity.assigned_work_items(conn)
        return {
            "work_items": [
                {
                    "connection": activity.connection_name(w.connection_id),
                    "id": w.id,
                    "title": w.title,
                    "type": w.type,
                    "state": w.state,
                    "project": w.project or w.repo,
                    "assigned_to": w.assigned_to,
                    "updated": _fmt_dt(w.updated_at),
                    "url": w.url,
                }
                for w in result.items
            ],
            "errors": result.errors,
        }

    async def list_builds(args: dict[str, Any]) -> Any:
        result = await activity.recent_builds(args.get("connection"))
        only_failed = bool(args.get("only_failed"))
        return {
            "builds": [
                {
                    "connection": activity.connection_name(b.connection_id),
                    "connection_id": b.connection_id,
                    "id": b.id,
                    "repo": b.repo,
                    "platform_project": b.project,
                    "name": b.name,
                    "project": b.project or b.repo,
                    "branch": b.branch,
                    "status": b.status,
                    "result": b.result,
                    "requested_by": b.requested_by,
                    "started": _fmt_dt(b.started_at),
                    "url": b.url,
                }
                for b in result.items
                if not only_failed or b.result == "failed"
            ],
            "errors": result.errors,
        }

    async def list_my_commits(args: dict[str, Any]) -> Any:
        days = int(args.get("days", 1))
        now = datetime.now().astimezone()
        result = await activity.my_commits(now - timedelta(days=days), now, args.get("connection"))
        return {
            "commits": [
                {
                    "repo": c.repo,
                    "sha": c.sha[:8],
                    "message": c.message.splitlines()[0] if c.message else "",
                    "date": _fmt_dt(c.date),
                }
                for c in result.items
            ],
            "errors": result.errors,
        }

    registry.register(Tool(
        name="search_pull_requests",
        description=(
            "查詢使用者在 ADS/TFS 與 GitHub 上進行中的 Pull Request。"
            "role=author 為使用者建立的 PR，role=reviewer 為待使用者審查的 PR。"
            "可用 min_age_days 找出擱置超過指定天數的 PR。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ["author", "reviewer"], "description": "author 或 reviewer"},
                "min_age_days": {"type": "integer", "description": "只回傳建立超過幾天的 PR"},
                **_CONNECTION_FILTER,
            },
        },
        handler=search_pull_requests,
    ))
    registry.register(Tool(
        name="list_work_items",
        description=(
            "查詢 Work Item（ADS/TFS）或 Issue（GitHub）。"
            "預設回傳指派給使用者且尚未關閉的項目；指定 changed_within_days 時改為回傳最近幾天內使用者異動過的項目。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "changed_within_days": {"type": "integer", "description": "最近幾天內異動過的項目"},
                **_CONNECTION_FILTER,
            },
        },
        handler=list_work_items,
    ))
    registry.register(Tool(
        name="list_builds",
        description="查詢最近的建置／Pipeline（GitHub Actions）執行結果。",
        input_schema={
            "type": "object",
            "properties": {
                "only_failed": {"type": "boolean", "description": "只回傳失敗的建置"},
                **_CONNECTION_FILTER,
            },
        },
        handler=list_builds,
    ))
    registry.register(Tool(
        name="list_my_commits",
        description="查詢使用者最近幾天提交的 commit。",
        input_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "往前查詢幾天，預設 1"},
                **_CONNECTION_FILTER,
            },
        },
        handler=list_my_commits,
    ))
