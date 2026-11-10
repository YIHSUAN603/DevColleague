"""內建工具的 Streamable HTTP MCP 轉接與短效執行憑證。"""

from __future__ import annotations

import secrets
from contextlib import contextmanager

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool as McpTool
from starlette.requests import Request
from starlette.responses import JSONResponse

from .base import ToolRegistry
from .context import ExecutionContext, execution_context


class McpBridge:
    def __init__(self, tools: ToolRegistry, port: int) -> None:
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.credentials: dict[str, ExecutionContext] = {}
        server = Server("DevColleague")

        @server.list_tools()
        async def list_tools() -> list[McpTool]:
            return [McpTool(name=t.name, description=t.description, inputSchema=t.input_schema) for t in tools.all()]

        @server.call_tool()
        async def call_tool(name: str, arguments: dict) -> CallToolResult:
            content, error = await tools.execute(name, arguments)
            return CallToolResult(content=[TextContent(type="text", text=content)], isError=error)

        self.manager = StreamableHTTPSessionManager(
            server, stateless=True, json_response=True,
            security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                allowed_hosts=["127.0.0.1:*", "localhost:*"],
                allowed_origins=["http://127.0.0.1:*", "http://localhost:*"]),
        )

    @contextmanager
    def grant(self, context: ExecutionContext):
        credential = secrets.token_urlsafe(32)
        self.credentials[credential] = context
        try:
            yield self.url, credential
        finally:
            self.credentials.pop(credential, None)

    async def __call__(self, scope, receive, send) -> None:
        request = Request(scope)
        authorization = request.headers.get("authorization", "")
        context = self.credentials.get(authorization.removeprefix("Bearer "))
        if context is None:
            await JSONResponse({"detail": "MCP 執行憑證無效或已到期"}, status_code=401)(scope, receive, send)
            return
        token = execution_context.set(context)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            execution_context.reset(token)
