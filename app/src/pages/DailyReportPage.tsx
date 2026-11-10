import { useEffect, useMemo, useRef, useState } from 'react'
import { get, put, stream } from '../api/backend'
import type { ConnectionError, DailyReport, DailyReportSettingsResponse } from '../api/types'
import { Markdown } from '../components/Markdown'
import { ProviderPicker } from '../components/ProviderPicker'
import { errorMessage } from '../utils'

interface DailyActivity {
  projects: { name: string; progress: unknown[]; work_items_changed: unknown[]; pull_requests: unknown[]; commits: unknown[] }[]
  previous_report: { date: string } | null
  errors: ConnectionError[]
}

interface Section {
  title: string
  body: string
}

/** 依 Markdown 標題把日報告切成各專案的區塊，方便逐一複製到文件中對應的位置 */
function splitSections(markdown: string): Section[] {
  const sections: Section[] = []
  let current: Section | null = null
  for (const line of markdown.split('\n')) {
    const heading = /^#{1,6}\s+(.*)$/.exec(line)
    if (heading) {
      current = { title: heading[1].trim(), body: '' }
      sections.push(current)
    } else if (current) {
      current.body += line + '\n'
    } else if (line.trim()) {
      current = { title: '', body: line + '\n' }
      sections.push(current)
    }
  }
  return sections
}

function count(activity: DailyActivity, key: 'progress' | 'work_items_changed' | 'pull_requests' | 'commits'): number {
  return activity.projects.reduce((n, p) => n + p[key].length, 0)
}

/** 單一專案的預覽；複製時同時放入 HTML 與純文字，貼到 Google 文件可保留巢狀清單與連結 */
function SectionPreview({ section }: { section: Section }) {
  const ref = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    const plain = section.body.trim()
    const html = ref.current?.querySelector('.markdown')?.innerHTML ?? ''
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([html], { type: 'text/html' }),
          'text/plain': new Blob([plain], { type: 'text/plain' }),
        }),
      ])
    } catch {
      // 環境不支援寫入 HTML 時，退回純文字
      await navigator.clipboard.writeText(plain)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="daily-section" ref={ref}>
      <div className="card-title">
        <h3>{section.title || '（未分段）'}</h3>
        <button onClick={() => void copy()} disabled={!section.body.trim()}>
          {copied ? '已複製' : '複製'}
        </button>
      </div>
      <Markdown text={section.body} />
    </div>
  )
}

export function DailyReportPage() {
  const [day, setDay] = useState('')
  const [notes, setNotes] = useState('')
  const [content, setContent] = useState('')
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [hasProjects, setHasProjects] = useState(true)
  const [providerId, setProviderId] = useState<string | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [activity, setActivity] = useState<DailyActivity | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    get<DailyReportSettingsResponse>('/daily-report/settings')
      .then((s) => {
        setDay(s.default_date)
        setHasProjects(s.projects.length > 0)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [])

  // 切換日期時載入該日已儲存的內容
  useEffect(() => {
    if (!day) return
    let cancelled = false
    setActivity(null)
    get<DailyReport>(`/daily-report/reports/${day}`)
      .then((r) => {
        if (cancelled) return
        setContent(r.content)
        setNotes(r.notes)
        setSavedAt(r.updated_at)
        setDirty(false)
      })
      .catch((err) => !cancelled && setError(errorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [day])

  const save = async (text: string) => {
    const saved = await put<DailyReport>(`/daily-report/reports/${day}`, { content: text, notes })
    setSavedAt(saved.updated_at)
    setDirty(false)
  }

  const generate = async () => {
    setRunning(true)
    setError(null)
    setContent('')
    setActivity(null)
    const controller = new AbortController()
    abortRef.current = controller
    let full = ''
    let failed = false
    try {
      await stream(
        '/daily-report/generate',
        { day, notes, provider_id: providerId, model },
        (event) => {
          if (event.type === 'activity') setActivity(event.data as unknown as DailyActivity)
          else if (event.type === 'text_delta') {
            full += String(event.data.text ?? '')
            setContent(full)
          } else if (event.type === 'error') {
            failed = true
            setError(String(event.data.message))
          }
        },
        controller.signal,
      )
      // 產生完成後自動儲存，下一次產生時才能延續這份內容
      if (!failed && full.trim()) await save(full)
      else setDirty(true)
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err))
      setDirty(true)
    } finally {
      setRunning(false)
    }
  }

  const sections = useMemo(() => splitSections(content), [content])

  return (
    <div className="page">
      <div className="page-header">
        <h2>日報告</h2>
      </div>
      {!hasProjects && (
        <div className="alert warn">尚未設定日報告的專案對應，所有活動會歸在「未分類」。請到「設定」新增專案對應。</div>
      )}
      <div className="card">
        <div className="form-row">
          <label>
            報告日期
            <input type="date" value={day} disabled={running} onChange={(e) => setDay(e.target.value)} />
          </label>
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
            <button className="primary" disabled={!day} onClick={() => void generate()}>
              {content ? '重新產生' : '產生日報告'}
            </button>
          )}
        </div>
        <label className="daily-notes">
          手動補充（會議、包版、待確認問題等，會連同平台資料一起送給 LLM 整理）
          <textarea
            rows={3}
            value={notes}
            disabled={running}
            onChange={(e) => {
              setNotes(e.target.value)
              setDirty(true)
            }}
            placeholder="例如：海洋：內部進度會議；公路局：測試站台架設完成，養護事件章節待確認"
          />
        </label>
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
            已彙整並送給 LLM 的資料：commit {count(activity, 'commits')} 筆、PR {count(activity, 'pull_requests')} 筆、
            異動的 Work Item {count(activity, 'work_items_changed')} 筆、由 Work Item 算出進度的工作項目{' '}
            {count(activity, 'progress')} 筆
            {activity.previous_report ? `、前一份日報告（${activity.previous_report.date}）` : ''}
          </summary>
          <pre>{JSON.stringify(activity, null, 2)}</pre>
        </details>
      )}

      {/* 內容被清空時仍保留編輯區，才能把空白內容存回去 */}
      {(content || running || dirty || savedAt) && (
        <div className="daily-grid">
          <div className="card">
            <div className="card-title">
              <h3>編輯（Markdown）</h3>
              <span className="toolbar">
                <span className="muted small">
                  {running ? '撰寫中…' : dirty ? '尚未儲存' : savedAt ? `已儲存 ${new Date(savedAt).toLocaleString()}` : ''}
                </span>
                <button
                  disabled={running || !dirty}
                  onClick={() => void save(content).catch((err) => setError(errorMessage(err)))}
                >
                  儲存
                </button>
              </span>
            </div>
            <textarea
              className="daily-editor"
              value={content}
              readOnly={running}
              onChange={(e) => {
                setContent(e.target.value)
                setDirty(true)
              }}
            />
          </div>
          <div className="card">
            <div className="card-title">
              <h3>預覽</h3>
              <span className="muted small">逐一複製各專案的內容，貼到日報告文件中自己的位置</span>
            </div>
            {sections.length === 0 && (
              <p className="muted">{!running ? '（沒有內容）' : activity ? '撰寫中…' : '正在彙整活動資料…'}</p>
            )}
            {sections.map((s, i) => (
              <SectionPreview key={i} section={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
