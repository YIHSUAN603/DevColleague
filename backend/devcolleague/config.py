"""後端執行設定。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "DevColleague"
SESSION_TOKEN_ENV = "DEVCOLLEAGUE_SESSION_TOKEN"
DATA_DIR_ENV = "DEVCOLLEAGUE_DATA_DIR"


def default_data_dir() -> Path:
    """回傳使用者資料目錄（Windows 為 %APPDATA%\\DevColleague）。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


@dataclass(frozen=True)
class Settings:
    session_token: str
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "devcolleague.db"
