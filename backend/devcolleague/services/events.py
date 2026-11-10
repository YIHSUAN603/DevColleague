"""背景事件通道與系統通知（F16）。

後端的背景工作（例行流程、事件提醒）透過 `EventBus` 發布事件，
前端以長連線 SSE（`/api/events`）訂閱，收到 `notification` 事件後交給桌面外殼顯示系統通知。
"""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from ..storage.db import utc_now

# 沒有任何訂閱者時（例如前端尚未啟動完成）暫存的事件上限，避免無限累積
PENDING_LIMIT = 50
# 單一訂閱者來不及讀取時的佇列上限；超過時丟棄最舊的事件
QUEUE_LIMIT = 100


@dataclass(frozen=True)
class AppEvent:
    type: str
    data: dict[str, Any]


class EventBus:
    """行程內的簡單發布／訂閱；每個訂閱者各有一個佇列。"""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[AppEvent]] = set()
        self._pending: deque[AppEvent] = deque(maxlen=PENDING_LIMIT)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: AppEvent) -> None:
        if not self._subscribers:
            self._pending.append(event)
            return
        for queue in self._subscribers:
            _put_dropping_oldest(queue, event)

    def subscribe(self) -> asyncio.Queue[AppEvent]:
        queue: asyncio.Queue[AppEvent] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        # 暫存的事件交給第一個連上的訂閱者
        while self._pending:
            _put_dropping_oldest(queue, self._pending.popleft())
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[AppEvent]) -> None:
        self._subscribers.discard(queue)


def _put_dropping_oldest(queue: asyncio.Queue[AppEvent], event: AppEvent) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(event)


@dataclass(frozen=True)
class NotificationTarget:
    """點擊通知後要開啟的位置。"""

    page: str
    conversation_id: str | None = None


@dataclass(frozen=True)
class Notification:
    id: str
    title: str
    body: str
    target: NotificationTarget | None = None
    created_at: str = field(default_factory=utc_now)


class NotificationService:
    """發送系統通知；實際顯示由前端與桌面外殼負責。"""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._ids = itertools.count(1)

    def notify(self, title: str, body: str, target: NotificationTarget | None = None) -> Notification:
        notification = Notification(id=str(next(self._ids)), title=title, body=body, target=target)
        self._bus.publish(AppEvent("notification", asdict(notification)))
        return notification
