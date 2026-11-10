import { useState } from 'react'
import type { Routine } from '../api/assistantTypes'
import { ProviderPicker } from './ProviderPicker'
import { RoutineSchedule } from './RoutineSchedule'

export function RoutineForm({ initial, isSaving, onSave, onCancel }: {
  initial: Routine; isSaving: boolean; onSave: (routine: Routine) => void; onCancel: () => void
}) {
  const [value, setValue] = useState(initial)
  const update = (fields: Partial<Routine>) => setValue((current) => ({ ...current, ...fields }))
  return <form className="card" onSubmit={(event) => { event.preventDefault(); onSave(value) }}>
    <h3>例行流程設定</h3>
    <div className="form">
      <label>識別字（小寫英文、數字與連字號）<input required pattern="[a-z0-9][a-z0-9-]{0,63}"
        value={value.id} readOnly={Boolean(initial.id)} onChange={(e) => update({ id: e.target.value })} /></label>
      <label>名稱<input required value={value.name} onChange={(e) => update({ name: e.target.value })} /></label>
      <label>提示範本<textarea required rows={7} value={value.prompt} onChange={(e) => update({ prompt: e.target.value })} /></label>
      <small className="muted">可使用 {'{{today}}'}、{'{{yesterday}}'}、{'{{last_run_at}}'}。</small>
      <RoutineSchedule schedule={value.schedule}
        onChange={(schedule) => update({ schedule, enabled: Boolean(schedule) && value.enabled })} />
      <label>對話指令<input placeholder="/morning" value={value.command ?? ''}
        onChange={(e) => update({ command: e.target.value || null })} /></label>
      <ProviderPicker providerId={value.provider} model={value.model}
        onChange={(provider, model) => update({ provider, model })} />
      <small className="muted">未指定時使用預設 Provider。背景執行可使用平台查詢、草稿、記憶與通知工具。</small>
      <label className="inline"><input type="checkbox" checked={value.enabled && Boolean(value.schedule)} disabled={!value.schedule}
        onChange={(e) => update({ enabled: e.target.checked })} />啟用自動排程</label>
      <label className="inline"><input type="checkbox" checked={value.catch_up} onChange={(e) => update({ catch_up: e.target.checked })} />啟動時補跑錯過的排程一次</label>
      <label className="inline"><input type="checkbox" checked={value.notify} onChange={(e) => update({ notify: e.target.checked })} />完成後通知</label>
      <div className="toolbar"><button className="primary" disabled={isSaving}>儲存設定</button>
        <button type="button" onClick={onCancel}>取消</button></div>
    </div>
  </form>
}
