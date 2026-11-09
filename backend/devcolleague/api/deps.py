"""API 共用相依：session token 驗證、取得元件、SSE 輸出。"""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass

from fastapi import Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..container import Container
from ..llm.types import ChatEvent


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
    return f"event: {event.type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


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
