"""後端進入點。

由 Tauri 以 sidecar 啟動：
    devcolleague-backend --port <port>
session token 透過環境變數 DEVCOLLEAGUE_SESSION_TOKEN 傳入（不放在命令列，避免被其他行程看見）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading

import uvicorn

from .app import create_app
from .config import SESSION_TOKEN_ENV, Settings, default_data_dir
from .container import build_container

HOST = "127.0.0.1"  # 只監聽本機


def _exit_on_stdin_eof() -> None:
    try:
        while sys.stdin.read(1024):
            pass
    finally:
        os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="devcolleague-backend")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--exit-on-stdin-eof",
        action="store_true",
        help="stdin 關閉時結束（由 Tauri 啟動時使用，App 異常結束也不會留下孤兒行程）",
    )
    args = parser.parse_args()
    if args.exit_on_stdin_eof:
        threading.Thread(target=_exit_on_stdin_eof, daemon=True).start()

    token = os.environ.pop(SESSION_TOKEN_ENV, "")
    if len(token) < 16:
        print(f"必須以環境變數 {SESSION_TOKEN_ENV} 提供至少 16 字元的 session token", file=sys.stderr)
        sys.exit(2)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings(session_token=token, data_dir=default_data_dir(), port=args.port)
    app = create_app(build_container(settings))
    config = uvicorn.Config(app, host=HOST, port=args.port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    # 使用 asyncio.run（Windows 預設為 ProactorEventLoop），CLI Provider 需要非同步子行程支援
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
