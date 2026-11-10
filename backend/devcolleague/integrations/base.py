"""平台整合介面。每個平台以「連線」為單位實作同一組查詢方法。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from .models import Build, Commit, PullRequest, WorkItem, WorkItemProgress

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class IntegrationError(Exception):
    """平台 API 呼叫失敗；訊息可直接顯示給使用者。"""


@dataclass
class ConnectionInfo:
    id: str
    kind: str
    name: str
    base_url: str
    # ADS/TFS：要查詢的專案名稱；GitHub：要查詢建置的 repo（owner/name）。空清單表示自動判斷。
    scopes: list[str] = field(default_factory=list)


class Integration(ABC):
    def __init__(
        self,
        info: ConnectionInfo,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.info = info
        self._token = token
        self._transport = transport

    @abstractmethod
    async def test_connection(self) -> str:
        """測試連線，成功時回傳目前登入的使用者名稱。"""

    @abstractmethod
    async def list_my_pull_requests(self, *, updated_since: datetime | None = None) -> list[PullRequest]:
        """我建立的 PR。未指定 updated_since 時只回傳進行中的 PR。"""

    @abstractmethod
    async def list_review_requests(self) -> list[PullRequest]:
        """待我審查的進行中 PR。"""

    @abstractmethod
    async def list_assigned_work_items(self) -> list[WorkItem]:
        """指派給我、尚未關閉的 Work Item／Issue。"""

    @abstractmethod
    async def list_work_items_changed(self, since: datetime, until: datetime) -> list[WorkItem]:
        """在區間內被我異動或與我相關的 Work Item／Issue。"""

    @abstractmethod
    async def list_recent_builds(self, limit: int = 10) -> list[Build]:
        """最近的建置／Pipeline 結果。"""

    @abstractmethod
    async def list_my_commits(self, since: datetime, until: datetime) -> list[Commit]:
        """區間內我提交的 commit。"""

    async def list_mentions(self) -> list[WorkItem]:
        """近期提及使用者的項目。"""
        return []

    async def list_projects(self) -> list[str]:
        """可查詢的專案名稱（沒有專案概念的平台回傳 repo 名稱）。預設回傳空清單。"""
        return []

    async def list_work_item_progress(self, work_item_ids: list[str]) -> list[WorkItemProgress]:
        """依父子階層計算進度。

        傳入的 Work Item 若有子項目，以它本身為計算單位；否則以它的父項目為單位。
        平台沒有父子階層時回傳空清單（預設）。
        """
        return []

    async def aclose(self) -> None:  # noqa: B027 - 子類別可覆寫
        pass

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise IntegrationError(f"驗證失敗（HTTP {resp.status_code}），請確認 Token 是否有效且具備足夠權限")
        if resp.status_code == 404:
            raise IntegrationError(f"找不到資源（HTTP 404）：{resp.request.url}")
        if resp.status_code >= 400:
            raise IntegrationError(f"平台回應錯誤（HTTP {resp.status_code}）：{resp.text[:200]}")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
