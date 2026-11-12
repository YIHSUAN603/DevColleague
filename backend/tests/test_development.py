"""第三階段的資料揭露、唯讀 Git 與平台 API 合約測試。"""

import json
import subprocess
import time

import httpx
import pytest

from devcolleague.api.development import PreviewRequest
from devcolleague.integrations.azure_devops import AzureDevOpsIntegration
from devcolleague.integrations.base import ConnectionInfo
from devcolleague.integrations.github import GitHubIntegration
from devcolleague.llm.types import ChatEvent, ProviderCapabilities
from devcolleague.services.local_repo import branch_diff, inspect_repo, read_files, resolve_commit
from .conftest import FakeProvider


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    def run(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "測試")
    (path / "code.py").write_text("answer = 1\n", encoding="utf-8")
    (path / ".env").write_text("SECRET=private", encoding="utf-8")
    run("add", "code.py", ".env")
    run("commit", "-m", "測試基準")
    run("checkout", "-b", "feature")
    (path / "code.py").write_text("answer = 2\n", encoding="utf-8")
    run("add", "code.py")
    run("commit", "-m", "更新答案")
    return str(path)


async def test_local_repo_branches_diff_and_file_boundary(repo):
    info = await inspect_repo(repo)
    assert {"main", "feature"} <= set(info["branches"])
    assert ".env" not in info["files"]
    diff = await branch_diff(repo, "feature", "main")
    assert "-answer = 1" in diff["diff"] and "+answer = 2" in diff["diff"]
    assert "更新答案" in diff["commits"]
    assert (await read_files(repo, ["code.py"]))["code.py"].splitlines() == ["answer = 2"]
    for file in (".env", "../outside.py", ".git/config"):
        with pytest.raises(ValueError):
            await read_files(repo, [file])
    with pytest.raises(ValueError):
        await resolve_commit(repo, "--help")
    with pytest.raises(ValueError):
        await resolve_commit(repo, "missing")


