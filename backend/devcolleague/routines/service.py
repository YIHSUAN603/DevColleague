"""背景助理執行與 Routine 變更提案。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from ..llm.gateway import Gateway
from ..llm.registry import ProviderService
from ..llm.types import ChatMessage, ChatRequest
from ..services.conversations import ConversationService, _system_prompt
from ..services.events import NotificationService, NotificationTarget
from ..services.memory import MemoryService
from ..storage.db import Database, utc_now
from ..tools.context import ExecutionContext, execution_context
from .definitions import Routine, RoutineStore


class RoutineService:
    def __init__(self, db: Database, store: RoutineStore, providers: ProviderService, gateway: Gateway,
                 conversations: ConversationService, memory: MemoryService, notifications: NotificationService) -> None:
        self.db, self.store, self.providers, self.gateway = db, store, providers, gateway
        self.conversations, self.memory, self.notifications = conversations, memory, notifications
        self.active: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.db.execute("UPDATE assistant_runs SET status='interrupted',finished_at=?,error='應用程式已中斷' WHERE status='running'", (utc_now(),))

    def last_run(self, routine_id: str) -> str | None:
        row = self.db.fetch_one("SELECT started_at FROM assistant_runs WHERE routine_id=? ORDER BY started_at DESC LIMIT 1", (routine_id,))
        return row["started_at"] if row else None

    def runs(self) -> list[dict]:
        return self.db.fetch_all("SELECT id,routine_id,trigger,conversation_id,started_at,finished_at,status,error FROM assistant_runs ORDER BY started_at DESC LIMIT 100")

    def run_detail(self, run_id: str) -> dict | None:
        row = self.db.fetch_one("SELECT * FROM assistant_runs WHERE id=?", (run_id,))
        if row:
            row["events"] = json.loads(row["events"])
        return row

    def start(self, routine: Routine, trigger: str = "manual") -> dict:
        if routine.id in self.active:
            raise ValueError("這個例行流程正在執行")
        run = self.prepare(routine, trigger)
        task = asyncio.create_task(self.execute(routine, run))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return run

    def prepare(self, routine: Routine, trigger: str) -> dict:
        prompt = routine.render(datetime.now().astimezone(), self.last_run(routine.id))
        conv = self.conversations.create(routine.name, routine.provider, routine.model)
        run_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO assistant_runs(id,routine_id,trigger,conversation_id,started_at,status,prompt) VALUES(?,?,?,?,?,'running',?)",
            (run_id, routine.id, trigger, conv["id"], utc_now(), prompt),
        )
        self.active.add(routine.id)
        return self.run_detail(run_id)

    async def execute(self, routine: Routine, run: dict) -> None:
        context = ExecutionContext(unattended=True, conversation_id=run["conversation_id"],
                                   allow_side_effects=tuple(routine.allow_side_effects))
        token = execution_context.set(context)
        text, saved_events, error = [], [], None
        system = _system_prompt() + self.memory.context(run["prompt"])
        self.db.execute("UPDATE assistant_runs SET system_prompt=? WHERE id=?", (system, run["id"]))
        self.conversations._append(run["conversation_id"], "user", run["prompt"], [])
        try:
            provider, config = self.providers.build(routine.provider)
            self.conversations.update(run["conversation_id"], provider_id=config.id)
            request = ChatRequest(messages=[ChatMessage(role="user", content=run["prompt"])], system=system, model=routine.model)
            async with asyncio.timeout(600):
                async for event in self.gateway.run(provider, request):
                    if event.type == "text_delta":
                        text.append(event.data.get("text", ""))
                    else:
                        saved_events.append({"type": event.type, **event.data})
                    if event.type == "error":
                        error = event.data.get("message", "執行失敗")
            if not any(e["type"] == "done" for e in saved_events) and not error:
                error = "模型未正常結束回應"
        except asyncio.CancelledError:
            error = "應用程式停止，執行已中斷"
            raise
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        finally:
            self.conversations._append(run["conversation_id"], "assistant", "".join(text), saved_events)
            self.db.execute(
                "UPDATE assistant_runs SET status=?,finished_at=?,events=?,error=? WHERE id=?",
                ("failed" if error else "succeeded", utc_now(), json.dumps({"stream": saved_events, "tools": context.audit}, ensure_ascii=False), error, run["id"]),
            )
            self.active.discard(routine.id)
            execution_context.reset(token)
        if routine.notify or error:
            self.notifications.notify(routine.name + ("執行失敗" if error else "已完成"),
                                      error or "".join(text)[-200:] or "點擊查看結果",
                                      NotificationTarget("chat", run["conversation_id"]))

    def propose(self, routine: Routine, conversation_id: str) -> dict:
        proposal_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO routine_proposals(id,conversation_id,definition,created_at) VALUES(?,?,?,?)",
                        (proposal_id, conversation_id, routine.model_dump_json(), utc_now()))
        return {"proposal_id": proposal_id, "requires_confirmation": True, "definition": routine.model_dump(),
                "message": "尚未套用，請使用者在確認卡片按下套用或拒絕。"}

    def proposals(self, conversation_id: str) -> list[dict]:
        rows = self.db.fetch_all("SELECT * FROM routine_proposals WHERE conversation_id=? ORDER BY created_at", (conversation_id,))
        return [{**r, "definition": json.loads(r["definition"])} for r in rows]

    def resolve(self, proposal_id: str, approve: bool) -> dict:
        row = self.db.fetch_one("SELECT * FROM routine_proposals WHERE id=?", (proposal_id,))
        if not row or row["status"] != "pending":
            raise ValueError("提案不存在或已處理")
        if approve:
            routine = self.store.save(Routine.model_validate_json(row["definition"]))
            self.db.set_setting(f"routine.cursor.{routine.id}", utc_now())
        status = "approved" if approve else "rejected"
        self.db.execute("UPDATE routine_proposals SET status=? WHERE id=?", (status, proposal_id))
        routine = Routine.model_validate_json(row["definition"])
        self.conversations._append(row["conversation_id"], "user",
            f"我已在確認卡片{'套用' if approve else '拒絕'}例行流程「{routine.name}」的設定。", [])
        self.conversations._append(row["conversation_id"], "assistant",
            f"例行流程「{routine.name}」{'已儲存設定' if approve else '未套用變更'}。", [])
        return {"status": status}

    async def close(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.db.execute("UPDATE assistant_runs SET status='interrupted',finished_at=?,error='應用程式已停止' WHERE status='running'", (utc_now(),))
        self.active.clear()
