"""ADS／TFS 開發流程 API，沿用 Collection 連線與 6.0 版本。"""

import difflib
from urllib.parse import quote

from .base import IntegrationError
from .development import PullRequestDetail


def git_path(project: str, repo: str) -> str:
    if not project or not repo:
        raise ValueError("ADS/TFS 需要指定專案與 Repo")
    return f"{quote(project, safe='')}/_apis/git/repositories/{quote(repo, safe='')}"


class AzureDevelopment:
    async def _item_text(self, path: str, file: str, commit: str) -> str:
        response = await self._client.get(path + "/items", params={
            "path": file, "versionDescriptor.version": commit, "versionDescriptor.versionType": "commit",
            "includeContent": "true", "includeContentMetadata": "true", "$format": "json", "api-version": "6.0",
        }, headers={"Accept": "application/json"})
        self._raise_for_status(response)
        data = response.json()
        if data.get("isBinary") or (data.get("contentMetadata") or {}).get("isBinary"):
            return "（二進位檔案）"
        return data.get("content", "")

    async def pull_request_detail(self, project: str, repo: str, number: int) -> PullRequestDetail:
        path = git_path(project, repo)
        data = await self._request("GET", f"{path}/pullrequests/{number}")
        base = data["lastMergeTargetCommit"]["commitId"]
        head = data["lastMergeSourceCommit"]["commitId"]
        comparison = await self._request("GET", path + "/diffs/commits", params={
            "baseVersion": base, "baseVersionType": "commit", "targetVersion": head,
            "targetVersionType": "commit", "diffCommonCommit": "true", "$top": 100,
        })
        base = comparison.get("commonCommit") or base
        chunks = []
        for change in comparison.get("changes", []):
            item = change.get("item", {})
            if item.get("isFolder"):
                continue
            file = item["path"]
            kind = change.get("changeType", "").lower()
            old_file = change.get("originalPath") or change.get("sourceServerItem") or file
            flags = {part.strip() for part in kind.split(",")}
            old = "" if "add" in flags else await self._item_text(path, old_file, base)
            new = "" if "delete" in flags else await self._item_text(path, file, head)
            chunks.append(f"diff {kind}: {old_file} → {file}\n")
            diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=old_file, tofile=file)
            chunks.append("".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in diff))
            if "（二進位檔案）" in (old, new):
                chunks.append("（二進位檔案變更，未提供內容）\n")
        if not comparison.get("allChangesIncluded", True):
            chunks.append("\n（平台差異僅載入前 100 個項目，審查範圍不完整）")
        return PullRequestDetail(str(number), data["title"], data.get("description") or "",
                                 data["sourceRefName"], data["targetRefName"], "\n".join(chunks),
                                 f"{self._web_base}/{quote(project, safe='')}/_git/{quote(repo, safe='')}/pullrequest/{number}")

    async def create_pull_request(self, project: str, repo: str, source: str, target: str,
                                  title: str, description: str) -> dict:
        def ref(value):
            return value if value.startswith("refs/heads/") else "refs/heads/" + value
        data = await self._request("POST", git_path(project, repo) + "/pullrequests", json={
            "sourceRefName": ref(source), "targetRefName": ref(target), "title": title, "description": description,
        })
        number = data["pullRequestId"]
        return {"id": str(number), "url":
                f"{self._web_base}/{quote(project, safe='')}/_git/{quote(repo, safe='')}/pullrequest/{number}"}

    async def comment_pull_request(self, project: str, repo: str, number: int, content: str) -> dict:
        data = await self._request("POST", f"{git_path(project, repo)}/pullrequests/{number}/threads", json={
            "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}], "status": 1,
        })
        return {"id": str(data["id"]), "url": ""}

    async def build_log(self, project: str, repo: str, number: int) -> str:
        if not project:
            raise ValueError("請指定 ADS/TFS 專案")
        path = f"{quote(project, safe='')}/_apis/build/builds/{number}"
        logs = await self._request("GET", path + "/logs")
        chunks = []
        for log in logs.get("value", [])[-20:]:
            response = await self._client.get(f"{path}/logs/{log['id']}",
                                               params={"api-version": "6.0", "startLine": 0, "endLine": 10000},
                                               headers={"Accept": "text/plain"})
            self._raise_for_status(response)
            chunks.append(f"## Log {log['id']}\n{response.text[:200000]}")
        if not chunks:
            raise IntegrationError("此建置沒有可讀取的記錄")
        return "\n".join(chunks) + "\n（最多載入最後 20 份記錄，每份前 10000 行／200000 字元）"
