"""跨連線彙整平台資料。單一連線失敗時記錄錯誤並繼續處理其他連線。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, TypeVar

from ..integrations.base import ConnectionInfo, Integration, IntegrationError
from ..integrations.models import Build, Commit, PullRequest, WorkItem, WorkItemProgress
from ..integrations.registry import ConnectionService

T = TypeVar("T")


@dataclass
class AggregateResult(Generic[T]):
    items: list[T] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def _sort_key(dt: datetime | None) -> float:
    return dt.timestamp() if dt else 0


class ActivityService:
    def __init__(self, connections: ConnectionService) -> None:
        self._connections = connections

    def connection_name(self, connection_id: str) -> str:
        info = self._connections.get(connection_id)
        return info.name if info else connection_id

    def _targets(self, connection: str | None) -> list[ConnectionInfo]:
        infos = self._connections.list()
        if connection:
            infos = [c for c in infos if c.name == connection or c.id == connection]
        return infos

    async def _gather(
        self,
        connection: str | None,
        fn: Callable[[Integration], Awaitable[list[T]]],
    ) -> AggregateResult[T]:
        targets = self._targets(connection)

        async def run(info: ConnectionInfo) -> list[T]:
            integration = self._connections.open(info)
            try:
                return await fn(integration)
            finally:
                await integration.aclose()

        results = await asyncio.gather(*(run(t) for t in targets), return_exceptions=True)
        aggregate: AggregateResult[T] = AggregateResult()
        for info, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                message = str(result) if isinstance(result, IntegrationError) else f"未預期的錯誤：{result!r}"
                aggregate.errors.append({"connection_id": info.id, "connection": info.name, "message": message})
            else:
                aggregate.items.extend(result)
        return aggregate

    async def my_pull_requests(
        self, connection: str | None = None, updated_since: datetime | None = None
    ) -> AggregateResult[PullRequest]:
        r = await self._gather(connection, lambda i: i.list_my_pull_requests(updated_since=updated_since))
        r.items.sort(key=lambda p: _sort_key(p.updated_at), reverse=True)
        return r

    async def review_requests(self, connection: str | None = None) -> AggregateResult[PullRequest]:
        r = await self._gather(connection, lambda i: i.list_review_requests())
        r.items.sort(key=lambda p: _sort_key(p.created_at))
        return r

    async def assigned_work_items(self, connection: str | None = None) -> AggregateResult[WorkItem]:
        r = await self._gather(connection, lambda i: i.list_assigned_work_items())
        r.items.sort(key=lambda w: _sort_key(w.updated_at), reverse=True)
        return r

    async def work_items_changed(
        self, since: datetime, until: datetime, connection: str | None = None
    ) -> AggregateResult[WorkItem]:
        r = await self._gather(connection, lambda i: i.list_work_items_changed(since, until))
        r.items.sort(key=lambda w: _sort_key(w.updated_at), reverse=True)
        return r

    async def work_item_progress(self, items: list[WorkItem]) -> AggregateResult[WorkItemProgress]:
        """依父子階層計算這些 Work Item 所屬上層項目的進度；不支援階層的平台不會回傳資料。"""
        ids: dict[str, list[str]] = {}
        for item in items:
            ids.setdefault(item.connection_id, []).append(item.id)
        if not ids:
            return AggregateResult()
        return await self._gather(None, lambda i: i.list_work_item_progress(ids.get(i.info.id, [])))

    async def mentions(self) -> AggregateResult[WorkItem]:
        return await self._gather(None, lambda i: i.list_mentions())

    async def projects(self) -> AggregateResult[str]:
        """所有連線可查詢的專案名稱（去除重複）。"""
        r = await self._gather(None, lambda i: i.list_projects())
        r.items = list(dict.fromkeys(r.items))
        return r

    async def recent_builds(self, connection: str | None = None, limit: int = 10) -> AggregateResult[Build]:
        r = await self._gather(connection, lambda i: i.list_recent_builds(limit))
        r.items.sort(key=lambda b: _sort_key(b.started_at), reverse=True)
        return r

    async def my_commits(
        self, since: datetime, until: datetime, connection: str | None = None
    ) -> AggregateResult[Commit]:
        r = await self._gather(connection, lambda i: i.list_my_commits(since, until))
        r.items.sort(key=lambda c: _sort_key(c.date), reverse=True)
        return r
