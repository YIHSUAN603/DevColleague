import { useRef, useState } from 'react'
import { stream } from '../api/backend'
import { Markdown } from '../components/Markdown'
import { ProviderPicker } from '../components/ProviderPicker'
import { errorMessage } from '../utils'

type Range = 'today' | 'yesterday' | 'this_week' | 'custom'
type Report = 'standup' | 'weekly'

interface Activity {
  commits: unknown[]
  pull_requests: unknown[]
  work_items_changed: unknown[]
  work_items_assigned_open: unknown[]
  errors: { connection: string; message: string }[]
}

function today(): string {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

export function SummaryPage() {
  const [range, setRange] = useState<Range>('yesterday')
  const [report, setReport] = useState<Report>('standup')
  const [start, setStart] = useState(today())
  const [end, setEnd] = useState(today())
  const [providerId, setProviderId] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [activity, setActivity] = useState<Activity | null>(null)
  const [text, setText] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const generate = async () => {
    setRunning(true)
    setError(null)
    setText('')
    setActivity(null)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await stream(
        '/summary',
        {
          range,
          report,
          start: range === 'custom' ? start : null,
          end: range === 'custom' ? end : null,
          provider_id: providerId,
          model,
        },
        (event) => {
          if (event.type === 'activity') setActivity(event.data as unknown as Activity)
          else if (event.type === 'text_delta') setText((t) => t + String(event.data.text ?? ''))
          else if (event.type === 'error') setError(String(event.data.message))
        },
        controller.signal,
      )
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err))
    } finally {
      setRunning(false)
    }
  }

  const copy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="page">
      <div className="page-header">
        <h2>工作摘要</h2>
      </div>
      <div className="card form-row">
        <label>
          報告類型
          <select value={report} onChange={(e) => setReport(e.target.value as Report)}>
            <option value="standup">站會報告</option>
            <option value="weekly">週報</option>
          </select>
        </label>
        <label>
          時間區間
          <select value={range} onChange={(e) => setRange(e.target.value as Range)}>
            <option value="today">今日</option>
            <option value="yesterday">昨日</option>
            <option value="this_week">本週</option>
            <option value="custom">自訂</option>
          </select>
        </label>
        {range === 'custom' && (
          <>
            <label>
              開始
              <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
            </label>
            <label>
              結束
              <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
            </label>
          </>
        )}
        <ProviderPicker
          providerId={providerId}
          model={model}
          disabled={running}
          onChange={(p, m) => {
            setProviderId(p)
            setModel(m)
          }}
        />
        {running ? (
          <button onClick={() => abortRef.current?.abort()}>停止</button>
        ) : (
          <button className="primary" onClick={() => void generate()}>
            產生報告
          </button>
        )}
      </div>

      {error && <div className="alert error">{error}</div>}
      {activity?.errors.map((e, i) => (
        <div key={i} className="alert warn">
          連線「{e.connection}」：{e.message}
        </div>
      ))}

      {activity && (
        <details className="card disclosure">
          <summary>
            已彙整並送給 LLM 的資料：commit {activity.commits.length} 筆、PR {activity.pull_requests.length} 筆、
            異動的 Work Item {activity.work_items_changed.length} 筆、進行中的 Work Item{' '}
            {activity.work_items_assigned_open.length} 筆
          </summary>
          <pre>{JSON.stringify(activity, null, 2)}</pre>
        </details>
      )}

      {(text || running) && (
        <div className="card">
          <div className="card-actions">
            <button onClick={() => void copy()} disabled={!text || running}>
              {copied ? '已複製' : '複製為 Markdown'}
            </button>
          </div>
          {text ? <Markdown text={text} /> : <p className="muted">{activity ? '撰寫中…' : '正在彙整活動資料…'}</p>}
        </div>
      )}
    </div>
  )
}
