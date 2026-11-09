"""統一資料模型：上層功能與 UI 只使用這些模型，不直接依賴各平台的原始格式。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Platform = Literal["ado", "github"]


class PullRequest(BaseModel):
    id: str
    platform: Platform
    connection_id: str
    title: str
    url: str
    project: str | None = None
    repo: str
    author: str
    status: str  # active / completed / abandoned / merged / closed
    is_draft: bool = False
    source_branch: str | None = None
    target_branch: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkItem(BaseModel):
    id: str
    platform: Platform
    connection_id: str
    title: str
    url: str
    project: str | None = None
    repo: str | None = None
    type: str  # Bug / Task / User Story / Issue…
    state: str
    assigned_to: str | None = None
    updated_at: datetime | None = None


class Build(BaseModel):
    id: str
    platform: Platform
    connection_id: str
    name: str
    url: str
    project: str | None = None
    repo: str | None = None
    branch: str | None = None
    status: str  # queued / in_progress / completed
    result: str | None = None  # succeeded / failed / canceled / partially_succeeded
    requested_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class Commit(BaseModel):
    sha: str
    platform: Platform
    connection_id: str
    message: str
    url: str
    project: str | None = None
    repo: str
    author: str
    date: datetime | None = None
