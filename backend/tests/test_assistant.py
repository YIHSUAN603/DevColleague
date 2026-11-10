from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from devcolleague.app import create_app
from devcolleague.integrations.models import Build, PullRequest
from devcolleague.llm.providers.cli.claude_code import ClaudeCodeProvider
from devcolleague.llm.providers.cli.codex import CodexProvider
from devcolleague.llm.types import ChatEvent, ChatRequest
from devcolleague.routines.definitions import Routine, RoutineStore
from devcolleague.services.activity import AggregateResult
from devcolleague.services.event_monitor import MonitorSettings
from devcolleague.services.memory import MemoryInput
from devcolleague.tools.base import Tool
from devcolleague.tools.context import ExecutionContext, execution_context

from .conftest import FakeProvider, TOKEN


def test_memory_search_update_delete_and_preferences(container):
    first = container.memory.save(MemoryInput(content="資料庫升級 SQLite 注意事項"))
    preference = container.memory.save(MemoryInput(kind="preference", content="偏好繁體中文"))
    assert container.memory.search("SQLite")[0]["id"] == first["id"]
    assert container.memory.search("資料庫")[0]["id"] == first["id"]
    assert "偏好繁體中文" in container.memory.context("不相關提問")
    container.memory.save(MemoryInput(content="週報"), memory_id=first["id"])
    assert container.memory.search("SQLite") == []
    assert container.memory.search("週報")[0]["id"] == first["id"]
    assert container.memory.delete(preference["id"])
    assert "偏好繁體中文" not in container.memory.context("不相關")
    assert container.memory.search("請查詢週報")
    with pytest.raises(ValueError):
        container.memory.save(MemoryInput(content=TOKEN))


def test_routine_markdown_roundtrip_and_validation(tmp_path):
    store = RoutineStore(tmp_path / "routines")
    routine = Routine(id="test", name="測試", prompt="今天 {{today}} / {{last_run_at}}", command="/test")
    store.save(routine)
    assert store.get("test") == routine
    assert "2026-11-10" in routine.render(datetime(2026, 11, 10), None)
    with pytest.raises(ValueError):
        store.save(Routine(id="other", name="重複指令", prompt="x", command="/test"))
    with pytest.raises(ValueError):
        Routine(id="../test", name="x", prompt="x")
    with pytest.raises(ValueError):
        Routine(id="test", name="x", prompt="x", schedule="61 99 * * *")
    store.delete("morning-brief")
    assert not any(r.id == "morning-brief" for r in RoutineStore(store.directory).list())


async def test_unattended_permissions_and_full_audit(container):
    called = []

    async def handler(args):
        called.append(args)
        return "結果"

    container.tools.register(Tool("external", "外部修改", {"type": "object"}, handler, "side_effect"))
    context = ExecutionContext(unattended=True)
    token = execution_context.set(context)
    try:
        assert (await container.tools.execute("external", {}))[1]
        assert (await container.tools.execute("save_routine", {}))[1]
        assert not called
        context.allow_side_effects = ("external",)
        assert await container.tools.execute("external", {}) == ("結果", False)
        assert len(context.audit) == 3
    finally:
        execution_context.reset(token)


def test_draft_never_overwrites_manual_content(container):
    day = date(2026, 11, 9)
    assert container.daily_report.save_draft(day, "初稿")["saved"]
    assert container.daily_report.get(day)["status"] == "draft"
    assert not container.daily_report.save_draft(day, "另一份草稿")["saved"]
    container.daily_report.save(day, "手動修改", "補充", "final")
    assert not container.daily_report.save_draft(day, "機器重寫")["saved"]
    assert container.daily_report.get(day)["content"] == "手動修改"


