"""AI 對話：對話紀錄的保存與一次對話回合的執行。"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ..llm.gateway import Gateway
from ..llm.registry import ProviderService
from ..llm.types import ChatEvent, ChatMessage, ChatRequest
from ..storage.db import Database, utc_now

TITLE_MAX_CHARS = 30

CHAT_SYSTEM_PROMPT = """你是 DevColleague，一位陪伴開發者日常工作的 AI 同事。
- 一律使用繁體中文（台灣用語）回覆，技術名詞可保留英文。
- 需要查詢 PR、Work Item、建置、commit 等平台資料時，使用提供的工具取得實際資料再回答，不要臆測。
- 列出項目時附上連結，回覆保持簡潔、重點清楚。
今天是 {today}。"""


def _system_prompt() -> str:
    return CHAT_SYSTEM_PROMPT.format(today=datetime.now().astimezone().strftime("%Y-%m-%d（%A）"))


class ConversationService:
    def __init__(self, db: Database, providers: ProviderService, gateway: Gateway) -> None:
        self._db = db
        self._providers = providers
        self._gateway = gateway

    def list(self) -> list[dict[str, Any]]:
        return self._db.fetch_all("SELECT * FROM conversations ORDER BY updated_at DESC")

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        conv = self._db.fetch_one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if conv is None:
            return None
        rows = self._db.fetch_all(
            "SELECT id, role, content, events, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        )
        conv["messages"] = [{**r, "events": json.loads(r["events"])} for r in rows]
        return conv

    def create(self, title: str | None = None, provider_id: str | None = None, model: str | None = None) -> dict[str, Any]:
        conversation_id = uuid.uuid4().hex
        now = utc_now()
        self._db.execute(
            "INSERT INTO conversations(id, title, provider_id, model, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
            (conversation_id, title or "新對話", provider_id, model, now, now),
        )
        return self.get(conversation_id)  # type: ignore[return-value]

    def update(self, conversation_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {k: v for k, v in fields.items() if k in ("title", "provider_id", "model")}
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            self._db.execute(
                f"UPDATE conversations SET {sets} WHERE id = ?",  # noqa: S608 - 欄位名稱來自白名單
                (*allowed.values(), conversation_id),
            )
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        return self._db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)) > 0

    def _append(self, conversation_id: str, role: str, content: str, events: list[dict[str, Any]]) -> None:
        now = utc_now()
        self._db.insert(
            "INSERT INTO messages(conversation_id, role, content, events, created_at) VALUES(?, ?, ?, ?, ?)",
            (conversation_id, role, content, json.dumps(events, ensure_ascii=False), now),
        )
        self._db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))

    async def send(
        self,
        conversation_id: str,
        content: str,
        *,
        provider_id: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
    ) -> AsyncIterator[ChatEvent]:
        conv = self.get(conversation_id)
        if conv is None:
            yield ChatEvent.error("找不到對話")
            return

        # 對話中切換 Provider／模型時記住選擇
        provider_id = provider_id or conv["provider_id"]
        model = model if model is not None else conv["model"]
        try:
            provider, config = self._providers.build(provider_id)
        except LookupError as exc:
            yield ChatEvent.error(str(exc))
            return

        updates: dict[str, Any] = {"provider_id": config.id, "model": model or None}
        if not conv["messages"]:
            updates["title"] = content.strip().splitlines()[0][:TITLE_MAX_CHARS] if content.strip() else "新對話"
        self.update(conversation_id, **updates)

        history = [
            ChatMessage(role=m["role"], content=m["content"])
            for m in conv["messages"]
            if m["role"] in ("user", "assistant") and m["content"]
        ]
        history.append(ChatMessage(role="user", content=content))
        self._append(conversation_id, "user", content, [])

        request = ChatRequest(messages=history, system=_system_prompt(), model=model or None, cwd=cwd)
        text_parts: list[str] = []
        # 保存工具呼叫與錯誤，重新開啟對話時可顯示過程
        saved_events: list[dict[str, Any]] = []
        try:
            async for event in self._gateway.run(provider, request):
                if event.type == "text_delta":
                    text_parts.append(event.data.get("text", ""))
                elif event.type in ("tool_call", "tool_result", "error", "done"):
                    saved_events.append({"type": event.type, **event.data})
                yield event
        finally:
            # 包含使用者中斷的情況，已產生的部分回覆仍會保存
            text = "".join(text_parts)
            if text or saved_events:
                self._append(conversation_id, "assistant", text, saved_events)
