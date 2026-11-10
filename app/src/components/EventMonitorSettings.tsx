import { useEffect, useState } from 'react'
import { get, put } from '../api/backend'
import { errorMessage } from '../utils'
import { ProviderPicker } from './ProviderPicker'
import { MonitorStatus } from './MonitorStatus'

interface MonitorSettings {
  enabled: boolean
  interval_minutes: number
  stale_pr_days: number
  daily_limit: number
  provider: string | null
  model: string | null
}

export function EventMonitorSettings() {
  const [value, setValue] = useState<MonitorSettings | null>(null)
  const [message, setMessage] = useState('')
  const [isSaving, setIsSaving] = useState(false)
  useEffect(() => {
    get<{ settings: MonitorSettings }>('/event-monitor').then((response) => setValue(response.settings))
      .catch((err) => setMessage(errorMessage(err)))
  }, [])
  const save = async () => {
    setIsSaving(true)
    try { await put('/event-monitor', value); setMessage('已儲存事件提醒設定') }
    catch (err) { setMessage(errorMessage(err)) }
    finally { setIsSaving(false) }
  }
  if (!value) { return <p>{message || '載入事件提醒設定…'}</p> }
  return <div className="card">
    <h3>事件提醒</h3><p className="muted">平台有新事件時才喚醒助理。達到每日上限後改用固定格式通知。</p>
    <div className="form">
      <label className="inline"><input type="checkbox" checked={value.enabled}
        onChange={(e) => setValue({ ...value, enabled: e.target.checked })} />啟用背景事件提醒</label>
      <label>檢查間隔（分鐘）<input type="number" min={1} max={1440} value={value.interval_minutes}
        onChange={(e) => setValue({ ...value, interval_minutes: Number(e.target.value) })} /></label>
      <label>PR 擱置天數<input type="number" min={1} max={365} value={value.stale_pr_days}
        onChange={(e) => setValue({ ...value, stale_pr_days: Number(e.target.value) })} /></label>
      <label>每日喚醒 LLM 上限<input type="number" min={0} max={1000} value={value.daily_limit}
        onChange={(e) => setValue({ ...value, daily_limit: Number(e.target.value) })} /></label>
      <ProviderPicker providerId={value.provider} model={value.model}
        onChange={(provider, model) => setValue({ ...value, provider, model })} />
      <button disabled={isSaving} onClick={() => void save()}>儲存事件提醒設定</button>
      {message && <p role="status">{message}</p>}
    </div>
    <MonitorStatus />
  </div>
}
