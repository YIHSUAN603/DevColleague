import { useCallback, useEffect, useState } from 'react'
import { del, get, post, put } from '../api/backend'
import type { AssistantRun, Routine } from '../api/assistantTypes'
import { EventMonitorSettings } from '../components/EventMonitorSettings'
import { RoutineForm } from '../components/RoutineForm'
import { errorMessage } from '../utils'
import { describeSchedule } from '../utils/routineSchedule'

const EMPTY_ROUTINE: Routine = {
  id: '', name: '', prompt: '', schedule: null, command: null, provider: null, model: null,
  enabled: false, notify: true, catch_up: true, allow_side_effects: [],
}
const RUN_LABELS: Record<string, string> = {
  running: '執行中', succeeded: '已完成', failed: '失敗', interrupted: '已中斷',
}

export function RoutinesPage({ onOpenConversation }: { onOpenConversation: (id: string) => void }) {
  const [items, setItems] = useState<Routine[]>([])
  const [runs, setRuns] = useState<AssistantRun[]>([])
  const [editing, setEditing] = useState<Routine | null>(null)
  const [detail, setDetail] = useState<AssistantRun | null>(null)
  const [error, setError] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const [fileErrors, setFileErrors] = useState<string[]>([])
  const load = useCallback(async () => {
    const [routines, history, diagnostics] = await Promise.all([
      get<Routine[]>('/routines'), get<AssistantRun[]>('/assistant-runs'), get<string[]>('/routine-errors'),
    ])
    setItems(routines)
    setRuns(history)
    setFileErrors(diagnostics)
  }, [])
  useEffect(() => {
    void load().catch((err) => setError(errorMessage(err)))
    const timer = setInterval(() => void load().catch((err) => setError(errorMessage(err))), 5000)
    return () => clearInterval(timer)
  }, [load])

  const save = async (routine: Routine) => {
    setIsSaving(true)
    setError('')
    try { await put(`/routines/${routine.id}`, routine); setEditing(null); await load() }
    catch (err) { setError(errorMessage(err)) }
    finally { setIsSaving(false) }
  }
  const run = async (id: string) => {
    setError('')
    try { await post(`/routines/${id}/run`); await load() }
    catch (err) { setError(errorMessage(err)) }
  }
  const remove = async (id: string) => {
    try { await del(`/routines/${id}`); await load() }
    catch (err) { setError(errorMessage(err)) }
  }
  const inspect = async (id: string) => {
    try { setDetail(await get<AssistantRun>(`/assistant-runs/${id}`)) }
    catch (err) { setError(errorMessage(err)) }
  }

  return <div className="page">
    <div className="page-header"><h2>背景助理</h2>
      <button className="primary" onClick={() => { setError(''); setEditing({ ...EMPTY_ROUTINE }) }}>新增例行流程</button></div>
    <p className="muted">內建範本預設停用。設定 Provider 與排程後啟用，即可在系統匣背景執行。</p>
    {error && <div className="alert error">{error}</div>}
    {fileErrors.map((message) => <div className="alert error" key={message}>{message}</div>)}
    {editing && <RoutineForm key={editing.id} initial={editing} isSaving={isSaving}
      onSave={(routine) => void save(routine)} onCancel={() => { setError(''); setEditing(null) }} />}
    <div className="card"><h3>例行流程</h3><ul className="settings-list">
      {items.map((item) => <li key={item.id}><div><strong>{item.name}</strong> <span className="badge">
          {item.enabled && item.schedule ? '排程已啟用' : '自動排程未啟用'}</span>
        <div className="muted small">{describeSchedule(item.schedule)} · {item.command || '無對話指令'}</div></div>
        <div className="row-actions">
          <button disabled={runs.some((r) => r.routine_id === item.id && r.status === 'running')}
            onClick={() => void run(item.id)}>立即執行</button>
          <button onClick={() => { setError(''); setEditing(item) }}>編輯</button>
          <button onClick={() => void remove(item.id)}>刪除</button>
        </div></li>)}
    </ul></div>
    <EventMonitorSettings />
    <div className="card"><h3>執行紀錄</h3>
      {!runs.length && <p className="muted">尚無執行紀錄。</p>}
      <ul className="settings-list">{runs.map((item) => <li key={item.id}>
        <div><strong>{item.routine_id}</strong> · {RUN_LABELS[item.status] ?? item.status}
          <div className="muted small">{new Date(item.started_at).toLocaleString()} · {item.trigger}</div>
          {item.error && <div className="error">{item.error}</div>}</div>
        <div className="row-actions"><button onClick={() => void inspect(item.id)}>查看送出資料</button>
          <button onClick={() => onOpenConversation(item.conversation_id)}>開啟對話</button></div>
      </li>)}</ul>
    </div>
    {detail && <div className="card"><div className="card-title"><h3>送給 LLM 的資料與工具紀錄</h3>
      <button onClick={() => setDetail(null)}>關閉</button></div>
      <details open><summary>系統提示與記憶</summary><pre>{detail.system_prompt}</pre></details>
      <details open><summary>執行提示</summary><pre>{detail.prompt}</pre></details>
      <details open><summary>工具呼叫與結果</summary><pre>{JSON.stringify(detail.events, null, 2)}</pre></details>
    </div>}
  </div>
}
