import { useEffect, useRef, useState } from 'react'
import { get, post, stream } from '../api/backend'
import type { Connection } from '../api/types'
import { DEVELOPMENT_MODES, INITIAL_DEVELOPMENT_INPUT } from '../api/developmentTypes'
import type { DevelopmentInput, DevelopmentMode, DevelopmentPreview, RepoInfo } from '../api/developmentTypes'
import { DevelopmentSource } from '../components/DevelopmentSource'
import { DevelopmentResult } from '../components/DevelopmentResult'
import { ProviderPicker } from '../components/ProviderPicker'
import { Markdown } from '../components/Markdown'
import { errorMessage } from '../utils'

function useDevelopment() {
  const [input, setInput] = useState<DevelopmentInput>(INITIAL_DEVELOPMENT_INPUT)
  const [repoInfo, setRepoInfo] = useState<RepoInfo | null>(null)
  const [preview, setPreview] = useState<DevelopmentPreview | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [isBusy, setIsBusy] = useState(false)
  const [isComplete, setIsComplete] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  useEffect(() => () => abortRef.current?.abort(), [])
  const change = (patch: Partial<DevelopmentInput>) => {
    setInput((previous) => ({ ...previous, ...patch }))
    setPreview(null)
    setText('')
    setError('')
    setIsComplete(false)
    if ('root' in patch) { setRepoInfo(null) }
  }
  const task = async (operation: () => Promise<void>) => {
    setIsBusy(true)
    setError('')
    try { await operation() } catch (err) { setError(errorMessage(err)) } finally { setIsBusy(false) }
  }
  return { input, repoInfo, setRepoInfo, preview, setPreview, text, setText, error, setError,
    isBusy, isComplete, setIsComplete, abortRef, change, task }
}

export function DevelopmentPage() {
  const state = useDevelopment()
  const [connections, setConnections] = useState<Connection[]>([])
  const { input, preview, isBusy, text, change, task, setError } = state
  useEffect(() => {
    get<Connection[]>('/connections').then(setConnections).catch((err) => setError(errorMessage(err)))
  }, [setError])
  const selectMode = (mode: DevelopmentMode) => {
    const source = ['summary', 'diagnose'].includes(mode) ? 'platform' : 'local'
    change({ mode, source })
  }
  const inspect = () => task(async () => {
    const info = await post<RepoInfo>('/development/repo', { root: input.root })
    change({ root: info.root, files: [] })
    state.setRepoInfo(info)
  })
  const loadPreview = () => task(async () => {
    state.setPreview(null)
    state.setText('')
    state.setIsComplete(false)
    state.setPreview(await post<DevelopmentPreview>('/development/preview', input))
  })
  const generate = () => task(async () => {
    if (!preview) { return }
    const controller = new AbortController()
    state.abortRef.current = controller
    state.setText('')
    state.setIsComplete(false)
    await stream('/development/generate', { preview_id: preview.id }, (event) => {
      if (event.type === 'text_delta') { state.setText((old) => old + String(event.data.text ?? '')) }
      if (event.type === 'error') { state.setError(String(event.data.message)) }
      if (event.type === 'done') { state.setIsComplete(true) }
    }, controller.signal)
  })
  const canPublish = state.isComplete && !state.error && !!input.connection_id && !!input.repo && (
    input.mode === 'pr' || (input.mode === 'review' && input.source === 'platform'))
  return <div className="page">
    <div className="page-header"><h2>開發助手</h2></div>
    <p className="muted">先載入並檢查資料，再送給所選模型分析。本機 Git 操作為唯讀。</p>
    <fieldset disabled={isBusy} className="card development-form">
      <div className="form-row"><label>功能<select aria-label="功能" value={input.mode}
        onChange={(e) => selectMode(e.target.value as DevelopmentMode)}>
        {DEVELOPMENT_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
      </select></label>
        <ProviderPicker providerId={input.provider_id} model={input.model}
          onChange={(provider_id, model) => change({ provider_id, model })} />
      </div>
      <DevelopmentSource input={input} repoInfo={state.repoInfo} connections={connections}
        onChange={change} onInspect={() => void inspect()} />
      {input.mode === 'review' && <ReviewRules value={input.rules} onChange={(rules) => change({ rules })} />}
      <button className="primary" onClick={() => void loadPreview()}>載入資料預覽</button>
    </fieldset>
    {state.error && <div className="alert error" role="alert">{state.error}</div>}
    {isBusy && <p role="status">處理中…</p>}
    {preview && <div className="card disclosure">
      <h3>即將送給模型的資料</h3>
      {preview.workspace_access && <p className="alert warn">已允許 CLI 唯讀查閱所選工作目錄，執行時可能再讀取其他檔案。</p>}
      {preview.truncated && <p className="alert warn">資料已截斷，分析只涵蓋下列範圍。</p>}
      <details open><summary>資料內容（30 分鐘內有效）</summary><pre>{preview.payload}</pre></details>
      <button className="primary" disabled={isBusy} onClick={() => void generate()}>確認資料並開始分析</button>
      {isBusy && <button onClick={() => state.abortRef.current?.abort()}>停止分析</button>}
    </div>}
    {text && isBusy && <div className="card"><Markdown text={text} /></div>}
    {text && !isBusy && <DevelopmentResult key={text} text={text} input={input} canPublish={canPublish} />}
  </div>
}

function ReviewRules({ value, onChange }: { value: string; onChange: (rules: string) => void }) {
  const [error, setError] = useState('')
  const load = async (file: File | undefined) => {
    if (!file) { return }
    if (file.size > 90000) { setError('規範檔不可超過 90 KB'); return }
    try {
      const text = await file.text()
      if (text.length > 30000) { throw new Error('規範不可超過 30000 字元') }
      onChange(text)
      setError('')
    } catch (err) { setError(errorMessage(err)) }
  }
  return <div className="daily-notes"><label>載入審查規範檔（Markdown／純文字）
    <input type="file" accept=".md,.txt" onChange={(e) => void load(e.target.files?.[0])} /></label>
    <label>自訂審查規範<textarea rows={5} maxLength={30000} value={value} onChange={(e) => onChange(e.target.value)} /></label>
    {error && <span className="error">{error}</span>}
  </div>
}
