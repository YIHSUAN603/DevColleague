"""連線管理：設定存 SQLite、Token 存 keyring，並依連線建立 Integration。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from ..storage.db import Database, utc_now
from ..storage.secrets import SecretStore, connection_token_key
from .azure_devops import AzureDevOpsIntegration
from .base import ConnectionInfo, Integration, IntegrationError
from .github import DEFAULT_BASE_URL as GITHUB_BASE_URL
from .github import GitHubIntegration

INTEGRATION_CLASSES: dict[str, type[Integration]] = {
    "ado": AzureDevOpsIntegration,
    "github": GitHubIntegration,
}


class ConnectionService:
    def __init__(
        self,
        db: Database,
        secrets: SecretStore,
        transport_factory: Callable[[], httpx.AsyncBaseTransport | None] = lambda: None,
    ) -> None:
        self._db = db
        self._secrets = secrets
        self._transport_factory = transport_factory

    @staticmethod
    def _row_to_info(row: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            base_url=row["base_url"],
            scopes=json.loads(row["scopes"] or "[]"),
        )

    def list(self) -> list[ConnectionInfo]:
        rows = self._db.fetch_all("SELECT * FROM connections ORDER BY created_at")
        return [self._row_to_info(r) for r in rows]

    def get(self, connection_id: str) -> ConnectionInfo | None:
        row = self._db.fetch_one("SELECT * FROM connections WHERE id = ?", (connection_id,))
        return self._row_to_info(row) if row else None

    def has_token(self, connection_id: str) -> bool:
        return bool(self._secrets.get(connection_token_key(connection_id)))

    def create(self, *, kind: str, name: str, base_url: str, scopes: list[str], token: str) -> ConnectionInfo:
        if kind not in INTEGRATION_CLASSES:
            raise ValueError(f"不支援的平台類型：{kind}")
        if not token:
            raise ValueError("Token 不可為空")
        if kind == "github" and not base_url:
            base_url = GITHUB_BASE_URL
        if not base_url:
            raise ValueError("伺服器網址不可為空")
        connection_id = uuid.uuid4().hex[:12]
        self._secrets.set(connection_token_key(connection_id), token)
        self._db.execute(
            "INSERT INTO connections(id, kind, name, base_url, scopes, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (connection_id, kind, name, base_url.rstrip("/"), json.dumps(scopes, ensure_ascii=False), utc_now()),
        )
        return self.get(connection_id)  # type: ignore[return-value]

    def update(
        self,
        connection_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        scopes: list[str] | None = None,
        token: str | None = None,
    ) -> ConnectionInfo:
        current = self.get(connection_id)
        if current is None:
            raise KeyError(connection_id)
        if token:
            self._secrets.set(connection_token_key(connection_id), token)
        self._db.execute(
            "UPDATE connections SET name = ?, base_url = ?, scopes = ? WHERE id = ?",
            (
                name if name is not None else current.name,
                (base_url if base_url is not None else current.base_url).rstrip("/"),
                json.dumps(scopes if scopes is not None else current.scopes, ensure_ascii=False),
                connection_id,
            ),
        )
        return self.get(connection_id)  # type: ignore[return-value]

    def delete(self, connection_id: str) -> bool:
        self._secrets.delete(connection_token_key(connection_id))
        return self._db.execute("DELETE FROM connections WHERE id = ?", (connection_id,)) > 0

    def open(self, info: ConnectionInfo, token: str | None = None) -> Integration:
        """建立 Integration；未提供 token 時從 keyring 讀取（提供時用於儲存前的連線測試）。"""
        token = token or self._secrets.get(connection_token_key(info.id))
        if not token:
            raise IntegrationError(f"連線「{info.name}」尚未設定 Token")
        cls = INTEGRATION_CLASSES[info.kind]
        return cls(info, token, transport=self._transport_factory())
