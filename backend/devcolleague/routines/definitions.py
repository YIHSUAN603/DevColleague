"""Routine 以 YAML frontmatter 加 Markdown 提示儲存。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml
from croniter import croniter
from pydantic import BaseModel, Field, field_validator


class Routine(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=30000)
    schedule: str | None = None
    command: str | None = Field(default=None, pattern=r"^/[a-zA-Z0-9_-]+$")
    provider: str | None = None
    model: str | None = None
    enabled: bool = True
    notify: bool = True
    catch_up: bool = True
    allow_side_effects: list[str] = Field(default_factory=list)

    @field_validator("schedule")
    @classmethod
    def validate_schedule(cls, value: str | None) -> str | None:
        if value and (len(value.split()) != 5 or not croniter.is_valid(value)):
            raise ValueError("排程須為有效的五欄 cron 格式")
        return value or None

    def render(self, now: datetime, last_run_at: str | None) -> str:
        from datetime import timedelta

        values = {"today": now.date().isoformat(), "yesterday": (now.date() - timedelta(days=1)).isoformat(),
                  "last_run_at": last_run_at or "尚未執行"}
        text = self.prompt
        for name, value in values.items():
            text = text.replace("{{" + name + "}}", value)
        return text


DEFAULTS = [
    Routine(id="morning-brief", name="晨間流程", schedule="30 8 * * 1-5", command="/morning", enabled=False,
            prompt="今天是 {{today}}。請用 collect_daily_report 查詢上一個工作日的活動，依回傳的進度撰寫日報告，"
            "以 save_daily_report_draft 儲存草稿。已有內容則保留。整理今天待審查的 PR、指派的 Work Item、"
            "失敗的建置，以及前一份日報告中進行中的項目。"),
    Routine(id="standup", name="站會報告", command="/standup", enabled=False,
            prompt="今天是 {{today}}。以 collect_work_summary 的 yesterday 區間查詢活動，整理昨天完成、今天預計與阻礙。"),
    Routine(id="weekly", name="週報", command="/weekly", schedule="0 17 * * 5", enabled=False,
            prompt="今天是 {{today}}。以 collect_work_summary 的 this_week 區間查詢活動，整理完成事項、進行中工作及風險。"),
]


class RoutineStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.errors: list[str] = []
        directory.mkdir(parents=True, exist_ok=True)
        # 只在首次啟動安裝範本，刪除的範本不會重新出現。
        marker = directory / ".initialized"
        if not marker.exists():
            for routine in DEFAULTS:
                if not (directory / f"{routine.id}.md").exists():
                    self.save(routine)
            marker.write_text("1", encoding="utf-8")

    def list(self) -> list[Routine]:
        self.errors = []
        routines = []
        for path in sorted(self.directory.glob("*.md")):
            try:
                routines.append(self.read(path))
            except (ValueError, TypeError, OSError, yaml.YAMLError) as exc:
                self.errors.append(f"{path.name}：{exc}")
        return routines

    def read(self, path: Path) -> Routine:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise ValueError(f"Routine {path.name} 缺少 frontmatter")
        parts = text.split("---", 2)
        if len(parts) != 3:
            raise ValueError(f"Routine {path.name} 格式錯誤")
        routine = Routine(**yaml.safe_load(parts[1]), prompt=parts[2].strip())
        if path.stem != routine.id:
            raise ValueError("Routine 檔名必須與 id 相同")
        return routine

    def get(self, routine_id: str) -> Routine:
        # 識別字先驗證，避免路徑跳脫。
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", routine_id):
            raise ValueError("Routine id 格式錯誤")
        path = self.directory / f"{routine_id}.md"
        if not path.exists():
            raise LookupError("找不到例行流程")
        return self.read(path)

    def save(self, routine: Routine) -> Routine:
        for existing in self.list():
            if existing.id != routine.id and routine.command and existing.command == routine.command:
                raise ValueError("指令已被其他例行流程使用")
        path = self.directory / f"{routine.id}.md"
        temp = path.with_suffix(".tmp")
        metadata = routine.model_dump(exclude={"prompt"})
        temp.write_text("---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n" + routine.prompt + "\n", encoding="utf-8")
        temp.replace(path)
        return routine

    def delete(self, routine_id: str) -> None:
        self.get(routine_id)
        (self.directory / f"{routine_id}.md").unlink()
