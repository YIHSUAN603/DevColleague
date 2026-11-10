import { useEffect, useState } from 'react'
import { get, post } from '../api/backend'
import type { RoutineProposal } from '../api/assistantTypes'
import { errorMessage } from '../utils'

export function RoutineProposals({ conversationId, isStreaming }: { conversationId: string; isStreaming: boolean }) {
  const [items, setItems] = useState<RoutineProposal[]>([])
  const [error, setError] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  useEffect(() => {
    let isCancelled = false
    get<RoutineProposal[]>(`/conversations/${conversationId}/routine-proposals`)
      .then((rows) => { if (!isCancelled) { setItems(rows) } })
      .catch((err) => { if (!isCancelled) { setError(errorMessage(err)) } })
    return () => { isCancelled = true }
  }, [conversationId, isStreaming])
  const resolve = async (id: string, approve: boolean) => {
    setIsSaving(true)
    try {
      await post(`/routine-proposals/${id}/resolve`, { approve })
      setItems(await get<RoutineProposal[]>(`/conversations/${conversationId}/routine-proposals`))
    } catch (err) { setError(errorMessage(err)) }
    finally { setIsSaving(false) }
  }
  return <div>
    {error && <div className="alert error">{error}</div>}
    {items.filter((item) => item.status !== 'pending').map((item) => <p className="muted small" key={item.id}>
      {item.definition.name}：{item.status === 'approved' ? '已套用設定' : '已拒絕變更'}
    </p>)}
    {items.filter((item) => item.status === 'pending').map((item) => <div className="card" key={item.id}>
      <h3>確認例行流程變更：{item.definition.name}</h3>
      <p>套用後會改變之後的自動行為，請先確認完整設定。</p>
      <details><summary>查看設定</summary><pre>{JSON.stringify(item.definition, null, 2)}</pre></details>
      <div className="toolbar"><button className="primary" disabled={isSaving || isStreaming}
        onClick={() => void resolve(item.id, true)}>套用變更</button>
        <button disabled={isSaving || isStreaming} onClick={() => void resolve(item.id, false)}>拒絕</button></div>
    </div>)}
  </div>
}