def test_proposal_requires_explicit_user_resolution(container, client):
    conv = container.conversations.create()
    routine = Routine(id="new-routine", name="每天整理", prompt="整理", schedule="0 8 * * *")
    proposal = container.routines.propose(routine, conv["id"])
    with pytest.raises(LookupError):
        container.routines.store.get(routine.id)
    response = client.post(f"/api/routine-proposals/{proposal['proposal_id']}/resolve", json={"approve": True})
    assert response.status_code == 200
    assert container.routines.store.get(routine.id).schedule == "0 8 * * *"
    assert client.post(f"/api/routine-proposals/{proposal['proposal_id']}/resolve", json={"approve": True}).status_code == 400


async def test_routine_executes_tools_persists_trace_and_conversation(container, monkeypatch):
    fake = FakeProvider([
        [ChatEvent("tool_call", {"id": "one", "name": "save_memory", "arguments": {"content": "已整理日報告", "kind": "work"}}),
         ChatEvent("done", {"stop_reason": "tool_use"})],
        [ChatEvent.text("已完成整理"), ChatEvent("done", {"stop_reason": "end_turn"})],
    ])
    monkeypatch.setattr(container.providers, "build", lambda _: (fake, SimpleNamespace(id="fake")))
    routine = Routine(id="test", name="測試", prompt="請整理 {{today}}", notify=True)
    run = container.routines.start(routine)
    with pytest.raises(ValueError):
        container.routines.start(routine)
    await asyncio.gather(*container.routines.tasks)
    result = container.routines.run_detail(run["id"])
    assert result["status"] == "succeeded"
    assert result["system_prompt"]
    assert result["events"]["tools"][0]["name"] == "save_memory"
    conv = container.conversations.get(run["conversation_id"])
    assert conv["messages"][-1]["content"] == "已完成整理"
    assert container.memory.search("日報告")
    queue = container.events.subscribe()
    assert queue.get_nowait().data["target"]["conversation_id"] == conv["id"]


async def test_scheduler_catchup_once_and_disabled_catchup(container, monkeypatch):
    now = datetime(2026, 11, 10, 10).astimezone()
    for routine in container.routines.store.list():
        container.routines.store.delete(routine.id)
    routine = Routine(id="daily", name="每日", prompt="x", schedule="30 8 * * 1-5")
    container.routines.store.save(routine)
    container.db.set_setting("routine.cursor.daily", (now - timedelta(days=5)).isoformat())
    calls = []
    monkeypatch.setattr(container.routines, "start", lambda *args: calls.append(args))
    await container.scheduler.tick(now)
    await container.scheduler.tick(now)
    assert len(calls) == 1
    container.routines.store.save(routine.model_copy(update={"catch_up": False}))
    container.db.set_setting("routine.cursor.daily", (now - timedelta(days=5)).isoformat())
    await container.scheduler.tick(now)
    assert len(calls) == 1


async def test_scheduler_uses_local_timezone_for_utc_cursor(container, monkeypatch):
    from datetime import UTC, timezone

    now = datetime(2026, 11, 10, 8, 30, 10, tzinfo=timezone(timedelta(hours=8)))
    routine = Routine(id="local", name="本機時間", prompt="x", schedule="30 8 * * *")
    container.routines.store.save(routine)
    cursor = (now - timedelta(minutes=1)).astimezone(UTC)
    container.db.set_setting("routine.cursor.local", cursor.isoformat())
    calls = []
    monkeypatch.setattr(container.routines, "start", lambda *args: calls.append(args))
    await container.scheduler.tick(now)
    assert len(calls) == 1
    assert calls[0][0].id == "local"


async def test_shutdown_persists_interrupted_run(container, monkeypatch):
    class SlowProvider:
        from devcolleague.llm.types import ProviderCapabilities
        capabilities = ProviderCapabilities(custom_tools=True, local_workspace=False, streaming=True)

        async def chat(self, request):
            await asyncio.sleep(60)
            yield ChatEvent.text("永遠不會到這裡")

    monkeypatch.setattr(container.providers, "build", lambda _: (SlowProvider(), SimpleNamespace(id="slow")))
    run = container.routines.start(Routine(id="slow", name="慢速", prompt="x"))
    await asyncio.sleep(0)
    await container.routines.close()
    assert container.routines.run_detail(run["id"])["status"] == "failed"
    assert not container.routines.active


