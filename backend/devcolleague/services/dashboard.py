"""統一儀表板：先回傳快取，再由前端要求背景更新。"""

from __future__ import annotations

import asyncio
from typing import Any

from ..storage.db import Database
from .activity import ActivityService

CACHE_KEY = "dashboard"


class DashboardService:
    def __init__(self, db: Database, activity: ActivityService) -> None:
        self._db = db
        self._activity = activity
        self._refresh_lock = asyncio.Lock()

    def cached(self) -> dict[str, Any]:
        hit = self._db.get_cache(CACHE_KEY)
        if hit is None:
            return {"data": None, "fetched_at": None}
        data, fetched_at = hit
        return {"data": data, "fetched_at": fetched_at}

    async def refresh(self) -> dict[str, Any]:
        # 同時有多個更新請求時只實際執行一次
        if self._refresh_lock.locked():
            async with self._refresh_lock:
                return self.cached()
        async with self._refresh_lock:
            my_prs, reviews, work_items, builds = await asyncio.gather(
                self._activity.my_pull_requests(),
                self._activity.review_requests(),
                self._activity.assigned_work_items(),
                self._activity.recent_builds(limit=15),
            )
            errors: dict[str, dict[str, str]] = {}
            for r in (my_prs, reviews, work_items, builds):
                for e in r.errors:
                    errors[e["connection_id"]] = e
            data = {
                "my_pull_requests": [p.model_dump(mode="json") for p in my_prs.items],
                "review_requests": [p.model_dump(mode="json") for p in reviews.items],
                "work_items": [w.model_dump(mode="json") for w in work_items.items],
                "builds": [b.model_dump(mode="json") for b in builds.items],
                "errors": list(errors.values()),
            }
            fetched_at = self._db.set_cache(CACHE_KEY, data)
            return {"data": data, "fetched_at": fetched_at}
