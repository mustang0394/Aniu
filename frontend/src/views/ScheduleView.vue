<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader
      title="定时设置"
      kicker="Schedules"
      description="配置分析与交易时段的自动任务"
    />

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
        v-if="errorMessage"
        class="mb-4 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
        role="alert"
      >
        {{ errorMessage }}
      </div>

      <!-- Analysis tasks -->
      <section class="mb-8">
        <header class="mb-4">
          <h3 class="m-0 text-title-3 font-semibold text-label">分析任务</h3>
          <p class="m-0 mt-1 text-footnote text-label-secondary">配置自动执行的 AI 分析任务</p>
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
              <p class="mb-2 text-caption font-semibold uppercase tracking-wide text-label-tertiary">执行时间</p>
              <div class="mb-3 flex flex-wrap gap-2">
                <button
                  v-for="option in task.timeOptions"
                  :key="`${task.key}-${option.label}`"
                  type="button"
                  class="h-9 rounded-pill px-3.5 text-footnote font-semibold transition-colors"
                  :class="task.isTimeActive(option)
                    ? 'bg-accent text-on-accent shadow-sm'
                    : 'bg-card-solid text-label-secondary ring-1 ring-separator-strong hover:bg-hover'"
                  :disabled="!task.enabled"
                  @click="task.setTime(option)"
                >
                  {{ option.label }}
                </button>
              </div>

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
          <p class="m-0 mt-1 text-footnote text-label-secondary">配置交易时段内的定时任务频率</p>
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
                <p class="m-0 mt-1 text-footnote text-label-secondary">{{ session.timeRange }}</p>
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
              <div class="flex flex-wrap gap-1.5">
                <span
                  v-for="(time, index) in session.plannedTimes"
                  :key="`${session.key}-t-${index}`"
                  class="inline-flex rounded-pill bg-card-solid px-2.5 py-1 text-caption font-medium tabular-nums text-label-secondary ring-1 ring-separator"
                >
                  {{ time }}
                </span>
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
import { computed, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'

import { useAppStore } from '@/stores/legacy'
import { useScheduleForm } from '@/composables/useScheduleForm'
import { formatWeekdayMinuteTime } from '@/utils/formatters'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiPanel from '@/components/ui/UiPanel.vue'
import UiToggle from '@/components/ui/UiToggle.vue'

const store = useAppStore()
const { busy, schedules, errorMessage, activeScheduleCards, nextScheduledTask } = storeToRefs(store)
const {
  scheduleSettings,
  fixedTaskTimeOptions,
  runCountOptions,
  syncFromSchedules,
  buildPayload,
  setFixedTaskTime,
  autoResizeTextarea,
  getMorningRunTimes,
  getAfternoonRunTimes,
} = useScheduleForm()

const analysisTasks = computed(() => [
  {
    key: 'preMarket' as const,
    name: '盘前分析',
    desc: '开盘前的市场预测与策略建议',
    enabled: scheduleSettings.preMarket.enabled,
    prompt: scheduleSettings.preMarket.prompt,
    promptLength: scheduleSettings.preMarket.prompt.length,
    timeOptions: fixedTaskTimeOptions.preMarket.options,
    setEnabled: (v: boolean) => { scheduleSettings.preMarket.enabled = v },
    isTimeActive: (option: { hour: number; minute: number }) =>
      scheduleSettings.preMarket.hour === option.hour && scheduleSettings.preMarket.minute === option.minute,
    setTime: (option: { hour: number; minute: number; label: string }) => setFixedTaskTime('preMarket', option),
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
    timeOptions: fixedTaskTimeOptions.midday.options,
    setEnabled: (v: boolean) => { scheduleSettings.midday.enabled = v },
    isTimeActive: (option: { hour: number; minute: number }) =>
      scheduleSettings.midday.hour === option.hour && scheduleSettings.midday.minute === option.minute,
    setTime: (option: { hour: number; minute: number; label: string }) => setFixedTaskTime('midday', option),
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
    timeOptions: fixedTaskTimeOptions.postMarket.options,
    setEnabled: (v: boolean) => { scheduleSettings.postMarket.enabled = v },
    isTimeActive: (option: { hour: number; minute: number }) =>
      scheduleSettings.postMarket.hour === option.hour && scheduleSettings.postMarket.minute === option.minute,
    setTime: (option: { hour: number; minute: number; label: string }) => setFixedTaskTime('postMarket', option),
    onPromptInput: (e: Event) => {
      scheduleSettings.postMarket.prompt = (e.target as HTMLTextAreaElement).value
      autoResizeTextarea(e)
    },
  },
])

const tradeSessions = computed(() => [
  {
    key: 'morning',
    name: '上午运行',
    timeRange: '09:30 - 11:30',
    runCount: scheduleSettings.morning.runCount,
    prompt: scheduleSettings.morning.prompt,
    promptLength: scheduleSettings.morning.prompt.length,
    plannedTimes: getMorningRunTimes().split(', ').filter(Boolean),
    setRunCount: (count: number) => { scheduleSettings.morning.runCount = count },
    onPromptInput: (e: Event) => {
      scheduleSettings.morning.prompt = (e.target as HTMLTextAreaElement).value
      autoResizeTextarea(e)
    },
  },
  {
    key: 'afternoon',
    name: '下午运行',
    timeRange: '13:00 - 15:00',
    runCount: scheduleSettings.afternoon.runCount,
    prompt: scheduleSettings.afternoon.prompt,
    promptLength: scheduleSettings.afternoon.prompt.length,
    plannedTimes: getAfternoonRunTimes().split(', ').filter(Boolean),
    setRunCount: (count: number) => { scheduleSettings.afternoon.runCount = count },
    onPromptInput: (e: Event) => {
      scheduleSettings.afternoon.prompt = (e.target as HTMLTextAreaElement).value
      autoResizeTextarea(e)
    },
  },
])

async function saveScheduleSettings() {
  await store.saveSchedule(buildPayload(schedules.value))
}

watch(
  schedules,
  (value) => {
    syncFromSchedules(value)
  },
  { immediate: true },
)

onMounted(async () => {
  if (schedules.value.length === 0) {
    await store.loadSchedule()
  }
})
</script>