async def test_event_deduplication_daily_cap_and_no_llm_without_changes(container, monkeypatch):
    now = datetime.now().astimezone()
    pr = PullRequest(id="1", platform="github", connection_id="gh", title="待審", repo="a/b",
                     author="a", status="active", url="https://example.com/pr/1", updated_at=now)
    build = Build(id="1", platform="github", connection_id="gh", name="失敗建置", url="https://example.com/build/1",
                  status="completed", result="failed")

    async def review(): return AggregateResult(items=[pr])
    async def builds(): return AggregateResult(items=[build])
    async def empty(): return AggregateResult()

    monkeypatch.setattr(container.activity, "review_requests", review)
    monkeypatch.setattr(container.activity, "recent_builds", builds)
    monkeypatch.setattr(container.activity, "my_pull_requests", empty)
    monkeypatch.setattr(container.activity, "mentions", empty)
    container.monitor.configure(MonitorSettings(enabled=True, daily_limit=1))
    calls = []
    monkeypatch.setattr(container.routines, "start", lambda *args: calls.append(args))
    assert (await container.monitor.poll(now))["events"] == 2
    assert len(calls) == 1
    assert (await container.monitor.poll(now))["events"] == 0
    assert len(calls) == 1
    pr.url = "https://example.com/pr/2"
    assert (await container.monitor.poll(now))["events"] == 1
    assert len(calls) == 1
    queue = container.events.subscribe()
    assert queue.get_nowait().data["title"] == "平台工作提醒"


async def test_review_request_reassignment_and_failed_connection_baseline(container, monkeypatch):
    item = PullRequest(id="1", platform="github", connection_id="gh", title="待審", repo="a/b",
                       author="a", status="active", url="https://example.com/pr/1")
    result = AggregateResult(items=[item])

    async def reviews(): return result
    async def empty(): return AggregateResult()

    monkeypatch.setattr(container.activity, "review_requests", reviews)
    for method in ("recent_builds", "my_pull_requests", "mentions"):
        monkeypatch.setattr(container.activity, method, empty)
    container.monitor.configure(MonitorSettings(enabled=True, daily_limit=0))
    assert (await container.monitor.poll())["events"] == 1
    result.items = []
    result.errors = [{"connection_id": "gh", "message": "暫時離線"}]
    assert (await container.monitor.poll())["events"] == 0
    result.items = [item]
    result.errors = []
    assert (await container.monitor.poll())["events"] == 0
    result.items = []
    await container.monitor.poll()
    result.items = [item]
    assert (await container.monitor.poll())["events"] == 1


async def test_github_mentions_use_actual_comment_timestamp():
    import httpx
    from devcolleague.integrations.base import ConnectionInfo
    from devcolleague.integrations.github import GitHubIntegration

    def handler(request):
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "me"})
        if request.url.path == "/search/issues":
            assert "@today" not in request.url.params["q"]
            return httpx.Response(200, json={"items": [{"number": 1, "title": "題目", "state": "open",
                "repository_url": "https://api.github.com/repos/a/b", "html_url": "https://github.com/a/b/issues/1"}]})
        return httpx.Response(200, json=[{"id": 20, "body": "@me 幫忙看看", "html_url": "https://github.com/a/b/issues/1#issuecomment-20",
            "updated_at": "2026-11-09T08:00:00Z"}, {"id": 21, "body": "@member 其他人", "updated_at": "2026-11-10T08:00:00Z"}])

    integration = GitHubIntegration(ConnectionInfo(id="gh", kind="github", name="gh", base_url="https://api.github.com"),
                                    "token", transport=httpx.MockTransport(handler))
    try:
        result = await integration.list_mentions()
        assert len(result) == 1
        assert result[0].id == "1:comments:20"
        assert result[0].updated_at.day == 9
    finally:
        await integration.aclose()


