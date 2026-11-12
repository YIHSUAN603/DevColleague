"""FastAPI 應用程式。"""

from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.assistant import router as assistant_router
from .api.development import router as development_router
from .container import Container

# Tauri WebView 與 Vite dev server 的來源
ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
]


def create_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with container.mcp.manager.run():
            container.scheduler.start()
            try:
                yield
            finally:
                await container.scheduler.close()

    app = FastAPI(title="DevColleague", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(router)
    app.include_router(assistant_router)
    app.include_router(development_router)
    # 使用原始 ASGI route，讓 MCP 位址無須額外的重新導向。
    from starlette.routing import Route
    app.router.routes.append(Route("/mcp", endpoint=container.mcp, methods=["GET", "POST", "DELETE"]))
    return app
