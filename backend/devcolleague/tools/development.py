"""讓對話與背景助理使用相同的唯讀 PR 差異及建置記錄。"""

import json
from dataclasses import asdict

from ..services.development import DevelopmentService
from .base import Tool, ToolRegistry


def register_development_tools(registry: ToolRegistry, service: DevelopmentService) -> None:
    def connection_id(value: str) -> str:
        matches = [c for c in service.connections.list() if c.id == value or c.name == value]
        if len(matches) != 1:
            raise ValueError("找不到唯一的平台連線，請提供連線 ID 或完整名稱")
        return matches[0].id

    async def get_pull_request_diff(args):
        detail = await service.platform(connection_id(args["connection"]), "pull_request_detail",
                                        args.get("project", ""), args["repo"], args["number"])
        return service.redact(json.dumps(asdict(detail), ensure_ascii=False))

    async def get_build_log(args):
        log = await service.platform(connection_id(args["connection"]), "build_log",
                                     args.get("project", ""), args.get("repo", ""), args["number"])
        return service.redact(log)

    properties = {
        "connection": {"type": "string", "description": "平台連線 ID 或完整名稱"},
        "project": {"type": "string", "description": "ADS/TFS 專案，GitHub 可省略"},
        "repo": {"type": "string", "description": "GitHub 的 owner/name，或 ADS/TFS Repo 名稱／ID"},
        "number": {"type": "integer", "minimum": 1, "description": "PR 編號或 Build／Run ID"},
    }
    registry.register(Tool("get_pull_request_diff", "唯讀取得平台 PR 本文與差異，可用來摘要或審查；長內容會截斷。",
                           {"type": "object", "properties": properties, "required": ["connection", "repo", "number"]},
                           get_pull_request_diff))
    registry.register(Tool("get_build_log", "唯讀取得 GitHub 失敗 job 或 ADS/TFS 建置記錄，供診斷使用；長內容會截斷。",
                           {"type": "object", "properties": properties, "required": ["connection", "number"]},
                           get_build_log))
