"""API 共用相依：session token 驗證、取得元件、SSE 輸出。"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass

from fastapi import Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..container import Container
from ..llm.types import ChatEvent
from ..services.events import EventBus

# 背景事件通道的心跳間隔（秒），讓前端能察覺連線中斷
EVENT_HEARTBEAT_SECONDS = 15.0


def get_container(request: Request) -> Container:
    return request.app.state.container


async def verify_session_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    """所有 API 都必須帶 `Authorization: Bearer <session token>`。"""
    expected = get_container(request).settings.session_token
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session token 無效")


def _encode(event: ChatEvent) -> str:
    data = event.data if not is_dataclass(event.data) else asdict(event.data)
    return _format_sse(event.type, data)


def _format_sse(event_type: str, data: object) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def sse_response(events: AsyncIterator[ChatEvent]) -> StreamingResponse:
    async def body() -> AsyncIterator[str]:
        try:
            async for event in events:
                yield _encode(event)
        except Exception as exc:  # 未預期錯誤以 error 事件回報，避免串流無聲中斷
            yield _encode(ChatEvent.error(f"伺服器錯誤：{exc}"))

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def event_stream(bus: EventBus, heartbeat: float = EVENT_HEARTBEAT_SECONDS) -> AsyncIterator[str]:
    """持續送出背景事件；沒有事件時定期送出 SSE 註解作為心跳。"""
    queue = bus.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            yield _format_sse(event.type, event.data)
    finally:
        bus.unsubscribe(queue)


def event_stream_response(bus: EventBus) -> StreamingResponse:
    return StreamingResponse(
        event_stream(bus),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
