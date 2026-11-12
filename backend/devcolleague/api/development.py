"""開發助手 API，外部寫入不提供給 LLM 工具。"""

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..container import Container
from ..integrations.base import IntegrationError
from ..services.local_repo import inspect_repo
from .deps import get_container, sse_response, verify_session_token

router = APIRouter(prefix="/api/development", dependencies=[Depends(verify_session_token)])


class RepoRequest(BaseModel):
    root: str = Field(min_length=1)


class PreviewRequest(BaseModel):
    mode: Literal["pr", "summary", "review", "diagnose", "question", "impact"]
    source: Literal["local", "platform"] = "local"
    root: str = ""
    source_branch: str = ""
    target_branch: str = ""
    connection_id: str = ""
    project: str = ""
    repo: str = ""
    number: int = Field(default=1, ge=1)
    files: list[str] = Field(default_factory=list, max_length=30)
    question: str = Field(default="", max_length=10000)
    rules: str = Field(default="", max_length=30000)
    workspace_access: bool = False
    provider_id: str | None = None
    model: str | None = None


class GenerateRequest(BaseModel):
    preview_id: str


class PublishRequest(BaseModel):
    action: Literal["create", "comment"]
    confirmed: Literal[True]
    connection_id: str
    project: str = ""
    repo: str = Field(min_length=1)
    number: int = Field(default=1, ge=1)
    source_branch: str = ""
    target_branch: str = ""
    title: str = Field(default="", max_length=250)
    content: str = Field(min_length=1, max_length=65000)


async def checked(awaitable):
    try:
        return await awaitable
    except (ValueError, LookupError, OSError, IntegrationError, httpx.HTTPError, NotImplementedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/repo")
async def repo(body: RepoRequest):
    return await checked(inspect_repo(body.root))


@router.post("/preview")
async def preview(body: PreviewRequest, c: Container = Depends(get_container)):
    return await checked(c.development.preview(body))


@router.post("/generate")
async def generate(body: GenerateRequest, c: Container = Depends(get_container)):
    return sse_response(c.development.generate(body.preview_id))


@router.post("/publish")
async def publish(body: PublishRequest, c: Container = Depends(get_container)):
    args = (body.project, body.repo)
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="發布內容不可為空白")
    if body.action == "create":
        if not body.title.strip() or not body.source_branch or not body.target_branch:
            raise HTTPException(status_code=400, detail="建立 PR 需要標題、來源及目標分支")
        return await checked(c.development.platform(body.connection_id, "create_pull_request", *args,
                             body.source_branch, body.target_branch, body.title, body.content))
    return await checked(c.development.platform(body.connection_id, "comment_pull_request", *args,
                         body.number, body.content))
