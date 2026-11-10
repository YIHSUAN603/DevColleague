from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from devcolleague.api.deps import event_stream
from devcolleague.container import Container
from devcolleague.services.events import PENDING_LIMIT, AppEvent, EventBus, NotificationTarget


def _parse(chunk: str) -> tuple[str, dict]:
    lines = chunk.strip().split("\n")
    assert lines[0].startswith("event: ")
    return lines[0][7:], json.loads(lines[1][6:])


def test_events_endpoint_requires_session_token(container: Container) -> None:
    from devcolleague.app import create_app

    raw = TestClient(create_app(container))
    assert raw.get("/api/events").status_code == 401


def test_bus_delivers_to_all_subscribers() -> None:
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish(AppEvent("notification", {"title": "x"}))
    assert a.get_nowait().data == {"title": "x"}
    assert b.get_nowait().data == {"title": "x"}
    bus.unsubscribe(a)
    assert bus.subscriber_count == 1


def test_bus_keeps_events_until_first_subscriber() -> None:
    bus = EventBus()
    for i in range(PENDING_LIMIT + 5):
        bus.publish(AppEvent("notification", {"n": i}))
    queue = bus.subscribe()
    received = [queue.get_nowait().data["n"] for _ in range(queue.qsize())]
    # 只保留最新的 PENDING_LIMIT 筆，且只交給第一個訂閱者
    assert received == list(range(5, PENDING_LIMIT + 5))
    assert bus.subscribe().empty()


def test_event_stream_sends_notification_and_heartbeat(container: Container) -> None:
    async def run() -> list[str]:
        stream = event_stream(container.events, heartbeat=0.01)
        container.notifications.notify("標題", "內容", NotificationTarget(page="chat", conversation_id="c1"))
        chunks = [await anext(stream), await anext(stream)]
        await stream.aclose()
        return chunks

    first, second = asyncio.run(run())
    event_type, data = _parse(first)
    assert event_type == "notification"
    assert data["title"] == "標題"
    assert data["body"] == "內容"
    assert data["target"] == {"page": "chat", "conversation_id": "c1"}
    assert second == ": ping\n\n"
    # 串流關閉後取消訂閱
    assert container.events.subscriber_count == 0


def test_test_notification_endpoint_publishes_event(client: TestClient, container: Container) -> None:
    queue = container.events.subscribe()
    resp = client.post("/api/notifications/test")
    assert resp.status_code == 200
    assert resp.json()["subscribers"] == 1
    event = queue.get_nowait()
    assert event.type == "notification"
    assert event.data["target"] == {"page": "settings", "conversation_id": None}
