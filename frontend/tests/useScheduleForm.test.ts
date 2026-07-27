import assert from 'node:assert/strict'
import test from 'node:test'

import { useScheduleForm } from '../src/composables/useScheduleForm.ts'

test('default morning/afternoon times match legacy 2-run mapping', () => {
  const { getMorningRunTimes, getAfternoonRunTimes, getSessionRunCount } = useScheduleForm()

  assert.equal(getSessionRunCount('morning'), 2)
  assert.equal(getSessionRunCount('afternoon'), 2)
  assert.equal(getMorningRunTimes(), '10:00, 11:00')
  assert.equal(getAfternoonRunTimes(), '13:30, 14:30')
})

test('setSessionRunCount expands with default mapping and shrinks from tail', () => {
  const { scheduleSettings, setSessionRunCount, getMorningRunTimes, getSessionRunCount } = useScheduleForm()

  setSessionRunCount('morning', 4)
  assert.equal(getSessionRunCount('morning'), 4)
  assert.equal(getMorningRunTimes(), '09:30, 10:00, 10:30, 11:00')

  // 用户自定义后再缩减：从尾部删除
  scheduleSettings.morning.times = [
    { hour: 9, minute: 35 },
    { hour: 10, minute: 20 },
    { hour: 11, minute: 10 },
    { hour: 11, minute: 25 },
  ]
  setSessionRunCount('morning', 2)
  assert.equal(getSessionRunCount('morning'), 2)
  assert.deepEqual(scheduleSettings.morning.times, [
    { hour: 9, minute: 35 },
    { hour: 10, minute: 20 },
  ])
})

test('buildPayload creates correct morning run count and analysis run_type', () => {
  const { setSessionRunCount, buildPayload } = useScheduleForm()

  setSessionRunCount('morning', 4)

  const payload = buildPayload([])
  const morningRuns = payload.filter((item) => item.name.startsWith('上午运行'))
  const fixedRuns = payload.filter((item) => ['盘前分析', '午间复盘', '收盘分析'].includes(item.name))

  assert.equal(morningRuns.length, 4)
  assert.equal(morningRuns.every((item) => item.run_type === 'trade'), true)
  assert.equal(fixedRuns.length, 3)
  assert.equal(fixedRuns.every((item) => item.run_type === 'analysis'), true)
})

