from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from devcolleague.app import create_app
from devcolleague.container import Container
from devcolleague.llm.types import ChatEvent

from .conftest import FakeProvider


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_requires_session_token(container: Container) -> None:
    raw = TestClient(create_app(container))
    assert raw.get("/api/health").status_code == 401
    assert raw.get("/api/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert raw.get("/api/health", headers={"Authorization": f"Bearer {container.settings.session_token}"}).status_code == 200


def test_cors_preflight_allows_tauri_origin(container: Container) -> None:
    raw = TestClient(create_app(container))
    resp = raw.options(
        "/api/health",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_connection_token_stored_in_secret_store_not_db(client: TestClient, container: Container) -> None:
    resp = client.post(
        "/api/connections",
        json={"kind": "ado", "name": "公司 ADS", "base_url": "https://ads.example.com/DefaultCollection/", "token": "pat-secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_token"] is True
    assert body["base_url"] == "https://ads.example.com/DefaultCollection"
    assert "token" not in body

    rows = container.db.fetch_all("SELECT * FROM connections")
    assert "pat-secret" not in json.dumps(rows)
    assert container.secrets.get(f"connection/{body['id']}/token") == "pat-secret"

    assert client.delete(f"/api/connections/{body['id']}").status_code == 200
    assert container.secrets.get(f"connection/{body['id']}/token") is None


def test_connection_test_endpoint(client: TestClient, mock_handler) -> None:
    mock_handler["fn"] = lambda req: httpx.Response(200, json={"login": "octocat"})
    resp = client.post("/api/connections/test", json={"kind": "github", "name": "GH", "token": "t"})
    assert resp.json() == {"ok": True, "user": "octocat", "message": "連線成功，登入身分：octocat"}

    mock_handler["fn"] = lambda req: httpx.Response(401)
    resp = client.post("/api/connections/test", json={"kind": "github", "name": "GH", "token": "bad"})
    assert resp.json()["ok"] is False


def test_provider_crud_and_default(client: TestClient, container: Container) -> None:
    resp = client.post("/api/providers", json={"kind": "anthropic", "name": "Claude", "api_key": "sk-test"})
    assert resp.status_code == 200
    claude = resp.json()
    assert claude["id"] == "claude"
    assert claude["has_api_key"] is True
    assert "sk-test" not in json.dumps(container.db.fetch_all("SELECT * FROM providers"))

    # 第一個 Provider 自動成為預設
    listing = client.get("/api/providers").json()
    assert listing["default_provider_id"] == "claude"
    assert {k["kind"] for k in listing["kinds"]} == {"anthropic", "openai", "claude_code", "codex"}

    cli = client.post("/api/providers", json={"kind": "claude_code", "name": "Claude Code"}).json()
    assert client.put("/api/providers/default", json={"provider_id": cli["id"]}).status_code == 200
    assert client.get("/api/providers").json()["default_provider_id"] == cli["id"]

    client.delete(f"/api/providers/{cli['id']}")
    assert client.get("/api/providers").json()["default_provider_id"] == "claude"

    assert client.post("/api/providers", json={"kind": "anthropic", "name": "x"}).status_code == 400


def test_chat_streams_and_persists(client: TestClient, container: Container, monkeypatch) -> None:
    client.post("/api/providers", json={"kind": "anthropic", "name": "Claude", "api_key": "sk-test"})
    fake = FakeProvider([[ChatEvent.text("你好"), ChatEvent.text("！"), ChatEvent("done", {"stop_reason": "end_turn", "usage": {}})]])
    original_build = container.providers.build
    monkeypatch.setattr(container.providers, "build", lambda pid=None: (fake, original_build(pid)[1]))

    conv = client.post("/api/conversations", json={}).json()
    resp = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "嗨，今天有什麼要做的？"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    assert [e[0] for e in events] == ["text_delta", "text_delta", "done"]

    saved = client.get(f"/api/conversations/{conv['id']}").json()
    assert saved["title"] == "嗨，今天有什麼要做的？"
    assert saved["provider_id"] == "claude"
    assert [(m["role"], m["content"]) for m in saved["messages"]] == [("user", "嗨，今天有什麼要做的？"), ("assistant", "你好！")]

    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 200
    assert client.get("/api/conversations").json() == []


def test_chat_without_provider_returns_error_event(client: TestClient) -> None:
    conv = client.post("/api/conversations", json={}).json()
    events = parse_sse(client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "hi"}).text)
    assert events[0][0] == "error"
    assert "Provider" in events[0][1]["message"]


def test_dashboard_cache_then_refresh(client: TestClient, mock_handler) -> None:
    def github(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/user":
            return httpx.Response(200, json={"login": "me"})
        if req.url.path == "/search/issues":
            return httpx.Response(200, json={"items": []})
        if req.url.path == "/user/repos":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    mock_handler["fn"] = github
    assert client.get("/api/dashboard").json() == {"data": None, "fetched_at": None}
    client.post("/api/connections", json={"kind": "github", "name": "GH", "token": "t"})
    refreshed = client.post("/api/dashboard/refresh").json()
    assert refreshed["data"]["errors"] == []
    cached = client.get("/api/dashboard").json()
    assert cached["fetched_at"] == refreshed["fetched_at"]