async def test_ado_mentions_use_comment_identity_and_pagination(monkeypatch):
    from devcolleague.integrations.azure_devops import AzureDevOpsIntegration
    from devcolleague.integrations.base import ConnectionInfo
    from devcolleague.integrations.models import WorkItem

    integration = AzureDevOpsIntegration(ConnectionInfo(id="ado", kind="ado", name="ado", base_url="https://ado.example"), "token")
    item = WorkItem(id="10", platform="ado", connection_id="ado", title="工作", url="https://ado.example/10", type="Task", state="Active")
    requests = []

    async def me(): return {"id": "user-id"}
    async def query(where): return [item]
    async def pr_mentions(identity): return []
    async def request(method, path, **kwargs):
        requests.append(kwargs["params"])
        if kwargs["params"].get("continuationToken"):
            return {"comments": [{"id": 2, "mentions": [{"targetId": "USER-ID"}], "modifiedDate": "2026-11-09T08:00:00Z"}]}
        return {"comments": [{"id": 1, "mentions": [{"targetId": "someone-else"}]}], "continuationToken": "next"}

    monkeypatch.setattr(integration, "_get_me", me)
    monkeypatch.setattr(integration, "_query_work_items", query)
    monkeypatch.setattr(integration, "_pr_mentions", pr_mentions)
    monkeypatch.setattr(integration, "_request", request)
    try:
        result = await integration.list_mentions()
        assert len(result) == 1 and result[0].id == "10:2"
        assert len(requests) == 2
    finally:
        await integration.aclose()


async def test_ado_pr_thread_mentions(monkeypatch):
    from devcolleague.integrations.azure_devops import AzureDevOpsIntegration
    from devcolleague.integrations.base import ConnectionInfo

    integration = AzureDevOpsIntegration(ConnectionInfo(id="ado", kind="ado", name="ado", base_url="https://ado.example"), "token")
    pr = PullRequest(id="1", platform="ado", connection_id="ado", title="程式碼審查", repo="repo", project="project",
                     author="a", status="active", url="https://ado.example/pr/1")

    async def search(criteria): return [pr]
    async def request(method, path):
        assert path.endswith("/pullRequests/1/threads")
        return {"value": [{"id": 10, "comments": [{"id": 1, "content": "@<USER-ID> 請看這行"},
            {"id": 2, "content": "@<another>"}, {"id": 3, "content": "@<user-id>", "isDeleted": True}]}]}

    monkeypatch.setattr(integration, "_search_prs", search)
    monkeypatch.setattr(integration, "_request", request)
    try:
        mentions = await integration._pr_mentions("user-id")
        assert len(mentions) == 1
        assert mentions[0].id == "pr:1:10:1"
        assert "discussionId=10" in mentions[0].url
    finally:
        await integration.aclose()


async def test_github_inline_review_mentions(monkeypatch):
    from devcolleague.integrations.base import ConnectionInfo
    from devcolleague.integrations.github import GitHubIntegration

    integration = GitHubIntegration(ConnectionInfo(id="gh", kind="github", name="gh", base_url="https://api.github.com"), "token")
    async def me(): return "me"
    async def search(query):
        return [{"number": 1, "title": "PR", "state": "open", "pull_request": {},
                 "repository_url": "https://api.github.com/repos/a/b"}]
    async def get(path, params):
        if "/pulls/" in path:
            return [{"id": 10, "body": "@me 看一下", "updated_at": datetime.now().astimezone().isoformat(),
                     "html_url": "https://github.com/a/b/pull/1#comment-10"}]
        return []
    monkeypatch.setattr(integration, "_me", me)
    monkeypatch.setattr(integration, "_search_issues", search)
    monkeypatch.setattr(integration, "_get", get)
    try:
        mentions = await integration.list_mentions()
        assert {m.id for m in mentions} == {"1:comments:10", "1:reviews:10"}
    finally:
        await integration.aclose()


