<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader
      title="定时设置"
      kicker="Schedules"
      description="配置分析与交易时段的自动任务"
    >
      <template v-if="store.hasMultipleAccounts">
        <select
          :value="store.selectedAccountId ?? ''"
          class="input h-9 w-auto py-1"
          @change="handleAccountSwitch"
        >
          <option v-for="acc in store.activeAccounts" :key="acc.id" :value="acc.id">
            {{ acc.name }}（{{ acc.slug }}）
          </option>
        </select>
      </template>
    </UiPageHeader>

    <!-- Active schedules overview -->
    <UiPanel title="当前定时任务" kicker="Live Schedules">
      <div v-if="activeScheduleCards.length" class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        <article
          v-for="task in activeScheduleCards"
          :key="task.id"
          class="rounded-[16px] border border-separator bg-fill/40 p-4 transition-transform hover:-translate-y-px"
        >
          <UiBadge :tone="task.category === '交易任务' ? 'trade' : 'analysis'">
            {{ task.category }}
          </UiBadge>
          <strong class="mt-2.5 block text-callout font-semibold text-label">{{ task.name }}</strong>
          <p class="m-0 mt-1 text-footnote text-label-secondary">交易日 {{ task.displayTime }}</p>
        </article>
      </div>
      <UiEmpty v-else title="没有已启用的定时任务" description="在下方开启分析或交易任务后会显示在这里。" />

      <div
        v-if="nextScheduledTask"
        class="mt-4 flex flex-wrap items-center gap-2 rounded-[14px] border border-accent/15 bg-accent-soft/60 px-4 py-3 text-body"
      >
        <span class="text-label-secondary">下次运行：</span>
        <UiBadge :tone="nextScheduledTask.category === '交易任务' ? 'trade' : 'analysis'">
          {{ nextScheduledTask.category }}
        </UiBadge>
        <strong class="text-label">{{ nextScheduledTask.name }}</strong>
        <span class="tabular-nums text-label-secondary">{{ formatWeekdayMinuteTime(nextScheduledTask.nextRunAt) }}</span>
      </div>
    </UiPanel>

    <!-- Task settings -->
    <UiPanel title="定时任务设置" kicker="Configuration">
      <div
        v-if="displayError"
        class="mb-4 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
        role="alert"
      >
        {{ displayError }}
      </div>

      <!-- Analysis tasks -->
      <section class="mb-8">
        <header class="mb-4">
          <h3 class="m-0 text-title-3 font-semibold text-label">分析任务</h3>
          <p class="m-0 mt-1 text-footnote text-label-secondary">配置自动执行的 AI 分析任务，可自定义执行时间</p>
        </header>

        <div class="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <article
            v-for="task in analysisTasks"
            :key="task.key"
            class="rounded-[16px] border p-4 transition-colors"
            :class="task.enabled
              ? 'border-accent/25 bg-accent-soft/30 shadow-sm'
              : 'border-separator bg-fill/30'"
          >
            <div class="mb-3 flex items-start justify-between gap-3">
              <div class="min-w-0">
                <h4 class="m-0 text-callout font-semibold text-label">{{ task.name }}</h4>
                <p class="m-0 mt-1 text-footnote text-label-secondary">{{ task.desc }}</p>
              </div>
              <UiToggle
                :model-value="task.enabled"
                @update:model-value="task.setEnabled"
              />
            </div>

            <div :class="task.enabled ? '' : 'pointer-events-none opacity-45'">
              <label class="mb-3 flex flex-col gap-1.5">
                <span class="text-caption font-semibold uppercase tracking-wide text-label-tertiary">执行时间</span>
                <input
                  type="time"
                  step="60"
                  :value="task.timeValue"
                  class="h-10 w-full max-w-[10rem] rounded-[12px] border border-separator-strong bg-card-solid px-3 text-body tabular-nums text-label outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-ring disabled:opacity-50"
                  :disabled="!task.enabled"
                  @input="task.onTimeInput"
                />
              </label>

              <label class="flex flex-col gap-1.5">
                <span class="text-caption font-semibold text-label-secondary">
                  提示词 <small class="font-normal text-label-tertiary">{{ task.promptLength }}字</small>
                </span>
                <textarea
                  :value="task.prompt"
                  rows="3"
                  class="w-full rounded-[12px] border border-separator-strong bg-card-solid px-3 py-2.5 text-body text-label outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-ring disabled:opacity-50"
                  :disabled="!task.enabled"
                  @input="task.onPromptInput"
                />
              </label>
            </div>
          </article>
        </div>
      </section>

      <!-- Trade tasks -->
      <section>
        <header class="mb-4">
          <h3 class="m-0 text-title-3 font-semibold text-label">交易任务</h3>
          <p class="m-0 mt-1 text-footnote text-label-secondary">配置交易时段内的运行次数与具体时间（须在对应交易时段内）</p>
        </header>

        <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <article
            v-for="session in tradeSessions"
            :key="session.key"
            class="rounded-[16px] border border-separator bg-fill/30 p-4"
          >
            <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h4 class="m-0 text-callout font-semibold text-label">{{ session.name }}</h4>
                <p class="m-0 mt-1 text-footnote text-label-secondary">允许时段 {{ session.timeRange }}</p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button
                  v-for="count in runCountOptions"
                  :key="`${session.key}-${count}`"
                  type="button"
                  class="h-9 min-w-12 rounded-pill px-3 text-footnote font-semibold transition-colors"
                  :class="session.runCount === count
                    ? 'bg-accent text-on-accent shadow-sm'
                    : 'bg-card-solid text-label-secondary ring-1 ring-separator-strong hover:bg-hover'"
                  @click="session.setRunCount(count)"
                >
                  {{ count }}次
                </button>
              </div>
            </div>

            <div class="mb-3">
              <p class="mb-2 text-caption font-semibold uppercase tracking-wide text-label-tertiary">计划运行时间</p>
              <div class="flex flex-col gap-2">
                <label
                  v-for="(slot, index) in session.timeSlots"
                  :key="`${session.key}-slot-${index}`"
                  class="flex flex-wrap items-center gap-2"
                >
                  <span class="min-w-10 text-caption font-medium text-label-secondary">{{ index + 1 }}号</span>
                  <input
                    type="time"
                    step="60"
                    :value="slot.value"
                    class="h-10 w-full max-w-[10rem] rounded-[12px] border bg-card-solid px-3 text-body tabular-nums text-label outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-ring"
                    :class="slot.invalid
                      ? 'border-danger/50 focus:border-danger focus:ring-danger/30'
                      : 'border-separator-strong'"
                    @input="session.onTimeInput(index, $event)"
                  />
                  <span v-if="slot.invalid" class="text-caption text-danger-text">{{ slot.hint }}</span>
                </label>
              </div>
            </div>

            <label class="flex flex-col gap-1.5">
              <span class="text-caption font-semibold text-label-secondary">
                提示词 <small class="font-normal text-label-tertiary">{{ session.promptLength }}字</small>
              </span>
              <textarea
                :value="session.prompt"
                rows="2"
                class="w-full rounded-[12px] border border-separator-strong bg-card-solid px-3 py-2.5 text-body text-label outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-ring"
                @input="session.onPromptInput"
              />
            </label>
          </article>
        </div>
      </section>

      <div class="mt-6 flex justify-end border-t border-separator pt-5">
        <UiButton
          variant="primary"
          :loading="busy"
          :disabled="busy"
          @click="saveScheduleSettings"
        >
          保存设置
        </UiButton>
      </div>
    </UiPanel>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { useTradingAccountsStore } from '@/stores/tradingAccounts'