async def test_local_repo_rejects_tracked_symlink_escape(repo, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("private", encoding="utf-8")
    from pathlib import Path
    file = Path(repo) / "code.py"
    file.unlink()
    try:
        file.symlink_to(outside)
    except OSError:
        pytest.skip("此 Windows 帳號未開放建立符號連結")
    with pytest.raises(ValueError, match="超出 Repo"):
        await read_files(repo, ["code.py"])


async def test_preview_is_immutable_and_does_not_call_llm(container, repo, monkeypatch):
    fake = FakeProvider([[ChatEvent.text("結果"), ChatEvent("done", {"stop_reason": "end_turn"})]])
    monkeypatch.setattr(container.providers, "build", lambda _: (fake, None))
    body = PreviewRequest(mode="review", root=repo, source_branch="feature", target_branch="main", rules="檢查邊界")
    preview = await container.development.preview(body)
    assert not fake.requests
    assert "檢查邊界" in preview["payload"]
    body.rules = "更動後的規範"
    events = [e async for e in container.development.generate(preview["id"])]
    assert events[-1].type == "done"
    request = fake.requests[0]
    assert "更動後的規範" not in request.messages[0].content
    assert request.tools == [] and request.cwd is None
    container.development.previews[preview["id"]]["expires"] = time.monotonic() - 1
    assert [e.type async for e in container.development.generate(preview["id"])] == ["error"]


async def test_workspace_requires_capability_and_explicit_opt_in(container, repo, monkeypatch):
    fake = FakeProvider([[ChatEvent("done", {"stop_reason": "end_turn"})]])
    monkeypatch.setattr(container.providers, "build", lambda _: (fake, None))
    body = PreviewRequest(mode="question", root=repo, workspace_access=True)
    with pytest.raises(ValueError, match="不支援本機"):
        await container.development.preview(body)
    fake.capabilities = ProviderCapabilities(custom_tools=False, local_workspace=True, streaming=True)
    preview = await container.development.preview(body)
    assert preview["workspace_access"] and "其他檔案" in preview["payload"]
    _ = [e async for e in container.development.generate(preview["id"])]
    assert fake.requests[0].cwd.replace("\\", "/").lower() == repo.replace("\\", "/").lower()


async def test_cli_without_workspace_uses_empty_temporary_directory(container, repo, monkeypatch):
    from pathlib import Path
    fake = FakeProvider([[ChatEvent("done", {"stop_reason": "end_turn"})]])
    fake.capabilities = ProviderCapabilities(custom_tools=False, local_workspace=True, streaming=True)
    monkeypatch.setattr(container.providers, "build", lambda _: (fake, None))
    preview = await container.development.preview(PreviewRequest(mode="question", root=repo, files=["code.py"]))
    assert not preview["workspace_access"]
    events = container.development.generate(preview["id"])
    await anext(events)
    temporary = Path(fake.requests[0].cwd)
    assert temporary.is_dir() and not list(temporary.iterdir())
    await events.aclose()
    assert not temporary.exists()


@pytest.mark.parametrize("source,mode", [("local", "diagnose"), ("platform", "pr"), ("local", "summary")])
def test_invalid_source_mode_is_rejected(client, source, mode):
    response = client.post("/api/development/preview", json={"source": source, "mode": mode})
    assert response.status_code == 400 and "不支援" in response.json()["detail"]


async def test_preview_redacts_credentials_and_marks_truncation(container, monkeypatch):
    secret = "a-private-platform-token"
    connection = container.connections.create(kind="github", name="測試", base_url="", scopes=[], token=secret)
    async def platform(*args):
        return secret + "x" * 170000
    monkeypatch.setattr(container.development, "platform", platform)
    preview = await container.development.preview(PreviewRequest(
        mode="diagnose", source="platform", connection_id=connection.id, repo="org/repo"))
    assert secret not in preview["payload"] and "已遮蔽憑證" in preview["payload"]
    assert preview["truncated"] and "已截斷" in preview["payload"]


def test_publish_requires_session_confirmation_and_valid_content(client, container, mock_handler):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(201, json={"number": 12, "html_url": "https://github.com/org/repo/pull/12"})
    mock_handler["fn"] = handler
    connection = container.connections.create(kind="github", name="測試", base_url="", scopes=[], token="private")
    body = {"action": "create", "connection_id": connection.id, "repo": "org/repo", "title": "標題",
            "content": "說明", "source_branch": "feature", "target_branch": "main"}
    assert client.post("/api/development/publish", json=body).status_code == 422
    assert client.post("/api/development/publish", json={**body, "confirmed": False}).status_code == 422
    assert not calls
    assert client.post("/api/development/publish", json={**body, "confirmed": True, "content": " "}).status_code == 400
    response = client.post("/api/development/publish", json={**body, "confirmed": True})
    assert response.status_code == 200 and response.json()["id"] == "12"
    assert json.loads(calls[0].content) == {"head": "feature", "base": "main", "title": "標題", "body": "說明"}
    client.headers.clear()
    assert client.post("/api/development/publish", json={**body, "confirmed": True}).status_code == 401


async def test_github_detail_comment_and_failed_job_log():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/pulls/7"):
            if req.headers["accept"] == "application/vnd.github.diff":
                return httpx.Response(200, text="diff --git a/code.py b/code.py")
            return httpx.Response(200, json={"title": "PR", "body": "說明", "head": {"ref": "feature"},
                                            "base": {"ref": "main"}, "html_url": "https://github.com/o/r/pull/7"})
        if req.url.path.endswith("/comments"):
            return httpx.Response(201, json={"id": 88, "html_url": "https://github.com/o/r/pull/7#comment"})
        if req.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 42, "name": "測試", "conclusion": "failure"},
                                                       {"id": 43, "conclusion": "success"}]})
        if req.url.path.endswith("/jobs/42/logs"):
            return httpx.Response(200, text="ERROR code.py:2")
        return httpx.Response(404)
    integration = GitHubIntegration(ConnectionInfo("gh", "github", "測試", "https://api.github.com"),
                                    "secret", transport=httpx.MockTransport(handler))
    try:
        detail = await integration.pull_request_detail("", "o/r", 7)
        assert detail.source_branch == "feature" and "diff --git" in detail.diff
        assert (await integration.comment_pull_request("", "o/r", 7, "審查"))["id"] == "88"
        assert "ERROR code.py:2" in await integration.build_log("", "o/r", 55)
        assert not any("/43/logs" in str(req.url) for req in calls)
    finally:
        await integration.aclose()


