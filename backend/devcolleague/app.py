"""FastAPI 應用程式。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
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
    app = FastAPI(title="DevColleague", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(router)
    return app
