"""日報告（F15）：依日報告專案分組彙整當天活動，交由 LLM 產生可貼到團隊日報告文件的內容。

- 進度百分比由 Work Item 父子階層計算，不由 LLM 推測；沒有對應 Work Item 的工作項目沿用前一份日報告的數字。
- 每天的定稿存進 SQLite，下次產生時帶入最近一份，讓工作項目名稱與進度能接續。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field

from ..llm.gateway import Gateway
from ..llm.registry import ProviderService
from ..llm.types import ChatEvent, ChatMessage, ChatRequest
from ..storage.db import Database, utc_now
from .activity import ActivityService

SETTING_PROJECTS = "daily_report.projects"
UNMAPPED_PROJECT = "未分類"
# 日報告最遲於下一個工作日的這個時間前完成；在此之前開啟時，預設填寫上一個工作日
REPORT_DEADLINE_HOUR = 10

DAILY_REPORT_SYSTEM_PROMPT = """你是協助開發者撰寫每日工作報告（日報告）的助理。
根據使用者提供的資料（JSON）撰寫當天的日報告，內容會被貼到團隊共用文件中各專案底下。

輸出格式（Markdown）：
- 每個有內容的專案以「### 專案名稱」為標題，依資料中 project_names 的順序排列；沒有內容的專案不要輸出。
- 專案底下使用巢狀條列（每層縮排 4 個空白）：第一層是工作項目，第二層以下是子功能與具體變更。
- 已完成的項目在結尾加上 ✅，進行中的加上 ⏳。
- 除了上述標題與條列，不要輸出任何說明文字。

範例：
### 公路局
- 行道樹木查詢(40%)
    - 條件查詢
        - 新增條件查詢 API 與樹種下拉選單資料來源 ✅
        - 查詢結果清冊匯出 ⏳
### 海洋
- 內部進度會議

撰寫規則：
- 使用繁體中文（台灣用語）。只根據資料撰寫，不要編造資料中沒有的工作。
- 相關的 commit、PR、Work Item 合併成同一項工作描述，以使用者看得懂的功能描述呈現，不要直接照抄 commit 訊息的前綴（例如 feat:）。
- 工作項目的進度：
  - 資料的 progress 中有對應的上層 Work Item 時，以它的標題作為工作項目名稱，並在名稱後加上「(percent%)」。這個數字是系統計算的，必須原樣使用。
  - progress 中沒有，但前一份日報告有相同的工作項目且帶有百分比時，沿用該數字並標示為「(數字%，待確認)」。
  - 兩者都沒有時不要寫百分比，也不要自行估算。
