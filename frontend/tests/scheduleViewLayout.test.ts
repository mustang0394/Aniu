import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('schedule overview panel is rendered before schedule settings panel', () => {
  const source = readFileSync(new URL('../src/views/ScheduleView.vue', import.meta.url), 'utf-8')

  const overviewIndex = source.indexOf('title="当前定时任务"')
  const settingsIndex = source.indexOf('title="定时任务设置"')

  assert.notEqual(overviewIndex, -1)
  assert.notEqual(settingsIndex, -1)
  assert.ok(overviewIndex < settingsIndex)
})

test('schedule view uses time inputs for analysis and trade slots', () => {
  const source = readFileSync(new URL('../src/views/ScheduleView.vue', import.meta.url), 'utf-8')

  assert.ok(source.includes('type="time"'))
  assert.ok(source.includes('step="60"'))
  assert.ok(source.includes('计划运行时间'))
  assert.ok(!source.includes('fixedTaskTimeOptions'))
})
