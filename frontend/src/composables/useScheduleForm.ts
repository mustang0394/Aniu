import { reactive } from 'vue'

import type { ScheduleConfig } from '@/types'

type ScheduleLike = Pick<ScheduleConfig, 'id' | 'name' | 'run_type' | 'cron_expression' | 'task_prompt' | 'timeout_seconds' | 'enabled'>

type ScheduleKey = 'preMarket' | 'midday' | 'postMarket'
type SessionKey = 'morning' | 'afternoon'

export type TimePoint = { hour: number; minute: number }

export const RUN_COUNT_OPTIONS = [1, 2, 3, 4] as const

export interface ScheduleFormState {
  preMarket: { enabled: boolean; hour: number; minute: number; prompt: string }
  postMarket: { enabled: boolean; hour: number; minute: number; prompt: string }
  midday: { enabled: boolean; hour: number; minute: number; prompt: string }
  morning: { enabled: boolean; times: TimePoint[]; prompt: string }
  afternoon: { enabled: boolean; times: TimePoint[]; prompt: string }
}

const FIXED_TASK_NAMES = {
  preMarket: '盘前分析',
  midday: '午间复盘',
  postMarket: '收盘分析',
} as const

const SESSION_TASK_NAMES = {
  morning: '上午运行',
  afternoon: '下午运行',
} as const

const DEFAULT_TIMEOUT = 1800

/** 交易时段边界（含端点），单位：分钟自 00:00 起 */
const SESSION_RANGES: Record<SessionKey, { start: number; end: number; label: string }> = {
  morning: { start: 9 * 60 + 30, end: 11 * 60 + 30, label: '09:30–11:30' },
  afternoon: { start: 13 * 60, end: 15 * 60, label: '13:00–15:00' },
}

const defaultState = (): ScheduleFormState => ({
  preMarket: {
    enabled: false,
    hour: 8,
    minute: 0,
    prompt: '你正在执行盘前分析任务，请分析今日市场情况和持仓情况，做好今日市场走势预测，为你决策交易做好准备。',
  },
  postMarket: {
    enabled: false,
    hour: 15,
    minute: 30,
    prompt: '你正在执行收盘分析任务，请对今日市场和交易操作进行全面复盘，总结今日市场和明日可能的走势。',
  },
  midday: {
    enabled: false,
    hour: 12,
    minute: 0,
    prompt: '你正在执行午间复盘任务，请对上午市场和交易操作进行复盘，做好下午市场走势预测，为你决策交易做好准备。',
  },
  morning: {
    enabled: true,
    times: getDefaultSessionTimes('morning', 2),
    prompt: '你正在执行盘中交易操作，你的唯一目标是追求收益最大化。',
  },
  afternoon: {
    enabled: true,
    times: getDefaultSessionTimes('afternoon', 2),
    prompt: '你正在执行盘中交易操作，你的唯一目标是追求收益最大化。',
  },
})

function parseCron(cronExpression: string): TimePoint {
  const [minuteText = '0', hourText = '0'] = cronExpression.split(' ')
  const minute = Number(minuteText)
  const hour = Number(hourText)
  return {
    minute: Number.isFinite(minute) ? Math.min(59, Math.max(0, Math.trunc(minute))) : 0,
    hour: Number.isFinite(hour) ? Math.min(23, Math.max(0, Math.trunc(hour))) : 0,
  }
}

function buildCron(hour: number, minute: number) {
  return `${minute} ${hour} * * 1-5`
}

function clampHourMinute(hour: number, minute: number): TimePoint {
  return {
    hour: Math.min(23, Math.max(0, Math.trunc(Number(hour) || 0))),
    minute: Math.min(59, Math.max(0, Math.trunc(Number(minute) || 0))),
  }
}

function toMinutes(time: TimePoint): number {
  return time.hour * 60 + time.minute
}

function formatTime(time: TimePoint): string {
  return `${String(time.hour).padStart(2, '0')}:${String(time.minute).padStart(2, '0')}`
}

function sortTimes(times: TimePoint[]): TimePoint[] {
  return [...times]
    .map((t) => clampHourMinute(t.hour, t.minute))
    .sort((a, b) => toMinutes(a) - toMinutes(b))
}

