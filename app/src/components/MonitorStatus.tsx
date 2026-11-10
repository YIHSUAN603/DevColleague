import { useEffect, useState } from 'react'
import { get } from '../api/backend'
import { errorMessage } from '../utils'

interface PollStatus {
  at: string
  events: number
  errors: { connection_id: string; message: string }[]
}

export function MonitorStatus() {
  const [status, setStatus] = useState<PollStatus | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let isCancelled = false
    const load = () => get<{ last_poll: PollStatus | null }>('/event-monitor')
      .then((response) => { if (!isCancelled) { setStatus(response.last_poll); setError('') } })
      .catch((err) => { if (!isCancelled) { setError(errorMessage(err)) } })
    void load()
    const timer = setInterval(() => void load(), 15000)
    return () => { isCancelled = true; clearInterval(timer) }
  }, [])
  return <div>
    {error && <p className="error">{error}</p>}
    {status && <p className="muted small">上次檢查：{new Date(status.at).toLocaleString()} · 新事件 {status.events} 筆</p>}
    {status?.errors.map((item, index) => <p className="alert warn" key={index}>{item.connection_id}：{item.message}</p>)}
  </div>
}
