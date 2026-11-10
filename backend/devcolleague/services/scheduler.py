"""共用背景排程器：Routine cron 與平台事件輪詢。"""

from __future__ import annotations

import asyncio
import logging
import json
from datetime import datetime

from croniter import croniter

from ..routines.service import RoutineService
from .event_monitor import EventMonitor

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, routines: RoutineService, monitor: EventMonitor) -> None:
        self.routines, self.monitor = routines, monitor
        self.task: asyncio.Task | None = None
        self.last_poll: datetime | None = None
        self.monitor_task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.loop())

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now().astimezone()
        for routine in self.routines.store.list():
            if not routine.enabled or not routine.schedule or routine.id in self.routines.active:
                continue
            key = f"routine.cursor.{routine.id}"
            raw = self.routines.db.get_setting(key)
            # 第一次看見排程時建立基準，不回溯安裝前的時間。
            if not raw:
                self.routines.db.set_setting(key, now.isoformat())
                continue
            cursor = datetime.fromisoformat(raw).astimezone(now.tzinfo)
            due = croniter(routine.schedule, cursor).get_next(datetime)
            if due <= now:
                if routine.catch_up or (now - due).total_seconds() < 60:
                    self.routines.start(routine, "schedule")
                self.routines.db.set_setting(key, now.isoformat())
        settings = self.monitor.settings()
        is_polling = self.monitor_task is not None and not self.monitor_task.done()
        if settings.enabled and not is_polling and (self.last_poll is None or (now - self.last_poll).total_seconds() >= settings.interval_minutes * 60):
            self.last_poll = now
            self.monitor_task = asyncio.create_task(self.poll(now))

    async def poll(self, now: datetime) -> None:
        try:
            async with asyncio.timeout(120):
                result = await self.monitor.poll(now)
        except Exception as exc:
            result = {"events": 0, "errors": [{"connection_id": "排程器", "message": str(exc) or "事件檢查逾時"}]}
        self.routines.db.set_setting("monitor.last_poll", json.dumps({"at": now.isoformat(), **result}))

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("背景排程執行失敗")
            await asyncio.sleep(15)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.monitor_task:
            self.monitor_task.cancel()
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        await self.routines.close()
