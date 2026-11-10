import { useCallback, useEffect, useState } from 'react'
import { del, get, post, put } from '../api/backend'
import type { Memory } from '../api/assistantTypes'
import { errorMessage } from '../utils'

const MEMORY_KINDS: Record<string, string> = {
  preference: '偏好', work: '進行中的工作', decision: '設計決策', note: '踩坑筆記',
}

export function MemoryPage() {
  const [items, setItems] = useState<Memory[]>([])
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<Memory | null>(null)
  const [content, setContent] = useState('')
  const [kind, setKind] = useState('note')
  const [error, setError] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  const load = useCallback(async () => {
    setItems(await get<Memory[]>(`/memories?q=${encodeURIComponent(query)}`))
  }, [query])

  useEffect(() => {
    let isCancelled = false
    get<Memory[]>(`/memories?q=${encodeURIComponent(query)}`)
      .then((rows) => { if (!isCancelled) { setItems(rows) } })
      .catch((err) => { if (!isCancelled) { setError(errorMessage(err)) } })
    return () => { isCancelled = true }
  }, [query])

  const save = async () => {
    setIsSaving(true)
    setError('')
    try {
      if (editing) { await put(`/memories/${editing.id}`, { kind, content }) }
      else { await post('/memories', { kind, content }) }
      setEditing(null)
      setContent('')
      await load()
    } catch (err) { setError(errorMessage(err)) }
    finally { setIsSaving(false) }
  }

  const remove = async (id: string) => {
    try { await del(`/memories/${id}`); await load() }
    catch (err) { setError(errorMessage(err)) }
  }

  return <div className="page">
    <div className="page-header"><h2>個人記憶</h2>
      <input aria-label="搜尋記憶" placeholder="搜尋記憶" value={query} onChange={(e) => setQuery(e.target.value)} />
    </div>
    <p className="muted">對話與背景助理會引用相關記憶與偏好。請勿填入 Token、API Key 或密碼。</p>
    {error && <div className="alert error">{error}</div>}
    <div className="card">
      <h3>{editing ? '編輯記憶' : '新增記憶'}</h3>
      <div className="form">
        <label>類型<select value={kind} onChange={(e) => setKind(e.target.value)}>
          {Object.entries(MEMORY_KINDS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select></label>
        <label>內容<textarea rows={4} value={content} onChange={(e) => setContent(e.target.value)} /></label>
        <div className="toolbar">
          <button className="primary" disabled={!content.trim() || isSaving} onClick={() => void save()}>儲存</button>
          {editing && <button onClick={() => { setEditing(null); setContent('') }}>取消編輯</button>}
        </div>
      </div>
    </div>
    {!items.length && <p className="muted">尚無符合的記憶。</p>}
    {items.map((item) => <div className="card" key={item.id}>
      <div className="card-title"><h3>{MEMORY_KINDS[item.kind]}</h3><div className="toolbar">
        <button onClick={() => { setEditing(item); setKind(item.kind); setContent(item.content) }}>編輯</button>
        <button onClick={() => void remove(item.id)}>刪除</button>
      </div></div>
      <p className="memory-content">{item.content}</p>
      <small className="muted">來源：{item.source} · {new Date(item.updated_at).toLocaleString()}</small>
    </div>)}
  </div>
}