test('buildPayload preserves disabled session schedules', () => {
  const { scheduleSettings, syncFromSchedules, buildPayload } = useScheduleForm()

  syncFromSchedules([
    {
      id: 11,
      name: '上午运行1号',
      run_type: 'trade',
      cron_expression: '0 10 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
    {
      id: 12,
      name: '上午运行2号',
      run_type: 'trade',
      cron_expression: '0 11 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
  ])

  assert.equal(scheduleSettings.morning.enabled, false)

  const payload = buildPayload([
    {
      id: 11,
      name: '上午运行1号',
      run_type: 'trade',
      cron_expression: '0 10 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
    {
      id: 12,
      name: '上午运行2号',
      run_type: 'trade',
      cron_expression: '0 11 * * 1-5',
      task_prompt: 'session',
      timeout_seconds: 1800,
      enabled: false,
    },
  ])

  const morningRuns = payload.filter((item) => item.name.startsWith('上午运行'))
  assert.equal(morningRuns.every((item) => item.enabled === false), true)
})

test('syncFromSchedules preserves custom analysis times without snapping to presets', () => {
  const { scheduleSettings, syncFromSchedules } = useScheduleForm()

  syncFromSchedules([
    {
      id: 1,
      name: '盘前分析',
      run_type: 'analysis',
      cron_expression: '45 7 * * 1-5',
      task_prompt: 'a',
      timeout_seconds: 1800,
      enabled: true,
    },
    {
      id: 2,
      name: '午间复盘',
      run_type: 'analysis',
      cron_expression: '45 11 * * 1-5',
      task_prompt: 'b',
      timeout_seconds: 1800,
      enabled: true,
    },
    {
      id: 3,
      name: '收盘分析',
      run_type: 'analysis',
      cron_expression: '15 16 * * 1-5',
      task_prompt: 'c',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.equal(scheduleSettings.preMarket.hour, 7)
  assert.equal(scheduleSettings.preMarket.minute, 45)
  assert.equal(scheduleSettings.midday.hour, 11)
  assert.equal(scheduleSettings.midday.minute, 45)
  assert.equal(scheduleSettings.postMarket.hour, 16)
  assert.equal(scheduleSettings.postMarket.minute, 15)
})

test('syncFromSchedules reads real trade cron times into times[]', () => {
  const { scheduleSettings, syncFromSchedules, getMorningRunTimes } = useScheduleForm()

  syncFromSchedules([
    {
      id: 21,
      name: '上午运行1号',
      run_type: 'trade',
      cron_expression: '35 9 * * 1-5',
      task_prompt: 'trade',
      timeout_seconds: 1800,
      enabled: true,
    },
    {
      id: 22,
      name: '上午运行2号',
      run_type: 'trade',
      cron_expression: '20 10 * * 1-5',
      task_prompt: 'trade',
      timeout_seconds: 1800,
      enabled: true,
    },
    {
      id: 23,
      name: '上午运行3号',
      run_type: 'trade',
      cron_expression: '10 11 * * 1-5',
      task_prompt: 'trade',
      timeout_seconds: 1800,
      enabled: true,
    },
  ])

  assert.deepEqual(scheduleSettings.morning.times, [
    { hour: 9, minute: 35 },
    { hour: 10, minute: 20 },
    { hour: 11, minute: 10 },
  ])
  assert.equal(getMorningRunTimes(), '09:35, 10:20, 11:10')
})

test('buildPayload writes custom analysis and trade times as cron', () => {
  const { scheduleSettings, buildPayload } = useScheduleForm()

  scheduleSettings.preMarket.enabled = true
  scheduleSettings.preMarket.hour = 7
  scheduleSettings.preMarket.minute = 45

  scheduleSettings.morning.times = [
    { hour: 9, minute: 35 },
    { hour: 10, minute: 20 },
    { hour: 11, minute: 10 },
  ]

  const payload = buildPayload([])
  const preMarket = payload.find((item) => item.name === '盘前分析')
  const morning = payload
    .filter((item) => item.name.startsWith('上午运行'))
    .map((item) => item.cron_expression)

  assert.equal(preMarket?.cron_expression, '45 7 * * 1-5')
  assert.deepEqual(morning, [
    '35 9 * * 1-5',
    '20 10 * * 1-5',
    '10 11 * * 1-5',
  ])
})

test('buildPayload sorts trade times before numbering', () => {
  const { scheduleSettings, buildPayload } = useScheduleForm()

  scheduleSettings.morning.times = [
    { hour: 11, minute: 0 },
    { hour: 9, minute: 45 },
  ]

  const payload = buildPayload([])
  const morning = payload.filter((item) => item.name.startsWith('上午运行'))

  assert.equal(morning[0].name, '上午运行1号')
  assert.equal(morning[0].cron_expression, '45 9 * * 1-5')
  assert.equal(morning[1].name, '上午运行2号')
  assert.equal(morning[1].cron_expression, '0 11 * * 1-5')
})

test('validate rejects trade times outside session range', () => {
  const { scheduleSettings, validate, buildPayload } = useScheduleForm()

  scheduleSettings.morning.times = [
    { hour: 9, minute: 35 },
    { hour: 11, minute: 45 },
  ]

  const error = validate()
  assert.ok(error)
  assert.match(error!, /上午运行/)
  assert.match(error!, /11:45/)

  assert.throws(() => buildPayload([]), /上午运行/)
})

test('validate rejects afternoon times outside session range', () => {
  const { scheduleSettings, validate } = useScheduleForm()

  scheduleSettings.afternoon.times = [
    { hour: 12, minute: 50 },
  ]

  const error = validate()
  assert.ok(error)
  assert.match(error!, /下午运行/)
})

test('validate rejects duplicate trade times', () => {
  const { scheduleSettings, validate } = useScheduleForm()

  scheduleSettings.morning.times = [
    { hour: 10, minute: 0 },
    { hour: 10, minute: 0 },
  ]

  const error = validate()
  assert.ok(error)
  assert.match(error!, /重复/)
})

test('boundary times 09:30 / 11:30 / 13:00 / 15:00 are accepted', () => {
  const { scheduleSettings, validate, buildPayload } = useScheduleForm()

  scheduleSettings.morning.times = [
    { hour: 9, minute: 30 },
    { hour: 11, minute: 30 },
  ]
  scheduleSettings.afternoon.times = [
    { hour: 13, minute: 0 },
    { hour: 15, minute: 0 },
  ]

  assert.equal(validate(), null)
  const payload = buildPayload([])
  const crons = payload
    .filter((item) => item.run_type === 'trade')
    .map((item) => item.cron_expression)
  assert.deepEqual(crons, [
    '30 9 * * 1-5',
    '30 11 * * 1-5',
    '0 13 * * 1-5',
    '0 15 * * 1-5',
  ])
})

test('pre-market default display time is 08:00', () => {
  const { scheduleSettings } = useScheduleForm()

  assert.equal(scheduleSettings.preMarket.hour, 8)
  assert.equal(scheduleSettings.preMarket.minute, 0)
})

test('setSessionTime and setFixedTaskTime parse HH:MM input', () => {
  const { scheduleSettings, setSessionTime, setFixedTaskTime } = useScheduleForm()

  setFixedTaskTime('preMarket', '07:45')
  assert.equal(scheduleSettings.preMarket.hour, 7)
  assert.equal(scheduleSettings.preMarket.minute, 45)

  setSessionTime('morning', 0, '09:40')
  assert.deepEqual(scheduleSettings.morning.times[0], { hour: 9, minute: 40 })
})
