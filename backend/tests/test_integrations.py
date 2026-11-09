from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import httpx
import pytest

from devcolleague.integrations.azure_devops import AzureDevOpsIntegration
from devcolleague.integrations.base import ConnectionInfo, IntegrationError
from devcolleague.integrations.github import GitHubIntegration

ME_ID = "11111111-aaaa"


def ado_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    assert req.headers["Authorization"] == "Basic " + base64.b64encode(b":pat").decode()
    if path.endswith("/_apis/connectionData"):
        return httpx.Response(200, json={"authenticatedUser": {"id": ME_ID, "providerDisplayName": "王小明"}})
    if path.endswith("/_apis/projects"):
        return httpx.Response(200, json={"value": [{"name": "Proj A"}, {"name": "NoAccess"}]})
    if "NoAccess" in path:
        return httpx.Response(403)
    if path.endswith("/_apis/git/pullrequests"):
        params = req.url.params
        if params.get("searchCriteria.reviewerId") == ME_ID:
            author = "李大華"
        else:
            assert params.get("searchCriteria.creatorId") == ME_ID
            author = "王小明"
        return httpx.Response(200, json={"value": [{
            "pullRequestId": 42,
            "title": "新增登入功能",
            "status": "active",
            "isDraft": False,
            "creationDate": "2026-11-01T02:00:00Z",
            "sourceRefName": "refs/heads/feature/login",
            "targetRefName": "refs/heads/develop",
            "createdBy": {"displayName": author},
            "repository": {"name": "web", "project": {"name": "Proj A"}},
        }]})
    if path.endswith("/_apis/wit/wiql"):
        query = json.loads(req.content)["query"]
        assert "@Me" in query
        return httpx.Response(200, json={"workItems": [{"id": 7}]})
    if path.endswith("/_apis/wit/workitems"):
        return httpx.Response(200, json={"value": [{"id": 7, "fields": {
            "System.Title": "修正報表錯誤",
            "System.State": "Active",
            "System.WorkItemType": "Bug",
            "System.TeamProject": "Proj A",
            "System.AssignedTo": {"displayName": "王小明"},
            "System.ChangedDate": "2026-11-08T03:00:00Z",
        }}]})
    if path.endswith("/_apis/build/builds"):
        return httpx.Response(200, json={"value": [{
            "id": 99, "buildNumber": "20261108.1", "definition": {"name": "CI"},
            "status": "completed", "result": "failed", "sourceBranch": "refs/heads/develop",
            "requestedFor": {"displayName": "王小明"}, "startTime": "2026-11-08T01:00:00Z",
            "_links": {"web": {"href": "https://ads.example.com/build/99"}},
        }]})
    return httpx.Response(404)


@pytest.fixture
def ado() -> AzureDevOpsIntegration:
    info = ConnectionInfo(id="c1", kind="ado", name="ADS", base_url="https://ads.example.com/DefaultCollection")
    return AzureDevOpsIntegration(info, "pat", transport=httpx.MockTransport(ado_handler))


async def test_ado_maps_unified_models(ado: AzureDevOpsIntegration) -> None:
    assert await ado.test_connection() == "王小明"

    prs = await ado.list_my_pull_requests()
    assert len(prs) == 1  # 無權限的專案被略過
    pr = prs[0]
    assert pr.platform == "ado"
    assert pr.source_branch == "feature/login"
    assert pr.url == "https://ads.example.com/DefaultCollection/Proj%20A/_git/web/pullrequest/42"

    reviews = await ado.list_review_requests()
    assert reviews[0].author == "李大華"

    items = await ado.list_assigned_work_items()
    assert items[0].type == "Bug"
    assert items[0].assigned_to == "王小明"

    builds = await ado.list_recent_builds()
    assert builds[0].result == "failed"
    assert builds[0].url == "https://ads.example.com/build/99"


async def test_ado_auth_error_message() -> None:
    info = ConnectionInfo(id="c1", kind="ado", name="ADS", base_url="https://ads.example.com/Col")
    ado = AzureDevOpsIntegration(info, "pat", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(IntegrationError, match="驗證失敗"):
        await ado.test_connection()


def github_handler(req: httpx.Request) -> httpx.Response:
    assert req.headers["Authorization"] == "Bearer ghp"
    if req.url.path == "/user":
        return httpx.Response(200, json={"login": "octocat"})
    if req.url.path == "/search/issues":
        q = req.url.params["q"]
        if "is:pr" in q:
            return httpx.Response(200, json={"items": [{
                "number": 5, "title": "Fix bug", "html_url": "https://github.com/o/r/pull/5",
                "state": "open", "draft": True, "user": {"login": "octocat"},
                "repository_url": "https://api.github.com/repos/o/r",
                "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-02T00:00:00Z",
                "pull_request": {"merged_at": None},
            }]})
        return httpx.Response(200, json={"items": [{
            "number": 9, "title": "Issue", "html_url": "https://github.com/o/r/issues/9", "state": "open",
            "repository_url": "https://api.github.com/repos/o/r", "labels": [{"name": "bug"}],
            "assignee": {"login": "octocat"}, "updated_at": "2026-11-02T00:00:00Z",
        }]})
    if req.url.path == "/user/repos":
        return httpx.Response(200, json=[{"full_name": "o/r"}])
    if req.url.path == "/repos/o/r/actions/runs":
        return httpx.Response(200, json={"workflow_runs": [{
            "id": 1, "name": "CI", "display_title": "Fix bug", "html_url": "https://github.com/o/r/actions/runs/1",
            "head_branch": "main", "status": "completed", "conclusion": "failure",
            "actor": {"login": "octocat"}, "run_started_at": "2026-11-02T00:00:00Z", "updated_at": "2026-11-02T00:05:00Z",
        }]})
    if req.url.path == "/search/commits":
        assert "author:octocat" in req.url.params["q"]
        return httpx.Response(200, json={"items": [{
            "sha": "abcdef123456", "html_url": "https://github.com/o/r/commit/abcdef1",
            "commit": {"message": "fix: bug", "author": {"date": "2026-11-02T00:00:00Z"}},
            "repository": {"full_name": "o/r"},
        }]})
    return httpx.Response(404)


async def test_github_maps_unified_models() -> None:
    info = ConnectionInfo(id="g1", kind="github", name="GH", base_url="https://api.github.com")
    gh = GitHubIntegration(info, "ghp", transport=httpx.MockTransport(github_handler))
    assert await gh.test_connection() == "octocat"

    prs = await gh.list_my_pull_requests()
    assert prs[0].repo == "o/r" and prs[0].is_draft and prs[0].status == "active"

    items = await gh.list_assigned_work_items()
    assert items[0].type == "Issue（bug）"

    builds = await gh.list_recent_builds()
    assert builds[0].result == "failed"

    since = datetime(2026, 11, 1, tzinfo=UTC)
    commits = await gh.list_my_commits(since, datetime(2026, 11, 3, tzinfo=UTC))
    assert commits[0].message == "fix: bug"
