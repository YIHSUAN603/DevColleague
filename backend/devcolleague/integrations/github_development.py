"""GitHub 開發流程 API。"""

from urllib.parse import quote, urlparse

import httpx

from .base import IntegrationError
from .development import PullRequestDetail

MAX_LOG_BYTES = 2_000_000


def repo_path(repo: str) -> str:
    parts = repo.split("/")
    if len(parts) != 2 or any(not p or p in (".", "..") for p in parts):
        raise ValueError("GitHub Repo 請使用 owner/name 格式")
    return "repos/" + "/".join(quote(p, safe="") for p in parts)


class GitHubDevelopment:
    async def _development_request(self, method: str, path: str, **kwargs):
        response = await self._client.request(method, path, **kwargs)
        self._raise_for_status(response)
        return response

    async def pull_request_detail(self, project: str, repo: str, number: int) -> PullRequestDetail:
        path = f"{repo_path(repo)}/pulls/{number}"
        data = await self._get(path)
        response = await self._development_request("GET", path, headers={"Accept": "application/vnd.github.diff"})
        return PullRequestDetail(str(number), data["title"], data.get("body") or "",
                                 data["head"]["ref"], data["base"]["ref"], response.text, data["html_url"])

    async def create_pull_request(self, project: str, repo: str, source: str, target: str,
                                  title: str, description: str) -> dict:
        response = await self._development_request("POST", f"{repo_path(repo)}/pulls", json={
            "head": source, "base": target, "title": title, "body": description,
        })
        data = response.json()
        return {"id": str(data["number"]), "url": data["html_url"]}

    async def comment_pull_request(self, project: str, repo: str, number: int, content: str) -> dict:
        response = await self._development_request("POST", f"{repo_path(repo)}/issues/{number}/comments",
                                                   json={"body": content})
        return {"url": response.json().get("html_url", ""), "id": str(response.json()["id"])}

    async def build_log(self, project: str, repo: str, number: int) -> str:
        path = f"{repo_path(repo)}/actions/runs/{number}/jobs"
        jobs = []
        for page in range(1, 11):
            data = await self._get(path, {"per_page": 100, "page": page})
            jobs.extend(data.get("jobs", []))
            if len(data.get("jobs", [])) < 100:
                break
        failed = [j for j in jobs if j.get("conclusion") in ("failure", "timed_out", "startup_failure")]
        if not failed:
            raise ValueError("此執行沒有可診斷的失敗 job")
        logs = []
        for job in failed[:10]:
            response = await self._development_request("GET", f"{repo_path(repo)}/actions/jobs/{job['id']}/logs")
            if response.is_redirect:
                location = response.headers.get("location", "")
                if urlparse(location).scheme != "https":
                    raise IntegrationError("建置記錄下載網址必須使用 HTTPS")
                # 簽署下載網址使用獨立連線，不轉送平台憑證。
                async with httpx.AsyncClient(timeout=30) as client:
                    async with client.stream("GET", location) as download:
                        download.raise_for_status()
                        chunks = bytearray()
                        async for chunk in download.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) >= MAX_LOG_BYTES:
                                break
                text = bytes(chunks[:MAX_LOG_BYTES]).decode("utf-8", errors="replace")
            else:
                text = response.text[:MAX_LOG_BYTES]
            logs.append(f"## {job['name']}\n{text}")
        return "\n\n".join(logs) + ("\n（僅載入前十個失敗 job）" if len(failed) > 10 else "")
