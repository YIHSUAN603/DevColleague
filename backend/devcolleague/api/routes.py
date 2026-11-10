"""HTTP 路由。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import __version__
from ..container import Container
from ..integrations.base import ConnectionInfo, IntegrationError
from ..llm.providers.cli.base import CliProvider
from ..llm.registry import PROVIDER_KINDS
from ..llm.types import ChatMessage, ChatRequest
from ..services.daily_report import ReportProject, default_report_date
from ..services.events import NotificationTarget
from .deps import event_stream_response, get_container, sse_response, verify_session_token

router = APIRouter(prefix="/api", dependencies=[Depends(verify_session_token)])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"找不到{what}")


# ---------- 系統 ----------


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# ---------- 背景事件與通知（F16） ----------


@router.get("/events")
async def events(c: Container = Depends(get_container)):
    """長連線 SSE：送出例行流程完成、新提醒等背景事件。"""
    return event_stream_response(c.events)


@router.post("/notifications/test")
async def send_test_notification(c: Container = Depends(get_container)) -> dict[str, Any]:
    notification = c.notifications.notify(
        "DevColleague 測試通知",
        "系統通知運作正常，點擊可開啟設定頁。",
        NotificationTarget(page="settings"),
    )
    return {"id": notification.id, "subscribers": c.events.subscriber_count}


# ---------- 平台連線（F1） ----------


class ConnectionCreate(BaseModel):
    kind: Literal["ado", "github"]
    name: str = Field(min_length=1)
    base_url: str = ""
    scopes: list[str] = []
    token: str = Field(min_length=1)


class ConnectionUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    scopes: list[str] | None = None
    token: str | None = None


def _connection_dict(c: Container, info: ConnectionInfo) -> dict[str, Any]:
    return {**asdict(info), "has_token": c.connections.has_token(info.id)}


async def _test(c: Container, info: ConnectionInfo, token: str | None = None) -> dict[str, Any]:
    try:
        integration = c.connections.open(info, token)
    except IntegrationError as exc:
        return {"ok": False, "message": str(exc)}
    try:
        user = await integration.test_connection()
        return {"ok": True, "user": user, "message": f"連線成功，登入身分：{user}"}
    except IntegrationError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        await integration.aclose()


@router.get("/connections")
async def list_connections(c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return [_connection_dict(c, i) for i in c.connections.list()]


@router.post("/connections")
async def create_connection(body: ConnectionCreate, c: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        info = c.connections.create(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _connection_dict(c, info)


@router.post("/connections/test")
async def test_new_connection(body: ConnectionCreate, c: Container = Depends(get_container)) -> dict[str, Any]:
    """儲存前先測試連線。"""
    base_url = body.base_url or ("https://api.github.com" if body.kind == "github" else "")
    if not base_url:
        return {"ok": False, "message": "請填寫伺服器網址"}
    info = ConnectionInfo(id="test", kind=body.kind, name=body.name, base_url=base_url, scopes=body.scopes)
    return await _test(c, info, body.token)


@router.patch("/connections/{connection_id}")
async def update_connection(
    connection_id: str, body: ConnectionUpdate, c: Container = Depends(get_container)
) -> dict[str, Any]:
    try:
        info = c.connections.update(connection_id, **body.model_dump())
    except KeyError as exc:
        raise _not_found("連線") from exc
    return _connection_dict(c, info)


@router.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str, c: Container = Depends(get_container)) -> dict[str, bool]:
    if not c.connections.delete(connection_id):
        raise _not_found("連線")
    return {"ok": True}


@router.post("/connections/{connection_id}/test")
async def test_connection(connection_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    info = c.connections.get(connection_id)
    if info is None:
        raise _not_found("連線")
    return await _test(c, info)


# ---------- LLM Provider（F5） ----------


class ProviderCreate(BaseModel):
    kind: str
    name: str = Field(min_length=1)
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = None


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    api_key: str | None = None


class DefaultProvider(BaseModel):
    provider_id: str


def _provider_dict(c: Container, p: Any) -> dict[str, Any]:
    meta = PROVIDER_KINDS[p.kind]
    return {
        **p.to_dict(),
        "mode": meta.mode,
        "has_api_key": c.providers.has_api_key(p.id),
        "suggested_models": list(meta.suggested_models),
    }


@router.get("/providers")
async def list_providers(c: Container = Depends(get_container)) -> dict[str, Any]:
    return {
        "providers": [_provider_dict(c, p) for p in c.providers.list()],
        "default_provider_id": c.providers.default_provider_id(),
        "kinds": [{**asdict(k), "suggested_models": list(k.suggested_models)} for k in PROVIDER_KINDS.values()],
    }


@router.post("/providers")
async def create_provider(body: ProviderCreate, c: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        p = c.providers.create(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _provider_dict(c, p)


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: str, body: ProviderUpdate, c: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        p = c.providers.update(provider_id, **body.model_dump())
    except KeyError as exc:
        raise _not_found("Provider") from exc
    return _provider_dict(c, p)


@router.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str, c: Container = Depends(get_container)) -> dict[str, bool]:
    if not c.providers.delete(provider_id):
        raise _not_found("Provider")
    return {"ok": True}


@router.put("/providers/default")
async def set_default_provider(body: DefaultProvider, c: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        c.providers.set_default(body.provider_id)
    except KeyError as exc:
        raise _not_found("Provider") from exc
    return {"default_provider_id": body.provider_id}


@router.get("/providers/{provider_id}/status")
async def provider_status(provider_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    """CLI Provider 偵測是否安裝、是否登入；API Provider 檢查是否已設定 API Key。"""
    config = c.providers.get(provider_id)
    if config is None:
        raise _not_found("Provider")
    meta = PROVIDER_KINDS[config.kind]
    if meta.mode == "api":
        has_key = c.providers.has_api_key(provider_id)
        ready = has_key or meta.api_key_optional
        return {"ready": ready, "detail": "已設定 API Key" if has_key else ("未設定 API Key" if not ready else "不需要 API Key")}
    provider, _ = c.providers.build(provider_id)
    assert isinstance(provider, CliProvider)
    status = await provider.status()
    return {"ready": status.installed and status.logged_in is not False, **status.to_dict()}


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    """送出一個極短的請求確認 Provider 可用（會消耗少量 token）。"""
    try:
        provider, _ = c.providers.build(provider_id)
    except LookupError as exc:
        return {"ok": False, "message": str(exc)}
    request = ChatRequest(messages=[ChatMessage(role="user", content="請只回覆：OK")], max_tokens=1024)
    text = []
    async for event in c.gateway.run(provider, request, use_tools=False):
        if event.type == "text_delta":
            text.append(event.data["text"])
        elif event.type == "error":
            return {"ok": False, "message": event.data["message"]}
    return {"ok": True, "message": f"回應正常：{''.join(text).strip()[:100]}"}


# ---------- 統一儀表板（F2） ----------


@router.get("/dashboard")
async def dashboard(c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.dashboard.cached()


@router.post("/dashboard/refresh")
async def refresh_dashboard(c: Container = Depends(get_container)) -> dict[str, Any]:
    return await c.dashboard.refresh()


# ---------- AI 對話（F3） ----------


class ConversationCreate(BaseModel):
    title: str | None = None
    provider_id: str | None = None
    model: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    provider_id: str | None = None
    model: str | None = None


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    provider_id: str | None = None
    model: str | None = None
    cwd: str | None = None


@router.get("/conversations")
async def list_conversations(c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return c.conversations.list()


@router.post("/conversations")
async def create_conversation(body: ConversationCreate, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.conversations.create(**body.model_dump())


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    conv = c.conversations.get(conversation_id)
    if conv is None:
        raise _not_found("對話")
    return conv


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str, body: ConversationUpdate, c: Container = Depends(get_container)
) -> dict[str, Any]:
    conv = c.conversations.update(conversation_id, **body.model_dump(exclude_none=True))
    if conv is None:
        raise _not_found("對話")
    return conv


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, c: Container = Depends(get_container)) -> dict[str, bool]:
    if not c.conversations.delete(conversation_id):
        raise _not_found("對話")
    return {"ok": True}


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, body: MessageCreate, c: Container = Depends(get_container)):
    if c.conversations.get(conversation_id) is None:
        raise _not_found("對話")
    return sse_response(
        c.conversations.send(
            conversation_id, body.content, provider_id=body.provider_id, model=body.model, cwd=body.cwd
        )
    )


# ---------- 工作摘要（F4） ----------


class SummaryRequest(BaseModel):
    range: Literal["today", "yesterday", "this_week", "custom"] = "yesterday"
    report: Literal["standup", "weekly"] = "standup"
    start: date | None = None
    end: date | None = None
    provider_id: str | None = None
    model: str | None = None


@router.post("/summary")
async def generate_summary(body: SummaryRequest, c: Container = Depends(get_container)):
    return sse_response(
        c.summary.generate(
            body.range,
            body.report,
            start=body.start,
            end=body.end,
            provider_id=body.provider_id,
            model=body.model,
        )
    )


# ---------- 日報告（F15） ----------


class DailyReportSettings(BaseModel):
    projects: list[ReportProject] = []


class DailyReportSave(BaseModel):
    content: str = ""
    notes: str = ""
    status: Literal["draft", "final"] = "final"


class DailyReportRequest(BaseModel):
    day: date
    notes: str = ""
    provider_id: str | None = None
    model: str | None = None


@router.get("/daily-report/settings")
async def get_daily_report_settings(c: Container = Depends(get_container)) -> dict[str, Any]:
    return {
        "projects": [p.model_dump() for p in c.daily_report.projects()],
        "default_date": default_report_date(datetime.now()).isoformat(),
    }


@router.put("/daily-report/settings")
async def set_daily_report_settings(body: DailyReportSettings, c: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        projects = c.daily_report.set_projects(body.projects)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"projects": [p.model_dump() for p in projects]}


@router.get("/daily-report/sources")
async def list_daily_report_sources(c: Container = Depends(get_container)) -> dict[str, Any]:
    """可供日報告專案對應的平台專案清單。"""
    return await c.daily_report.sources()


@router.get("/daily-report/reports/{day}")
async def get_daily_report(day: date, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.daily_report.get(day) or {"date": day.isoformat(), "content": "", "notes": "", "updated_at": None}


@router.put("/daily-report/reports/{day}")
async def save_daily_report(day: date, body: DailyReportSave, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.daily_report.save(day, body.content, body.notes, body.status)


@router.post("/daily-report/generate")
async def generate_daily_report(body: DailyReportRequest, c: Container = Depends(get_container)):
    return sse_response(
        c.daily_report.generate(body.day, body.notes, provider_id=body.provider_id, model=body.model)
    )