/** 与历史 getSessionTimes 一致的默认映射，用于首次进入与次数增加时补缺 */
function getDefaultSessionTimes(session: SessionKey, runCount: number): TimePoint[] {
  const count = Math.min(4, Math.max(1, Number(runCount) || 2))

  if (session === 'morning') {
    switch (count) {
      case 1:
        return [{ hour: 10, minute: 30 }]
      case 2:
        return [{ hour: 10, minute: 0 }, { hour: 11, minute: 0 }]
      case 3:
        return [
          { hour: 9, minute: 30 },
          { hour: 10, minute: 15 },
          { hour: 11, minute: 0 },
        ]
      case 4:
        return [
          { hour: 9, minute: 30 },
          { hour: 10, minute: 0 },
          { hour: 10, minute: 30 },
          { hour: 11, minute: 0 },
        ]
      default:
        return [{ hour: 10, minute: 0 }, { hour: 11, minute: 0 }]
    }
  }

  switch (count) {
    case 1:
      return [{ hour: 14, minute: 0 }]
    case 2:
      return [{ hour: 13, minute: 30 }, { hour: 14, minute: 30 }]
    case 3:
      return [
        { hour: 13, minute: 0 },
        { hour: 13, minute: 45 },
        { hour: 14, minute: 30 },
      ]
    case 4:
      return [
        { hour: 13, minute: 0 },
        { hour: 13, minute: 30 },
        { hour: 14, minute: 0 },
        { hour: 14, minute: 30 },
      ]
    default:
      return [{ hour: 13, minute: 30 }, { hour: 14, minute: 30 }]
  }
}

function isTimeInSessionRange(session: SessionKey, time: TimePoint): boolean {
  const range = SESSION_RANGES[session]
  const minutes = toMinutes(clampHourMinute(time.hour, time.minute))
  return minutes >= range.start && minutes <= range.end
}

function parseTimeInput(value: string): TimePoint | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec((value || '').trim())
  if (!match) {
    return null
  }
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) {
    return null
  }
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    return null
  }
  return { hour, minute }
}

export function timePointToInputValue(time: TimePoint): string {
  const clamped = clampHourMinute(time.hour, time.minute)
  return formatTime(clamped)
}

