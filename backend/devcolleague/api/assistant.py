"""第二階段的管理 API；所有寫入皆由使用者操作觸發。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..container import Container
from ..routines.definitions import Routine
from ..services.event_monitor import MonitorSettings
from ..services.memory import MemoryInput
from .deps import get_container, verify_session_token

router = APIRouter(prefix="/api", dependencies=[Depends(verify_session_token)])


@router.get("/memories")
async def memories(q: str | None = None, c: Container = Depends(get_container)):
    return c.memory.search(q) if q else c.memory.list()


@router.post("/memories")
async def create_memory(body: MemoryInput, c: Container = Depends(get_container)):
    try:
        return c.memory.save(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/memories/{memory_id}")
async def update_memory(memory_id: str, body: MemoryInput, c: Container = Depends(get_container)):
    if not c.db.fetch_one("SELECT id FROM memories WHERE id=?", (memory_id,)):
        raise HTTPException(404, "找不到記憶")
    try:
        return c.memory.save(body, memory_id=memory_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, c: Container = Depends(get_container)):
    return {"ok": c.memory.delete(memory_id)}


@router.get("/routines")
async def routines(c: Container = Depends(get_container)):
    return [r.model_dump() for r in c.routines.store.list()]


@router.get("/routine-errors")
async def routine_errors(c: Container = Depends(get_container)):
    c.routines.store.list()
    return c.routines.store.errors


@router.put("/routines/{routine_id}")
async def save_routine(routine_id: str, body: Routine, c: Container = Depends(get_container)):
    if body.id != routine_id:
        raise HTTPException(400, "路徑與 Routine id 不一致")
    try:
        result = c.routines.store.save(body)
        # 使用者更新排程後重新建立基準，不補跑更新前的時間。
        from ..storage.db import utc_now
        c.db.set_setting(f"routine.cursor.{routine_id}", utc_now())
        return result.model_dump()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/routines/{routine_id}")
async def delete_routine(routine_id: str, c: Container = Depends(get_container)):
    try:
        c.routines.store.delete(routine_id)
        return {"ok": True}
    except (ValueError, LookupError) as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/routines/{routine_id}/run")
async def run_routine(routine_id: str, c: Container = Depends(get_container)):
    try:
        return c.routines.start(c.routines.store.get(routine_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/assistant-runs")
async def runs(c: Container = Depends(get_container)):
    return c.routines.runs()


@router.get("/assistant-runs/{run_id}")
async def run_detail(run_id: str, c: Container = Depends(get_container)):
    result = c.routines.run_detail(run_id)
    if result is None:
        raise HTTPException(404, "找不到執行紀錄")
    return result


@router.get("/conversations/{conversation_id}/routine-proposals")
async def proposals(conversation_id: str, c: Container = Depends(get_container)):
    return c.routines.proposals(conversation_id)


class ProposalDecision(BaseModel):
    approve: bool


@router.post("/routine-proposals/{proposal_id}/resolve")
async def resolve(proposal_id: str, body: ProposalDecision, c: Container = Depends(get_container)):
    try:
        return c.routines.resolve(proposal_id, body.approve)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/event-monitor")
async def monitor_settings(c: Container = Depends(get_container)):
    return {"settings": c.monitor.settings().model_dump(),
            "last_poll": json.loads(c.db.get_setting("monitor.last_poll", "null"))}


@router.put("/event-monitor")
async def configure_monitor(body: MonitorSettings, c: Container = Depends(get_container)):
    return c.monitor.configure(body).model_dump()