- Work Item 與 PR 可用 Markdown 連結附上網址。
- notes 是使用者手動補充的內容（會議、包版、待確認問題等），請整理後放到最相關的專案底下；無法判斷專案時放在「未分類」。
- 前一份日報告只用來延續工作項目的名稱、階層與寫法，不要把其中的舊內容當成當天的工作重複列出。"""


class ReportProject(BaseModel):
    """日報告中的一個專案，以及它對應的平台專案（ADS 專案名稱或 GitHub repo）。"""

    name: str = Field(min_length=1)
    source: str = ""


def default_report_date(now: datetime) -> date:
    """預設的報告日期：截止時間前為上一個工作日，之後為當天；遇週末往前推到週五。"""
    day = now.date()
    if now.hour < REPORT_DEADLINE_HOUR:
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def day_range(day: date) -> tuple[datetime, datetime]:
    """回傳本地時區當天的 [since, until) 區間。"""
    tz = datetime.now().astimezone().tzinfo
    since = datetime.combine(day, time.min, tzinfo=tz)
    return since, since + timedelta(days=1)


def _within(dt: datetime | None, since: datetime, until: datetime) -> bool:
    return dt is not None and since <= dt < until


class DailyReportService:
    def __init__(self, db: Database, activity: ActivityService, providers: ProviderService, gateway: Gateway) -> None:
        self._db = db
        self._activity = activity
        self._providers = providers
        self._gateway = gateway

    # ---- 專案對應設定 ----

    def projects(self) -> list[ReportProject]:
        raw = self._db.get_setting(SETTING_PROJECTS)
        return [ReportProject(**p) for p in json.loads(raw)] if raw else []

    def set_projects(self, projects: list[ReportProject]) -> list[ReportProject]:
        cleaned = [ReportProject(name=p.name.strip(), source=p.source.strip()) for p in projects]
        names = [p.name.casefold() for p in cleaned]
        if len(set(names)) != len(names):
            raise ValueError("日報告專案名稱不可重複")
        self._db.set_setting(SETTING_PROJECTS, json.dumps([p.model_dump() for p in cleaned], ensure_ascii=False))
        return cleaned

    async def sources(self) -> dict[str, Any]:
        """可供對應的平台專案清單。"""
        result = await self._activity.projects()
        return {"sources": result.items, "errors": result.errors}

    # ---- 定稿 ----

    def get(self, day: date) -> dict[str, Any] | None:
        return self._db.fetch_one(
            "SELECT report_date AS date, content, notes, updated_at FROM daily_reports WHERE report_date = ?",
            (day.isoformat(),),
        )

    def save(self, day: date, content: str, notes: str) -> dict[str, Any]:
        self._db.execute(
            "INSERT INTO daily_reports(report_date, content, notes, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(report_date) DO UPDATE SET content = excluded.content, notes = excluded.notes, "
            "updated_at = excluded.updated_at",
            (day.isoformat(), content, notes, utc_now()),
        )
        saved = self.get(day)
        assert saved is not None
        return saved

    def previous(self, day: date) -> dict[str, Any] | None:
        """指定日期之前最近一份有內容的日報告。"""
        return self._db.fetch_one(
            "SELECT report_date AS date, content FROM daily_reports "
            "WHERE report_date < ? AND content <> '' ORDER BY report_date DESC LIMIT 1",
            (day.isoformat(),),
        )

    # ---- 彙整與產生 ----

    async def collect(self, day: date) -> dict[str, Any]:
        since, until = day_range(day)
        commits, prs, changed = await asyncio.gather(
            self._activity.my_commits(since, until),
            self._activity.my_pull_requests(updated_since=since),
            self._activity.work_items_changed(since, until),
        )
        progress = await self._activity.work_item_progress(changed.items)
        errors = {e["connection_id"]: e for r in (commits, prs, changed, progress) for e in r.errors}

        projects = self.projects()
        name_by_source = {p.source.casefold(): p.name for p in projects if p.source}
        groups: dict[str, dict[str, list[dict[str, Any]]]] = {}

        def group(item: Any) -> dict[str, list[dict[str, Any]]]:
            source = (item.project or item.repo or "").casefold()
            name = name_by_source.get(source, UNMAPPED_PROJECT)
            return groups.setdefault(
                name, {"progress": [], "work_items_changed": [], "pull_requests": [], "commits": []}
            )

        # 子項目 → 上層項目標題，讓 LLM 知道異動的 Work Item 屬於哪個工作項目
        parent_title = {
            (p.item.connection_id, child): p.item.title for p in progress.items for child in p.child_ids
        }
        for p in progress.items:
            group(p.item)["progress"].append(
                {"id": p.item.id, "title": p.item.title, "type": p.item.type, "url": p.item.url,
                 "percent": p.percent, "done": p.done, "total": p.total}
            )
        for w in changed.items:
            group(w)["work_items_changed"].append(
                {"id": w.id, "title": w.title, "type": w.type, "state": w.state, "url": w.url,
                 "parent": parent_title.get((w.connection_id, w.id))}
            )
        for pr in prs.items:
            if _within(pr.created_at, since, until) or _within(pr.updated_at, since, until):
                group(pr)["pull_requests"].append(
                    {"repo": pr.repo, "title": pr.title, "status": pr.status, "url": pr.url,
                     "branch": pr.source_branch}
                )
        for c in commits.items:
            if c.date is None or _within(c.date, since, until):
                group(c)["commits"].append(
                    {"repo": c.repo, "message": c.message.strip(),
                     "date": c.date.isoformat() if c.date else None, "url": c.url}
                )

        names = [p.name for p in projects] + [UNMAPPED_PROJECT]
        return {
            "date": day.isoformat(),
            "project_names": names,
            "projects": [{"name": n, **groups[n]} for n in names if n in groups],
            "errors": list(errors.values()),
        }

    async def generate(
        self,
        day: date,
        notes: str = "",
        *,
        provider_id: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        try:
            provider, _ = self._providers.build(provider_id)
        except LookupError as exc:
            yield ChatEvent.error(str(exc))
            return

        notes = notes.strip()
        activity = await self.collect(day)
        activity["notes"] = notes
        activity["previous_report"] = self.previous(day)
        # 先讓使用者看到即將送給 LLM 的資料
        yield ChatEvent("activity", activity)

        if not activity["projects"] and not notes:
            # 以錯誤事件回報，避免這段提示被前端當成日報告內容儲存
            yield ChatEvent.error("這一天沒有找到任何 commit、PR 或 Work Item 活動，也沒有手動補充的內容。")
            return

        payload = {k: v for k, v in activity.items() if k != "errors"}
        prompt = (
            f"請撰寫 {day:%Y-%m-%d} 的日報告。\n\n"
            f"資料：\n```json\n{json.dumps(payload, ensure_ascii=False, indent=1)}\n```"
        )
        request = ChatRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            system=DAILY_REPORT_SYSTEM_PROMPT,
            model=model or None,
        )
        async for event in self._gateway.run(provider, request, use_tools=False):
            yield event
