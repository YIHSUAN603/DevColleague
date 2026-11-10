import { useState } from 'react'
import { buildSchedule, describeSchedule, parseSchedule, WEEK_DAYS } from '../utils/routineSchedule'
import type { ScheduleFrequency } from '../utils/routineSchedule'

export function RoutineSchedule({ schedule, onChange }: {
  schedule: string | null; onChange: (schedule: string | null) => void
}) {
  const [selection, setSelection] = useState(() => parseSchedule(schedule))
  const { frequency, time, weekDay, monthDay } = selection
  const isSimple = frequency !== 'manual' && frequency !== 'custom'
  const update = (fields: Partial<typeof selection>) => {
    const next = { ...selection, ...fields }
    setSelection(next)
    if (next.frequency === 'custom') {
      onChange(schedule || '')
      return
    }
    onChange(buildSchedule(next.frequency, next.time, next.weekDay, next.monthDay))
  }
  return <fieldset className="routine-schedule">
    <legend>執行時間</legend>
    <div className="form-row">
      <label>重複頻率<select value={frequency}
        onChange={(event) => update({ frequency: event.target.value as ScheduleFrequency })}>
        <option value="manual">不排程，手動執行</option><option value="daily">每天</option>
        <option value="weekdays">工作日（週一至週五）</option><option value="weekly">每週</option>
        <option value="monthly">每月</option><option value="custom">進階：自訂 cron</option>
      </select></label>
      {frequency === 'weekly' && <label>星期<select value={weekDay}
        onChange={(event) => update({ weekDay: event.target.value })}>
        {WEEK_DAYS.map((day, index) => <option key={day} value={index}>{day}</option>)}
      </select></label>}
      {frequency === 'monthly' && <label>日期<select value={monthDay}
        onChange={(event) => update({ monthDay: event.target.value })}>
        {Array.from({ length: 31 }, (_, index) => <option key={index} value={index + 1}>{index + 1} 日</option>)}
      </select></label>}
      {isSimple && <label>時間<input type="time" required value={time}
        onChange={(event) => { if (event.target.value) { update({ time: event.target.value }) } }} /></label>}
    </div>
    {frequency === 'custom' && <label>五欄 cron<input required value={schedule ?? ''}
      placeholder="30 8 * * 1-5" onChange={(event) => onChange(event.target.value)} />
      <small>依序為：分鐘、小時、日期、月份、星期。例如 30 8 * * 1-5 表示週一至週五 08:30。</small>
    </label>}
    <p className="muted small" aria-live="polite">{describeSchedule(schedule)} · 使用本機時區</p>
    {frequency === 'monthly' && Number(monthDay) > 28
      && <small className="muted">沒有 {monthDay} 日的月份將略過執行。</small>}
  </fieldset>
}
