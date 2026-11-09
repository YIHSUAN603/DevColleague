from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from devcolleague.app import create_app
from devcolleague.config import Settings
from devcolleague.container import Container, build_container
from devcolleague.llm.types import ChatEvent, ChatRequest, ProviderCapabilities
from devcolleague.storage.db import Database
from devcolleague.storage.secrets import MemorySecretStore

TOKEN = "test-session-token-0123456789"


class FakeProvider:
    """依序回傳預先準備好的事件，並記錄收到的請求。"""

    def __init__(self, turns: list[list[ChatEvent]], custom_tools: bool = True) -> None:
        self.id = "fake"
        self.capabilities = ProviderCapabilities(custom_tools=custom_tools, local_workspace=False, streaming=True)
        self._turns = list(turns)
        self.requests: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        self.requests.append(request)
        for event in self._turns.pop(0):
            yield event


@pytest.fixture
def mock_handler() -> dict[str, Callable[[httpx.Request], httpx.Response]]:
    """測試可替換 handler['fn'] 來模擬平台 API。"""
    return {"fn": lambda req: httpx.Response(404)}


@pytest.fixture
def container(tmp_path: Path, mock_handler) -> Container:
    settings = Settings(session_token=TOKEN, data_dir=tmp_path)
    transport = httpx.MockTransport(lambda req: mock_handler["fn"](req))
    return build_container(
        settings,
        secrets=MemorySecretStore(),
        db=Database(":memory:"),
        transport_factory=lambda: transport,
    )


@pytest.fixture
def client(container: Container) -> TestClient:
    c = TestClient(create_app(container))
    c.headers["Authorization"] = f"Bearer {TOKEN}"
    return c
