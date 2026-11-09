"""ADS／TFS（Azure DevOps Server、Team Foundation Server）整合。

base_url 為 Collection 網址，例如：
- https://devops.example.com/DefaultCollection
- http://192.168.0.143:8080/tfs/DefaultCollection
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from .base import DEFAULT_TIMEOUT, Integration, IntegrationError, parse_dt
from .models import Build, Commit, PullRequest, WorkItem

API_VERSION = "6.0"
MAX_PROJECTS = 30
WORK_ITEM_FIELDS = [
    "System.Id",
    "System.Title",
    "System.State",
    "System.WorkItemType",
    "System.TeamProject",
    "System.AssignedTo",
    "System.ChangedDate",
]
CLOSED_STATES = ("Closed", "Done", "Removed", "Resolved", "Completed")


def _wiql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _strip_ref(ref: str | None) -> str | None:
    if ref and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    return ref


def _identity_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("displayName") or value.get("uniqueName")
    if isinstance(value, str):
        # 舊版 TFS 格式：「顯示名稱 <網域\帳號>」
        return value.split("<")[0].strip() or value
    return None


class AzureDevOpsIntegration(Integration):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        basic = base64.b64encode(f":{self._token}".encode()).decode()
        self._client = httpx.AsyncClient(
            base_url=self.info.base_url.rstrip("/") + "/",
            headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
            timeout=DEFAULT_TIMEOUT,
            transport=self._transport,
        )
        self._me: dict[str, Any] | None = None
        self._sem = asyncio.Semaphore(8)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- 共用 ----

    @property
    def _web_base(self) -> str:
        return self.info.base_url.rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        params = kwargs.pop("params", {}) or {}
        params.setdefault("api-version", API_VERSION)
        async with self._sem:
            try:
                resp = await self._client.request(method, path, params=params, **kwargs)
            except httpx.HTTPError as exc:
                raise IntegrationError(f"無法連線到 {self.info.base_url}：{exc}") from exc
        self._raise_for_status(resp)
        try:
            return resp.json()
        except ValueError as exc:
            # 驗證失敗時 TFS 可能回傳登入頁 HTML
            raise IntegrationError("平台回應不是 JSON，請確認網址是否為 Collection 網址、Token 是否有效") from exc

    async def _get_me(self) -> dict[str, Any]:
        if self._me is None:
            data = await self._request("GET", "_apis/connectionData", params={"api-version": "5.0-preview"})
            user = data.get("authenticatedUser") or {}
            if not user.get("id"):
                raise IntegrationError("無法取得目前使用者資訊")
            self._me = user
        return self._me

    async def _projects(self) -> list[str]:
        if self.info.scopes:
            return self.info.scopes
        data = await self._request("GET", "_apis/projects", params={"$top": MAX_PROJECTS})
        return [p["name"] for p in data.get("value", [])]

    async def _gather_per_project(self, fn: Any) -> list[Any]:
        projects = await self._projects()
        results = await asyncio.gather(*(fn(p) for p in projects), return_exceptions=True)
        items: list[Any] = []
        errors: list[BaseException] = []
        for r in results:
            if isinstance(r, BaseException):
                errors.append(r)
            else:
                items.extend(r)
        # 全部專案都失敗才視為錯誤；部分專案無權限時略過
        if errors and len(errors) == len(results):
            raise errors[0]
        return items

    # ---- Integration 實作 ----

    async def test_connection(self) -> str:
        me = await self._get_me()
        return me.get("providerDisplayName") or me.get("customDisplayName") or me["id"]

    def _to_pr(self, raw: dict[str, Any]) -> PullRequest:
        repo = raw.get("repository") or {}
        project = (repo.get("project") or {}).get("name")
        repo_name = repo.get("name", "")
        pr_id = raw["pullRequestId"]
        return PullRequest(
            id=str(pr_id),
            platform="ado",
            connection_id=self.info.id,
            title=raw.get("title", ""),
            url=f"{self._web_base}/{quote(project or '')}/_git/{quote(repo_name)}/pullrequest/{pr_id}",
            project=project,
            repo=repo_name,
            author=_identity_name(raw.get("createdBy")) or "",
            status=raw.get("status", ""),
            is_draft=bool(raw.get("isDraft")),
            source_branch=_strip_ref(raw.get("sourceRefName")),
            target_branch=_strip_ref(raw.get("targetRefName")),
            created_at=parse_dt(raw.get("creationDate")),
            updated_at=parse_dt(raw.get("closedDate")) or parse_dt(raw.get("creationDate")),
        )

    async def _search_prs(self, criteria: dict[str, str]) -> list[PullRequest]:
        async def per_project(project: str) -> list[PullRequest]:
            params = {f"searchCriteria.{k}": v for k, v in criteria.items()}
            params["$top"] = "100"
            data = await self._request("GET", f"{quote(project)}/_apis/git/pullrequests", params=params)
            return [self._to_pr(r) for r in data.get("value", [])]

        return await self._gather_per_project(per_project)

    async def list_my_pull_requests(self, *, updated_since: datetime | None = None) -> list[PullRequest]:
        me = await self._get_me()
        if updated_since is None:
            return await self._search_prs({"creatorId": me["id"], "status": "active"})
        prs = await self._search_prs({"creatorId": me["id"], "status": "all"})
        return [
            p for p in prs
            if p.status == "active" or (p.updated_at and p.updated_at >= updated_since)
        ]

    async def list_review_requests(self) -> list[PullRequest]:
        me = await self._get_me()
        prs = await self._search_prs({"reviewerId": me["id"], "status": "active"})
        return [p for p in prs if p.author != (me.get("providerDisplayName") or "")]

    async def _query_work_items(self, where: str) -> list[WorkItem]:
        if self.info.scopes:
            projects = ", ".join(_wiql_str(p) for p in self.info.scopes)
            where += f" AND [System.TeamProject] IN ({projects})"
        query = f"SELECT [System.Id] FROM WorkItems WHERE {where} ORDER BY [System.ChangedDate] DESC"
        data = await self._request("POST", "_apis/wit/wiql", params={"$top": "200"}, json={"query": query})
        ids = [str(w["id"]) for w in data.get("workItems", [])][:200]
        if not ids:
            return []
        detail = await self._request(
            "GET",
            "_apis/wit/workitems",
            params={"ids": ",".join(ids), "fields": ",".join(WORK_ITEM_FIELDS)},
        )
        items: list[WorkItem] = []
        for raw in detail.get("value", []):
            f = raw.get("fields", {})
            project = f.get("System.TeamProject")
            items.append(
                WorkItem(
                    id=str(raw["id"]),
                    platform="ado",
                    connection_id=self.info.id,
                    title=f.get("System.Title", ""),
                    url=f"{self._web_base}/{quote(project or '')}/_workitems/edit/{raw['id']}",
                    project=project,
                    type=f.get("System.WorkItemType", ""),
                    state=f.get("System.State", ""),
                    assigned_to=_identity_name(f.get("System.AssignedTo")),
                    updated_at=parse_dt(f.get("System.ChangedDate")),
                )
            )
        return items

    async def list_assigned_work_items(self) -> list[WorkItem]:
        states = ", ".join(_wiql_str(s) for s in CLOSED_STATES)
        return await self._query_work_items(f"[System.AssignedTo] = @Me AND [System.State] NOT IN ({states})")

    async def list_work_items_changed(self, since: datetime, until: datetime) -> list[WorkItem]:
        # WIQL 預設只接受日期精度
        where = (
            f"[System.ChangedBy] = @Me AND [System.ChangedDate] >= {_wiql_str(since.date().isoformat())}"
            f" AND [System.ChangedDate] <= {_wiql_str(until.date().isoformat())}"
        )
        return await self._query_work_items(where)

    async def list_recent_builds(self, limit: int = 10) -> list[Build]:
        async def per_project(project: str) -> list[Build]:
            data = await self._request(
                "GET",
                f"{quote(project)}/_apis/build/builds",
                params={"$top": str(limit), "queryOrder": "queueTimeDescending"},
            )
            builds = []
            for raw in data.get("value", []):
                web = ((raw.get("_links") or {}).get("web") or {}).get("href")
                builds.append(
                    Build(
                        id=str(raw["id"]),
                        platform="ado",
                        connection_id=self.info.id,
                        name=f"{(raw.get('definition') or {}).get('name', '')} #{raw.get('buildNumber', '')}",
                        url=web or f"{self._web_base}/{quote(project)}/_build/results?buildId={raw['id']}",
                        project=project,
                        repo=(raw.get("repository") or {}).get("name"),
                        branch=_strip_ref(raw.get("sourceBranch")),
                        status=_ADO_BUILD_STATUS.get(raw.get("status", ""), raw.get("status", "")),
                        result=_ADO_BUILD_RESULT.get(raw.get("result") or "", raw.get("result")),
                        requested_by=_identity_name(raw.get("requestedFor")),
                        started_at=parse_dt(raw.get("startTime")),
                        finished_at=parse_dt(raw.get("finishTime")),
                    )
                )
            return builds

        builds = await self._gather_per_project(per_project)
        builds.sort(key=lambda b: b.started_at.timestamp() if b.started_at else 0, reverse=True)
        return builds[:limit]

    async def list_my_commits(self, since: datetime, until: datetime) -> list[Commit]:
        me = await self._get_me()
        author = me.get("providerDisplayName") or ""

        async def per_project(project: str) -> list[Commit]:
            repos = await self._request("GET", f"{quote(project)}/_apis/git/repositories")

            async def per_repo(repo: dict[str, Any]) -> list[Commit]:
                data = await self._request(
                    "GET",
                    f"{quote(project)}/_apis/git/repositories/{repo['id']}/commits",
                    params={
                        "searchCriteria.author": author,
                        "searchCriteria.fromDate": since.isoformat(),
                        "searchCriteria.toDate": until.isoformat(),
                        "searchCriteria.$top": "100",
                    },
                )
                return [
                    Commit(
                        sha=c["commitId"],
                        platform="ado",
                        connection_id=self.info.id,
                        message=c.get("comment", ""),
                        url=f"{self._web_base}/{quote(project)}/_git/{quote(repo['name'])}/commit/{c['commitId']}",
                        project=project,
                        repo=repo["name"],
                        author=(c.get("author") or {}).get("name", ""),
                        date=parse_dt((c.get("author") or {}).get("date")),
                    )
                    for c in data.get("value", [])
                ]

            results = await asyncio.gather(
                *(per_repo(r) for r in repos.get("value", []) if not r.get("isDisabled")),
                return_exceptions=True,
            )
            return [c for r in results if not isinstance(r, BaseException) for c in r]

        return await self._gather_per_project(per_project)


_ADO_BUILD_STATUS = {
    "notStarted": "queued",
    "postponed": "queued",
    "inProgress": "in_progress",
    "cancelling": "in_progress",
    "completed": "completed",
}
_ADO_BUILD_RESULT = {
    "succeeded": "succeeded",
    "partiallySucceeded": "partially_succeeded",
    "failed": "failed",
    "canceled": "canceled",
}
