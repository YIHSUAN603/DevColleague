"""平台差異偵測：先去重與限額，再喚醒助理。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from ..routines.definitions import Routine
from ..routines.service import RoutineService
from ..storage.db import Database, utc_now
from .activity import ActivityService
from .events import NotificationService, NotificationTarget


class MonitorSettings(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=5, ge=1, le=1440)
    stale_pr_days: int = Field(default=3, ge=1, le=365)
    daily_limit: int = Field(default=20, ge=0, le=1000)
    provider: str | None = None
    model: str | None = None


class EventMonitor:
    def __init__(self, db: Database, activity: ActivityService, routines: RoutineService,
                 notifications: NotificationService) -> None:
        self.db, self.activity, self.routines, self.notifications = db, activity, routines, notifications

    def settings(self) -> MonitorSettings:
        return MonitorSettings.model_validate_json(self.db.get_setting("monitor.settings", "{}"))

    def configure(self, settings: MonitorSettings) -> MonitorSettings:
        self.db.set_setting("monitor.settings", settings.model_dump_json())
        return settings

    async def poll(self, now: datetime | None = None) -> dict:
        now = now or datetime.now().astimezone()
        settings = self.settings()
        if not settings.enabled:
            return {"events": 0, "errors": []}
        results = await asyncio.gather(self.activity.review_requests(), self.activity.recent_builds(),
                                       self.activity.my_pull_requests(), self.activity.mentions())
        settings = self.settings()
        if not settings.enabled:
            return {"events": 0, "errors": []}
        previous = json.loads(self.db.get_setting("monitor.snapshot", "[]"))
        current = set()
        failed_connections = {e["connection_id"] for r in results for e in r.errors}
        # 連線失敗時保留該連線的基準，避免恢復後把全部資料誤當新事件。
        current.update(key for key in previous if key.split(":", 2)[1] in failed_connections)
        candidates = []
        for kind, result in zip(("review", "build", "stale", "mention"), results, strict=True):
            for item in result.items:
                if kind == "build" and item.result != "failed":
                    continue
                if kind == "stale":
                    stamp = item.updated_at or item.created_at
                    if not stamp or item.status != "active" or now - stamp < timedelta(days=settings.stale_pr_days):
                        continue
                # URL 包含 repo／專案，可區別不同 repo 的同號 PR。
                key = f"{kind}:{item.connection_id}:{item.url}"
                if kind in ("mention", "stale"):
                    key += f":{item.updated_at}"
                if kind == "mention":
                    key += f":{item.id}"
                current.add(key)
                seen = kind != "review" and self.db.fetch_one("SELECT id FROM event_seen WHERE id=?", (key,))
                if key not in previous and not seen:
                    candidates.append({"key": key, "kind": kind, "item": item.model_dump(mode="json")})
        if not candidates:
            self.db.set_setting("monitor.snapshot", json.dumps(sorted(current)))
            return {"events": 0, "errors": [e for r in results for e in r.errors]}
        today = now.date().isoformat()
        usage = json.loads(self.db.get_setting("monitor.usage", "{}"))
        count = usage.get("count", 0) if usage.get("date") == today else 0
        if count >= settings.daily_limit:
            self.notifications.notify("平台工作提醒", "\n".join(f"{e['kind']}：{e['item'].get('title') or e['item'].get('name')}" for e in candidates)[:1000], NotificationTarget("dashboard"))
        else:
            if "platform-events" in self.routines.active:
                return {"events": 0, "pending": len(candidates)}
            routine = Routine(id="platform-events", name="平台事件提醒", provider=settings.provider, model=settings.model, notify=False,
                              prompt="平台出現以下新事件。參考記憶判斷是否值得打擾；需要時查詢細節並以 notify_user 通知，否則保持安靜。\n"
                              + json.dumps(candidates, ensure_ascii=False))
            self.routines.start(routine, "event")
            self.db.set_setting("monitor.usage", json.dumps({"date": today, "count": count + 1}))
        for event in candidates:
            self.db.execute("INSERT OR IGNORE INTO event_seen(id,created_at) VALUES(?,?)", (event["key"], utc_now()))
        self.db.set_setting("monitor.snapshot", json.dumps(sorted(current)))
        return {"events": len(candidates), "errors": [e for r in results for e in r.errors]}