async def test_azure_diff_create_comment_and_logs():
    calls = []
    def handler(req):
        calls.append(req)
        path = req.url.path
        if path.endswith("/pullrequests/7"):
            return httpx.Response(200, json={"title": "PR", "sourceRefName": "refs/heads/feature",
                "targetRefName": "refs/heads/main", "lastMergeTargetCommit": {"commitId": "target"},
                "lastMergeSourceCommit": {"commitId": "head"}})
        if path.endswith("/diffs/commits"):
            return httpx.Response(200, json={"commonCommit": "common", "allChangesIncluded": True,
                "changes": [{"changeType": "edit", "item": {"path": "/code.py"}}]})
        if path.endswith("/items"):
            version = req.url.params["versionDescriptor.version"]
            return httpx.Response(200, json={"content": "old\n" if version == "common" else "new\n"})
        if path.endswith("/pullrequests"):
            return httpx.Response(201, json={"pullRequestId": 9})
        if path.endswith("/threads"):
            return httpx.Response(201, json={"id": 10})
        if path.endswith("/logs"):
            return httpx.Response(200, json={"value": [{"id": 1}]})
        if path.endswith("/logs/1"):
            return httpx.Response(200, text="error CS1234 /code.cs:10")
        return httpx.Response(404)
    integration = AzureDevOpsIntegration(ConnectionInfo("ads", "ado", "測試", "https://ads/collection"),
                                         "secret", transport=httpx.MockTransport(handler))
    try:
        detail = await integration.pull_request_detail("My Project", "repo", 7)
        assert "-old" in detail.diff and "+new" in detail.diff
        assert "/My%20Project/" in str(calls[0].url)
        assert (await integration.create_pull_request("My Project", "repo", "feature", "main", "標題", "說明"))["id"] == "9"
        create = next(req for req in calls if req.method == "POST")
        assert json.loads(create.content)["sourceRefName"] == "refs/heads/feature"
        assert (await integration.comment_pull_request("My Project", "repo", 7, "審查"))["id"] == "10"
        assert "error CS1234" in await integration.build_log("My Project", "repo", 5)
    finally:
        await integration.aclose()


async def test_github_log_redirect_does_not_forward_token(monkeypatch):
    def handler(req):
        if req.url.path.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": [{"id": 1, "name": "失敗", "conclusion": "failure"}]})
        return httpx.Response(302, headers={"Location": "https://logs.example.test/signed"})
    integration = GitHubIntegration(ConnectionInfo("gh", "github", "測試", "https://api.github.com"),
                                    "private-token", transport=httpx.MockTransport(handler))
    downloaded = []
    def download(req):
        downloaded.append(req)
        return httpx.Response(200, text="ERROR example.py:4")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(download), **kwargs))
    try:
        assert "ERROR example.py:4" in await integration.build_log("", "o/r", 1)
        assert len(downloaded) == 1 and "authorization" not in downloaded[0].headers
    finally:
        await integration.aclose()


async def test_development_tools_are_read_only_redacted_and_audited(container, monkeypatch):
    from devcolleague.integrations.development import PullRequestDetail
    from devcolleague.tools.context import ExecutionContext, execution_context
    secret = "private-platform-token"
    conn = container.connections.create(kind="github", name="測試工具", base_url="", scopes=[], token=secret)
    calls = []
    async def platform(connection, method, *args):
        calls.append((connection, method, args))
        if method == "pull_request_detail":
            return PullRequestDetail("1", "測試", "", "feature", "main", "diff " + secret)
        return "ERROR " + secret
    monkeypatch.setattr(container.development, "platform", platform)
    context = ExecutionContext(unattended=True)
    token = execution_context.set(context)
    try:
        for name in ("get_pull_request_diff", "get_build_log"):
            assert container.tools.get(name).risk == "read_only"
            result, error = await container.tools.execute(name, {"connection": conn.name, "repo": "o/r", "number": 1})
            assert not error and secret not in result and "已遮蔽憑證" in result
        assert len(context.audit) == 2 and len(calls) == 2
        assert all(call[0] == conn.id for call in calls)
        _, error = await container.tools.execute("get_build_log", {"connection": conn.id, "number": -1})
        assert error and len(calls) == 2
        assert not container.tools.get("create_pull_request") and not container.tools.get("comment_pull_request")
    finally:
        execution_context.reset(token)