async def test_slow_event_poll_does_not_block_routine_scheduler(container, monkeypatch):
    started = asyncio.Event()
    async def poll(now):
        started.set()
        await asyncio.sleep(60)
    container.monitor.configure(MonitorSettings(enabled=True))
    monkeypatch.setattr(container.monitor, "poll", poll)
    await asyncio.wait_for(container.scheduler.tick(), 0.5)
    await asyncio.wait_for(started.wait(), 0.5)
    assert container.scheduler.monitor_task and not container.scheduler.monitor_task.done()
    await container.scheduler.close()


async def test_legacy_tfs_mentions_fall_back_to_work_item_updates(monkeypatch):
    from devcolleague.integrations.azure_devops import AzureDevOpsIntegration
    from devcolleague.integrations.base import ConnectionInfo, IntegrationError
    from devcolleague.integrations.models import WorkItem

    integration = AzureDevOpsIntegration(ConnectionInfo(id="tfs", kind="ado", name="tfs", base_url="https://tfs.example"), "token")
    item = WorkItem(id="1", platform="ado", connection_id="tfs", title="舊版工作項目", url="https://tfs.example/1", type="Task", state="Active")
    async def me(): return {"id": "my-id"}
    async def items(where): return [item]
    async def prs(identity): return []
    async def request(method, path, **kwargs):
        if path.endswith("/comments"):
            raise IntegrationError("找不到資源（HTTP 404）")
        return {"value": [{"id": 2, "revisedDate": "2026-11-10T08:00:00Z",
                          "fields": {"System.History": {"newValue": '<a data-vss-mention="version:2.0,my-id">@我</a>'}}}]}
    monkeypatch.setattr(integration, "_get_me", me)
    monkeypatch.setattr(integration, "_query_work_items", items)
    monkeypatch.setattr(integration, "_pr_mentions", prs)
    monkeypatch.setattr(integration, "_request", request)
    try:
        result = await integration.list_mentions()
        assert len(result) == 1 and result[0].id == "1:update:2"
    finally:
        await integration.aclose()


def test_malformed_routine_does_not_disable_valid_schedules(tmp_path):
    store = RoutineStore(tmp_path / "routines")
    (store.directory / "broken.md").write_text("這不是有效的 frontmatter", encoding="utf-8")
    assert len(store.list()) == 3
    assert "broken.md" in store.errors[0]
    store.save(Routine(id="valid", name="正常流程", prompt="正常提示"))
    assert store.get("valid").prompt == "正常提示"


def test_cli_mcp_args_never_include_credentials():
    request = ChatRequest(messages=[], mcp_url="http://127.0.0.1:8765/mcp", mcp_token="secret-value")
    claude = ClaudeCodeProvider("claude").build_args("claude", request)
    codex = CodexProvider("codex").build_args("codex", request)
    assert "secret-value" not in " ".join(claude + codex)
    assert "--strict-mcp-config" in claude
    assert "mcp__devcolleague__*" in claude
    assert "features.shell_tool=false" in codex
    assert "bearer_token_env_var" in " ".join(codex)
    assert "--ignore-user-config" in codex


def test_mcp_auth_lifecycle_and_unattended_policy(container):
    app = create_app(container)
    context = ExecutionContext(unattended=True)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.post("/mcp", json={}).status_code == 401
        with container.mcp.grant(context) as (_, credential):
            headers = {"Authorization": f"Bearer {credential}", "Accept": "application/json, text/event-stream"}
            response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
            assert response.status_code == 200
            assert response.json()["result"]["serverInfo"]["name"] == "DevColleague"
            response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert "save_routine" in [t["name"] for t in response.json()["result"]["tools"]]
            response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "save_routine", "arguments": {"id": "evil", "name": "x", "prompt": "x"}}})
            assert response.json()["result"]["isError"]
            assert context.audit[0]["name"] == "save_routine"
        assert client.post("/mcp", headers=headers, json={}).status_code == 401
