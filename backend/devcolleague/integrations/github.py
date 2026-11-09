"""GitHub 整合（github.com 或 GitHub Enterprise Server）。

base_url：github.com 使用 https://api.github.com；GHES 使用 https://<host>/api/v3。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from .base import DEFAULT_TIMEOUT, Integration, IntegrationError, parse_dt
from .models import Build, Commit, PullRequest, WorkItem

DEFAULT_BASE_URL = "https://api.github.com"
AUTO_REPO_COUNT = 5

_CONCLUSION = {
    "success": "succeeded",
    "failure": "failed",
    "cancelled": "canceled",
    "timed_out": "failed",
    "startup_failure": "failed",
    "action_required": None,
    "neutral": "succeeded",
    "skipped": "canceled",
}


def _repo_from_api_url(url: str) -> str:
    # https://api.github.com/repos/{owner}/{repo}
    parts = url.rstrip("/").split("/")
    return "/".join(parts[-2:])


def _day(dt: datetime) -> str:
    return dt.date().isoformat()


class GitHubIntegration(Integration):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client = httpx.AsyncClient(
            base_url=(self.info.base_url or DEFAULT_BASE_URL).rstrip("/") + "/",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=DEFAULT_TIMEOUT,
            transport=self._transport,
        )
        self._login: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            resp = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise IntegrationError(f"無法連線到 GitHub：{exc}") from exc
        if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
            raise IntegrationError("GitHub API 已達速率上限，請稍後再試")
        self._raise_for_status(resp)
        return resp.json()

    async def _me(self) -> str:
        if self._login is None:
            data = await self._get("user")
            self._login = data["login"]
        return self._login

    async def _search_issues(self, query: str) -> list[dict[str, Any]]:
        data = await self._get("search/issues", {"q": query, "per_page": 50, "sort": "updated"})
        return data.get("items", [])

    def _to_pr(self, raw: dict[str, Any]) -> PullRequest:
        state = raw.get("state", "")
        if state == "open":
            status = "active"
        elif (raw.get("pull_request") or {}).get("merged_at"):
            status = "merged"
        else:
            status = "closed"
        return PullRequest(
            id=str(raw["number"]),
            platform="github",
            connection_id=self.info.id,
            title=raw.get("title", ""),
            url=raw.get("html_url", ""),
            repo=_repo_from_api_url(raw.get("repository_url", "")),
            author=(raw.get("user") or {}).get("login", ""),
            status=status,
            is_draft=bool(raw.get("draft")),
            created_at=parse_dt(raw.get("created_at")),
            updated_at=parse_dt(raw.get("updated_at")),
        )

    def _to_work_item(self, raw: dict[str, Any]) -> WorkItem:
        labels = [lb.get("name", "") for lb in raw.get("labels", []) if isinstance(lb, dict)]
        return WorkItem(
            id=str(raw["number"]),
            platform="github",
            connection_id=self.info.id,
            title=raw.get("title", ""),
            url=raw.get("html_url", ""),
            repo=_repo_from_api_url(raw.get("repository_url", "")),
            type="Issue" + (f"（{', '.join(labels)}）" if labels else ""),
            state=raw.get("state", ""),
            assigned_to=(raw.get("assignee") or {}).get("login"),
            updated_at=parse_dt(raw.get("updated_at")),
        )

    # ---- Integration 實作 ----

    async def test_connection(self) -> str:
        return await self._me()

    async def list_my_pull_requests(self, *, updated_since: datetime | None = None) -> list[PullRequest]:
        login = await self._me()
        if updated_since is None:
            q = f"is:pr is:open author:{login} archived:false"
        else:
            q = f"is:pr author:{login} updated:>={_day(updated_since)}"
        return [self._to_pr(r) for r in await self._search_issues(q)]

    async def list_review_requests(self) -> list[PullRequest]:
        login = await self._me()
        q = f"is:pr is:open review-requested:{login} archived:false"
        return [self._to_pr(r) for r in await self._search_issues(q)]

    async def list_assigned_work_items(self) -> list[WorkItem]:
        login = await self._me()
        q = f"is:issue is:open assignee:{login} archived:false"
        return [self._to_work_item(r) for r in await self._search_issues(q)]

    async def list_work_items_changed(self, since: datetime, until: datetime) -> list[WorkItem]:
        login = await self._me()
        q = f"is:issue involves:{login} updated:{_day(since)}..{_day(until)}"
        return [self._to_work_item(r) for r in await self._search_issues(q)]

    async def _build_repos(self) -> list[str]:
        if self.info.scopes:
            return self.info.scopes
        data = await self._get("user/repos", {"sort": "pushed", "per_page": AUTO_REPO_COUNT})
        return [r["full_name"] for r in data]

    async def list_recent_builds(self, limit: int = 10) -> list[Build]:
        repos = await self._build_repos()

        async def per_repo(repo: str) -> list[Build]:
            data = await self._get(f"repos/{repo}/actions/runs", {"per_page": limit})
            return [
                Build(
                    id=str(r["id"]),
                    platform="github",
                    connection_id=self.info.id,
                    name=f"{r.get('name', '')}：{r.get('display_title', '')}",
                    url=r.get("html_url", ""),
                    repo=repo,
                    branch=r.get("head_branch"),
                    status=r.get("status", ""),
                    result=_CONCLUSION.get(r.get("conclusion") or "", r.get("conclusion")),
                    requested_by=(r.get("actor") or {}).get("login"),
                    started_at=parse_dt(r.get("run_started_at")),
                    finished_at=parse_dt(r.get("updated_at")) if r.get("status") == "completed" else None,
                )
                for r in data.get("workflow_runs", [])
            ]

        results = await asyncio.gather(*(per_repo(r) for r in repos), return_exceptions=True)
        builds = [b for r in results if not isinstance(r, BaseException) for b in r]
        if repos and not builds and all(isinstance(r, BaseException) for r in results):
            raise results[0]  # type: ignore[misc]
        builds.sort(key=lambda b: b.started_at.timestamp() if b.started_at else 0, reverse=True)
        return builds[:limit]

    async def list_my_commits(self, since: datetime, until: datetime) -> list[Commit]:
        login = await self._me()
        data = await self._get(
            "search/commits",
            {"q": f"author:{login} author-date:{_day(since)}..{_day(until)}", "per_page": 100, "sort": "author-date"},
        )
        return [
            Commit(
                sha=c["sha"],
                platform="github",
                connection_id=self.info.id,
                message=(c.get("commit") or {}).get("message", ""),
                url=c.get("html_url", ""),
                repo=(c.get("repository") or {}).get("full_name", ""),
                author=login,
                date=parse_dt(((c.get("commit") or {}).get("author") or {}).get("date")),
            )
            for c in data.get("items", [])
        ]
