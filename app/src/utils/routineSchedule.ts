export type ScheduleFrequency = 'manual' | 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'custom'

export const WEEK_DAYS = ['週日', '週一', '週二', '週三', '週四', '週五', '週六']

export function parseSchedule(schedule: string | null) {
  const defaults = { time: '08:30', weekDay: '1', monthDay: '1' }
  if (!schedule) {
    return { ...defaults, frequency: 'manual' as ScheduleFrequency }
  }
  const fields = schedule.trim().split(/\s+/)
  const [minute, hour, day, month, week] = fields
  const isTime = /^\d{1,2}$/.test(minute) && Number(minute) < 60
    && /^\d{1,2}$/.test(hour) && Number(hour) < 24
  if (fields.length === 5 && isTime && month === '*') {
    const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
    if (day === '*' && (week === '*' || week === '1-5')) {
      return { ...defaults, time, frequency: (week === '*' ? 'daily' : 'weekdays') as ScheduleFrequency }
    }
    if (day === '*' && /^[0-7]$/.test(week)) {
      return { ...defaults, time, weekDay: String(Number(week) % 7), frequency: 'weekly' as ScheduleFrequency }
    }
    if (week === '*' && /^\d{1,2}$/.test(day) && Number(day) >= 1 && Number(day) <= 31) {
      return { ...defaults, time, monthDay: String(Number(day)), frequency: 'monthly' as ScheduleFrequency }
    }
  }
  return { ...defaults, frequency: 'custom' as ScheduleFrequency }
}

export function buildSchedule(frequency: ScheduleFrequency, time: string, weekDay: string, monthDay: string) {
  const [hour, minute] = time.split(':').map(Number)
  if (frequency === 'manual') {
    return null
  }
  const day = frequency === 'monthly' ? monthDay : '*'
  let week = '*'
  if (frequency === 'weekly') {
    week = weekDay
  } else if (frequency === 'weekdays') {
    week = '1-5'
  }
  return `${minute} ${hour} ${day} * ${week}`
}

export function describeSchedule(schedule: string | null): string {
  const { frequency, time, weekDay, monthDay } = parseSchedule(schedule)
  const descriptions = {
    manual: '手動執行', daily: `每天 ${time}`, weekdays: `週一至週五 ${time}`,
    weekly: `每${WEEK_DAYS[Number(weekDay)]} ${time}`, monthly: `每月 ${monthDay} 日 ${time}`,
    custom: `自訂排程（${schedule}）`,
  }
  return descriptions[frequency]
}