import { useScheduleForm, timePointToInputValue } from '@/composables/useScheduleForm'
import { formatWeekdayMinuteTime } from '@/utils/formatters'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiPanel from '@/components/ui/UiPanel.vue'
import UiToggle from '@/components/ui/UiToggle.vue'

const store = useTradingAccountsStore()
const busy = ref(false)
const schedules = ref<import('@/types').ScheduleConfig[]>([])
const errorMessage = ref('')

interface ScheduleCard {
  id: number
  name: string
  category: string
  cronExpression: string
  displayTime: string
  nextRunAt: string | null
  lastRunAt: string | null
}

const activeScheduleCards = computed<ScheduleCard[]>(() => {
  const items = schedules.value
    .filter((item) => item.enabled)
    .slice()
    .map((item) => {
      const parts = (item.cron_expression || '').trim().split(/\s+/)
      const minute = Number(parts[0]) || 0
      const hour = Number(parts[1]) || 0
      const displayTime = `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
      const displayName = item.name.replace(/#(\d+)$/, '$1号')
      const category = item.run_type === 'trade' ? '交易任务' : '分析任务'
      return {
        id: item.id,
        name: displayName,
        category,
        cronExpression: item.cron_expression,
        displayTime,
        nextRunAt: item.next_run_at,
        lastRunAt: item.last_run_at,
      }
    })
  items.sort((a, b) => a.displayTime.localeCompare(b.displayTime))
  return items
})

const nextScheduledTask = computed<ScheduleCard | null>(() => {
  const cards = activeScheduleCards.value.filter((card) => !!card.nextRunAt)
  if (cards.length === 0) return null
  const sorted = [...cards].sort((a, b) => (a.nextRunAt ?? '').localeCompare(b.nextRunAt ?? ''))
  return sorted[0]
})
const {
  scheduleSettings,
  runCountOptions,
  sessionRanges,
  syncFromSchedules,
  buildPayload,
  validate,
  setFixedTaskTime,
  setSessionRunCount,
  setSessionTime,
  autoResizeTextarea,
  getSessionRunCount,
} = useScheduleForm()

const formError = ref('')
const displayError = computed(() => formError.value || errorMessage.value)

function minutesOf(hour: number, minute: number) {
  return hour * 60 + minute
}

function isSessionTimeInvalid(sessionKey: 'morning' | 'afternoon', hour: number, minute: number, index: number) {
  const range = sessionRanges[sessionKey]
  const m = minutesOf(hour, minute)
  if (m < range.start || m > range.end) {
    return { invalid: true, hint: `须在 ${range.label}` }
  }
  const times = scheduleSettings[sessionKey].times
  const dup = times.some((t, i) => i !== index && minutesOf(t.hour, t.minute) === m)
  if (dup) {
    return { invalid: true, hint: '时间重复' }
  }
  return { invalid: false, hint: '' }
}

const analysisTasks = computed(() => [
  {
    key: 'preMarket' as const,
    name: '盘前分析',
    desc: '开盘前的市场预测与策略建议',
    enabled: scheduleSettings.preMarket.enabled,
    prompt: scheduleSettings.preMarket.prompt,
    promptLength: scheduleSettings.preMarket.prompt.length,
    timeValue: timePointToInputValue(scheduleSettings.preMarket),
    setEnabled: (v: boolean) => { scheduleSettings.preMarket.enabled = v },
    onTimeInput: (e: Event) => {
      setFixedTaskTime('preMarket', (e.target as HTMLInputElement).value)
      formError.value = ''
    },
    onPromptInput: (e: Event) => {
      scheduleSettings.preMarket.prompt = (e.target as HTMLTextAreaElement).value
      autoResizeTextarea(e)
    },
  },
  {
    key: 'midday' as const,
    name: '午间复盘',
    desc: '中午时段的市场动态追踪',
    enabled: scheduleSettings.midday.enabled,
    prompt: scheduleSettings.midday.prompt,
    promptLength: scheduleSettings.midday.prompt.length,
    timeValue: timePointToInputValue(scheduleSettings.midday),
    setEnabled: (v: boolean) => { scheduleSettings.midday.enabled = v },
    onTimeInput: (e: Event) => {
      setFixedTaskTime('midday', (e.target as HTMLInputElement).value)
      formError.value = ''
    },
    onPromptInput: (e: Event) => {
      scheduleSettings.midday.prompt = (e.target as HTMLTextAreaElement).value
      autoResizeTextarea(e)
    },
  },
  {
    key: 'postMarket' as const,
    name: '收盘分析',
    desc: '收盘后的全面总结与回顾',
    enabled: scheduleSettings.postMarket.enabled,
    prompt: scheduleSettings.postMarket.prompt,
    promptLength: scheduleSettings.postMarket.prompt.length,
    timeValue: timePointToInputValue(scheduleSettings.postMarket),
    setEnabled: (v: boolean) => { scheduleSettings.postMarket.enabled = v },
    onTimeInput: (e: Event) => {
      setFixedTaskTime('postMarket', (e.target as HTMLInputElement).value)
      formError.value = ''
    },
    onPromptInput: (e: Event) => {
      scheduleSettings.postMarket.prompt = (e.target as HTMLTextAreaElement).value
      autoResizeTextarea(e)
    },
  },
])

const tradeSessions = computed(() => {
  const build = (key: 'morning' | 'afternoon', name: string) => {
    const times = scheduleSettings[key].times
    return {
      key,
      name,
      timeRange: sessionRanges[key].label,
      runCount: getSessionRunCount(key),
      prompt: scheduleSettings[key].prompt,
      promptLength: scheduleSettings[key].prompt.length,
      timeSlots: times.map((t, index) => {
        const status = isSessionTimeInvalid(key, t.hour, t.minute, index)
        return {
          value: timePointToInputValue(t),
          invalid: status.invalid,
          hint: status.hint,
        }
      }),
      setRunCount: (count: number) => {
        setSessionRunCount(key, count)
        formError.value = ''
      },
      onTimeInput: (index: number, e: Event) => {
        setSessionTime(key, index, (e.target as HTMLInputElement).value)
        formError.value = ''
      },
      onPromptInput: (e: Event) => {
        scheduleSettings[key].prompt = (e.target as HTMLTextAreaElement).value
        autoResizeTextarea(e)
      },
    }
  }

  return [
    build('morning', '上午运行'),
    build('afternoon', '下午运行'),
  ]
})

async function saveScheduleSettings() {
  formError.value = ''
  const validationError = validate()
  if (validationError) {
    formError.value = validationError
    return
  }

  try {
    const payload = buildPayload(schedules.value)
    if (store.selectedAccountId === null) {
      throw new Error('请先选择交易账户')
    }
    const saved = await store.saveSchedules(store.selectedAccountId, payload)
    schedules.value = saved
    syncFromSchedules(saved)
  } catch (error) {
    formError.value = (error as Error).message || '保存失败'
  }
}

async function loadSchedulesForAccount() {
  if (store.selectedAccountId === null) {
    schedules.value = []
    syncFromSchedules([])
    return
  }
  try {
    const payload = await store.loadSchedules(store.selectedAccountId)
    schedules.value = payload
    syncFromSchedules(payload)
  } catch (error) {
    errorMessage.value = (error as Error).message || '加载失败'
  }
  formError.value = ''
}

watch(
  () => store.selectedAccountId,
  (newId, oldId) => {
    if (newId !== null && newId !== oldId) {
      void loadSchedulesForAccount()
    }
  },
)

watch(
  schedules,
  (value) => {
    syncFromSchedules(value)
    formError.value = ''
  },
  { immediate: false, deep: true },
)

function handleAccountSwitch(event: Event) {
  const target = event.target as HTMLSelectElement
  const accountIdValue = Number(target.value)
  if (Number.isFinite(accountIdValue)) {
    store.selectAccount(accountIdValue)
  }
}

onMounted(async () => {
  try {
    await store.loadAccounts()
  } catch (error) {
    errorMessage.value = (error as Error).message
  }
  if (store.selectedAccountId !== null) {
    await loadSchedulesForAccount()
  }
})
</script>
