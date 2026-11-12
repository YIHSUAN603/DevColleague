"""開發流程共用資料模型與介面。"""

from dataclasses import dataclass


@dataclass
class PullRequestDetail:
    id: str
    title: str
    description: str
    source_branch: str
    target_branch: str
    diff: str
    url: str = ""


class DevelopmentIntegration:
    async def pull_request_detail(self, project: str, repo: str, number: int) -> PullRequestDetail:
        raise NotImplementedError("平台尚未支援 PR 詳細資料")

    async def create_pull_request(self, project: str, repo: str, source: str, target: str,
                                  title: str, description: str) -> dict:
        raise NotImplementedError("平台尚未支援建立 PR")

    async def comment_pull_request(self, project: str, repo: str, number: int, content: str) -> dict:
        raise NotImplementedError("平台尚未支援 PR 留言")

    async def build_log(self, project: str, repo: str, number: int) -> str:
        raise NotImplementedError("平台尚未支援建置記錄")