export function useScheduleForm() {
  const scheduleSettings = reactive<ScheduleFormState>(defaultState())

  function syncFromSchedules(schedules: ScheduleLike[]) {
    Object.assign(scheduleSettings, defaultState())

    ;(Object.keys(FIXED_TASK_NAMES) as ScheduleKey[]).forEach((key) => {
      const matched = schedules.find((item) => item.name === FIXED_TASK_NAMES[key])
      if (!matched) {
        return
      }

      const { hour, minute } = parseCron(matched.cron_expression)
      scheduleSettings[key].enabled = matched.enabled
      scheduleSettings[key].hour = hour
      scheduleSettings[key].minute = minute
      scheduleSettings[key].prompt = matched.task_prompt || scheduleSettings[key].prompt
    })

    ;(Object.keys(SESSION_TASK_NAMES) as SessionKey[]).forEach((key) => {
      const matched = schedules
        .filter((item) => item.name.startsWith(SESSION_TASK_NAMES[key]))
        .slice()
        .sort((a, b) => {
          const ta = toMinutes(parseCron(a.cron_expression))
          const tb = toMinutes(parseCron(b.cron_expression))
          if (ta !== tb) {
            return ta - tb
          }
          return a.cron_expression.localeCompare(b.cron_expression)
        })

      if (matched.length === 0) {
        return
      }

      const times = matched
        .map((item) => parseCron(item.cron_expression))
        .slice(0, 4)

      // 至少保留 1 个时间点
      scheduleSettings[key].enabled = matched.some((item) => item.enabled)
      scheduleSettings[key].times = times.length > 0 ? times : getDefaultSessionTimes(key, 1)
      scheduleSettings[key].prompt = matched[0].task_prompt || scheduleSettings[key].prompt
    })
  }

  function setSessionRunCount(session: SessionKey, count: number) {
    const target = Math.min(4, Math.max(1, Math.trunc(Number(count) || 1)))
    const current = scheduleSettings[session].times
    if (target === current.length) {
      return
    }

    if (target < current.length) {
      scheduleSettings[session].times = current.slice(0, target)
      return
    }

    // 增加：用默认映射表补缺，避免与已有时间冲突时回退到默认槽位
    const defaults = getDefaultSessionTimes(session, target)
    const next = current.map((t) => clampHourMinute(t.hour, t.minute))
    const used = new Set(next.map(toMinutes))

    for (let i = next.length; i < target; i += 1) {
      const candidate = defaults[i] ?? defaults[defaults.length - 1]
      let point = clampHourMinute(candidate.hour, candidate.minute)
      // 若与已有重复，在时段内向后找最近空位
      if (used.has(toMinutes(point))) {
        const range = SESSION_RANGES[session]
        let found: TimePoint | null = null
        for (let m = range.start; m <= range.end; m += 1) {
          if (!used.has(m)) {
            found = { hour: Math.floor(m / 60), minute: m % 60 }
            break
          }
        }
        point = found ?? point
      }
      next.push(point)
      used.add(toMinutes(point))
    }

    scheduleSettings[session].times = next
  }

  function setSessionTime(session: SessionKey, index: number, value: string) {
    const parsed = parseTimeInput(value)
    if (!parsed) {
      return
    }
    const times = scheduleSettings[session].times
    if (index < 0 || index >= times.length) {
      return
    }
    times[index] = parsed
  }

  function setFixedTaskTime(section: ScheduleKey, value: string) {
    const parsed = parseTimeInput(value)
    if (!parsed) {
      return
    }
    scheduleSettings[section].hour = parsed.hour
    scheduleSettings[section].minute = parsed.minute
  }

  function validate(): string | null {
    for (const key of Object.keys(FIXED_TASK_NAMES) as ScheduleKey[]) {
      const current = scheduleSettings[key]
      if (!Number.isFinite(current.hour) || !Number.isFinite(current.minute)) {
        return `${FIXED_TASK_NAMES[key]} 的执行时间无效，请选择时:分。`
      }
      if (current.hour < 0 || current.hour > 23 || current.minute < 0 || current.minute > 59) {
        return `${FIXED_TASK_NAMES[key]} 的执行时间无效，请选择时:分。`
      }
    }

    for (const key of Object.keys(SESSION_TASK_NAMES) as SessionKey[]) {
      const current = scheduleSettings[key]
      const range = SESSION_RANGES[key]
      const label = SESSION_TASK_NAMES[key]
      const times = current.times

      if (!Array.isArray(times) || times.length < 1 || times.length > 4) {
        return `${label} 的运行次数须为 1–4 次。`
      }

      const seen = new Set<number>()
      for (let i = 0; i < times.length; i += 1) {
        const t = clampHourMinute(times[i].hour, times[i].minute)
        if (!isTimeInSessionRange(key, t)) {
          return `${label} 第 ${i + 1} 个时间 ${formatTime(t)} 不在交易时段 ${range.label} 内。`
        }
        const minutes = toMinutes(t)
        if (seen.has(minutes)) {
          return `${label} 存在重复时间 ${formatTime(t)}，请修改后再保存。`
        }
        seen.add(minutes)
      }
    }

    return null
  }

  function buildPayload(existingSchedules: ScheduleLike[]) {
    const validationError = validate()
    if (validationError) {
      throw new Error(validationError)
    }

    const fixedPayload = (Object.keys(FIXED_TASK_NAMES) as ScheduleKey[]).map((key) => {
      const existing = existingSchedules.find((item) => item.name === FIXED_TASK_NAMES[key])
      const current = scheduleSettings[key]
      const time = clampHourMinute(current.hour, current.minute)
      return {
        id: existing?.id,
        name: FIXED_TASK_NAMES[key],
        run_type: 'analysis' as const,
        cron_expression: buildCron(time.hour, time.minute),
        task_prompt: current.prompt,
        timeout_seconds: existing?.timeout_seconds ?? DEFAULT_TIMEOUT,
        enabled: current.enabled,
      }
    })

    const sessionPayload = (Object.keys(SESSION_TASK_NAMES) as SessionKey[]).flatMap((key) => {
      const current = scheduleSettings[key]
      const existing = existingSchedules
        .filter((item) => item.name.startsWith(SESSION_TASK_NAMES[key]))
        .slice()
        .sort((a, b) => {
          const ta = toMinutes(parseCron(a.cron_expression))
          const tb = toMinutes(parseCron(b.cron_expression))
          if (ta !== tb) {
            return ta - tb
          }
          return a.name.localeCompare(b.name)
        })

      // 保存前按时间升序编号，避免「2 号比 1 号更早」
      const sorted = sortTimes(current.times)

      return sorted.map((time, index) => ({
        id: existing[index]?.id,
        name: `${SESSION_TASK_NAMES[key]}${index + 1}号`,
        run_type: 'trade' as const,
        cron_expression: buildCron(time.hour, time.minute),
        task_prompt: current.prompt,
        timeout_seconds: existing[index]?.timeout_seconds ?? DEFAULT_TIMEOUT,
        enabled: current.enabled,
      }))
    })

    return [...fixedPayload, ...sessionPayload]
  }

  function autoResizeTextarea(event: Event) {
    const textarea = event.target as HTMLTextAreaElement
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
  }

  function getMorningRunTimes() {
    return sortTimes(scheduleSettings.morning.times).map(formatTime).join(', ')
  }

  function getAfternoonRunTimes() {
    return sortTimes(scheduleSettings.afternoon.times).map(formatTime).join(', ')
  }

  function getSessionRunCount(session: SessionKey) {
    return scheduleSettings[session].times.length
  }

  return {
    scheduleSettings,
    runCountOptions: RUN_COUNT_OPTIONS,
    sessionRanges: SESSION_RANGES,
    syncFromSchedules,
    buildPayload,
    validate,
    setFixedTaskTime,
    setSessionRunCount,
    setSessionTime,
    autoResizeTextarea,
    getMorningRunTimes,
    getAfternoonRunTimes,
    getSessionRunCount,
    timePointToInputValue,
    getDefaultSessionTimes,
  }
}
