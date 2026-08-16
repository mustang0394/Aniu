<template>
  <article class="rounded-xl border border-separator bg-card-solid p-4 shadow-sm sm:p-5">
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <span class="truncate text-callout font-semibold text-label">
            {{ displayName }}
          </span>
          <UiBadge :tone="uziStatusTone(status)">{{ uziStatusLabel(status) }}</UiBadge>
        </div>
        <p class="mt-1 text-footnote text-label-tertiary break-all">
          {{ phaseLabel }}<template v-if="message"> · {{ message }}</template>
          <template v-if="errorCode"> · {{ errorCode }}</template>
        </p>
      </div>
      <UiButton
        variant="danger-soft"
        size="sm"
        class="shrink-0"
        :loading="cancelling"
        :disabled="isTerminal"
        @click="handleCancel"
      >
        取消任务
      </UiButton>
    </div>

    <!-- 进度条 -->
    <div class="mt-3.5">
      <div class="flex items-center justify-between gap-2 text-footnote">
        <span class="text-label-secondary">进度</span>
        <span class="tabular-nums font-semibold text-label">{{ progress }}%</span>
      </div>
      <div
        class="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-fill-secondary"
        role="progressbar"
        :aria-valuenow="progress"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <div
          class="h-full rounded-full transition-all duration-500"
          :class="progressBarClass"
          :style="{ width: `${progress}%` }"
        />
      </div>
      <div class="mt-2 flex flex-wrap items-center justify-between gap-2 text-footnote text-label-tertiary">
        <span class="tabular-nums">{{ elapsedText }}</span>
        <span class="flex items-center gap-1.5">
          <span
            v-if="!isTerminal"
            class="inline-block size-2 rounded-full animate-pulse-dot"
            :class="connected ? 'bg-accent' : 'bg-warning'"
          />
          <template v-if="!isTerminal">
            <span v-if="connected">实时更新中</span>
            <span v-else>轮询更新中</span>
          </template>
        </span>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch, onMounted } from 'vue'

import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import type { UziReportStatus, UziReportSummary } from '@/types'
import { useUziReportStream } from '@/composables/useUziReportStream'
import { formatReportDuration, uziStatusLabel, uziStatusTone } from '@/utils/uzi'

const props = defineProps<{
  report: UziReportSummary
  cancelling?: boolean
}>()

const emit = defineEmits<{
  /** 任务进入终态（完成/失败/取消）后通知上层刷新列表。 */
  terminal: [status: UziReportStatus]
  cancel: [reportId: number]
}>()

const stream = useUziReportStream()

const status = computed<UziReportStatus | null>(() => stream.status.value ?? props.report.status)
const progress = computed(() => stream.progress.value || props.report.progress || 0)
const message = computed(() => stream.message.value)
const phase = computed(() => stream.phase.value)
const connected = computed(() => stream.connected.value)
const errorCode = computed(() => stream.errorCode.value)
const isTerminal = computed(
  () =>
    status.value === 'completed' || status.value === 'failed' || status.value === 'cancelled',
)

const displayName = computed(
  () => props.report.company_name || props.report.ticker_normalized || props.report.ticker_input,
)

const phaseLabel = computed(() => {
  if (phase.value) return phase.value
  return uziStatusLabel(status.value)
})

const progressBarClass = computed(() => {
  switch (status.value) {
    case 'completed':
      return 'bg-success'
    case 'failed':
      return 'bg-danger'
    case 'cancelled':
      return 'bg-separator-strong'
    default:
      return 'bg-accent'
  }
})

const elapsedText = computed(() => {
  void nowTick.value
  const finished = stream.job.value?.finished_at ?? props.report.finished_at
  return formatReportDuration(props.report.created_at, finished)
})

let elapsedTimer: number | null = null
const nowTick = ref(Date.now())

function startElapsedTimer(): void {
  stopElapsedTimer()
  elapsedTimer = window.setInterval(() => {
    nowTick.value = Date.now()
  }, 1000)
}

function stopElapsedTimer(): void {
  if (elapsedTimer !== null) {
    window.clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

function handleCancel(): void {
  if (isTerminal.value) return
  emit('cancel', props.report.id)
}

const stopWatch = watch(
  () => props.report.id,
  (reportId) => {
    if (reportId != null) {
      void stream.start(reportId)
    }
  },
  { immediate: true },
)

watch(isTerminal, (terminal) => {
  if (terminal) {
    stopElapsedTimer()
    emit('terminal', status.value ?? props.report.status)
  } else {
    startElapsedTimer()
  }
})

onMounted(() => {
  if (!isTerminal.value) startElapsedTimer()
})

onBeforeUnmount(() => {
  stopElapsedTimer()
  stopWatch()
  stream.stop()
})
</script>