<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader title="AI 分析" kicker="Analysis" description="手动执行分析/交易，并查看历史运行详情">
      <UiButton
        variant="tinted"
        size="sm"
        :loading="manualRunning && activeManualAction === 'analysis'"
        :disabled="manualRunning"
        :title="manualRunButtonTitle"
        @click="handleManualRun"
      >
        {{ manualRunning && activeManualAction === 'analysis' ? '执行中…' : '执行分析' }}
      </UiButton>
      <UiButton
        variant="primary"
        size="sm"
        :loading="manualRunning && activeManualAction === 'trade'"
        :disabled="manualRunning"
        :title="manualTradeButtonTitle"
        @click="handleManualTrade"
      >
        {{ manualRunning && activeManualAction === 'trade' ? '执行中…' : '执行交易' }}
      </UiButton>
    </UiPageHeader>

    <UiPanel title="运行记录" kicker="Run History">
      <div
        v-if="analysisError"
        class="mb-4 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
        role="alert"
      >
        {{ analysisError }}
      </div>

      <div class="space-y-5">
        <section>
          <div class="mb-2.5 flex items-center justify-between gap-2">
            <span class="text-caption font-semibold uppercase tracking-[0.08em] text-label-tertiary">今日</span>
          </div>
          <div v-if="todayRuns.length || livePlaceholderVisible" class="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8">
            <button
              v-if="livePlaceholderVisible"
              type="button"
              class="run-card live"
              :class="{ active: liveFocused }"
              @click="focusLiveCard"
            >
              <span class="status-dot animate-pulse-dot bg-accent" />
              <span class="run-type">{{ liveRunTypeLabel }}</span>
              <span class="run-time">{{ formatShortTime(liveStartedAtIso) }}</span>
              <span class="run-duration">{{ liveElapsed }}</span>
            </button>
            <button
              v-for="run in todayRuns"
              :key="run.id"
              type="button"
              class="run-card"
              :class="{ active: isTodayRunActive(run.id), live: isTodayRunLive(run.id) }"
              @click="handleTodayRunSelect(run.id)"
            >
              <span
                class="status-dot"
                :class="isTodayRunLive(run.id) ? 'animate-pulse-dot bg-accent' : statusDotClass(run.status)"
              />
              <span class="run-type">{{ run.analysisType }}</span>
              <span class="run-time">{{ formatShortTime(run.startTime) }}</span>
              <span class="run-duration">{{ run.duration }}</span>
            </button>
          </div>
          <p v-else class="m-0 rounded-[12px] bg-fill/50 px-4 py-6 text-center text-footnote text-label-tertiary">
            今日暂无运行记录。
          </p>
        </section>

        <section>
          <div class="mb-2.5 flex flex-wrap items-center justify-between gap-2">
            <span class="text-caption font-semibold uppercase tracking-[0.08em] text-label-tertiary">历史</span>
            <div class="relative">
              <button
                type="button"
                class="inline-flex h-9 items-center rounded-pill border border-separator-strong bg-card-solid px-3.5 text-footnote font-semibold text-label-secondary hover:bg-hover"
                @click="openHistoryDatePicker"
              >
                {{ historyDateDisplay }}
              </button>
              <input
                ref="historyDateInput"
                type="date"
                v-model="selectedDate"
                class="pointer-events-none absolute inset-0 opacity-0"
                tabindex="-1"
                aria-hidden="true"
                @change="loadHistoryRuns"
              />
            </div>
          </div>
          <div v-if="historyRuns.length" class="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8">
            <button
              v-for="run in historyRuns"
              :key="run.id"
              type="button"
              class="run-card"
              :class="{ active: !liveFocused && selectedRun?.id === run.id }"
              @click="handleSelectRun(run.id, historyRuns)"
            >
              <span class="status-dot" :class="statusDotClass(run.status)" />
              <span class="run-type">{{ run.analysisType }}</span>
              <span class="run-time">{{ formatShortTime(run.startTime) }}</span>
              <span class="run-duration">{{ run.duration }}</span>
            </button>
          </div>
          <p v-else-if="selectedDate" class="m-0 rounded-[12px] bg-fill/50 px-4 py-6 text-center text-footnote text-label-tertiary">
            该日期没有找到运行记录，请切换日期后重试。
          </p>
        </section>
      </div>
    </UiPanel>

    <UiPanel title="分析详情" kicker="Analysis Detail">
      <template #actions>
        <UiButton
          v-if="selectedRun && !liveVisible"
          variant="danger-soft"
          size="sm"
          title="删除当前任务"
          @click="handleDeleteRun(selectedRun.id)"
        >
          删除任务
        </UiButton>
      </template>

      <div v-if="detailGridVisible" class="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <!-- Status -->
        <div class="rounded-[16px] border border-separator bg-fill/40 p-4">
          <h4 class="m-0 mb-3 text-footnote font-semibold uppercase tracking-wide text-label-tertiary">运行状态</h4>
          <div class="flex flex-wrap items-center gap-3">
            <span class="text-callout font-semibold tabular-nums text-label">{{ formatTime(displayStatusStartTime) }}</span>
            <span class="text-body text-label-secondary">{{ displayStatusDuration }}</span>
            <span class="status-dot" :class="displayStatusDotTailwind" />
          </div>
          <div v-if="displayTokenBarVisible" class="mt-3 flex flex-wrap gap-2">
            <span class="token-chip"><i>输入</i><b>{{ displayInputTokens }}</b></span>
            <span class="token-chip"><i>输出</i><b>{{ displayOutputTokens }}</b></span>
            <span class="token-chip emphasis"><i>总量</i><b>{{ displayTotalTokens }}</b></span>
          </div>
        </div>

        <!-- API calls -->
        <div class="flex min-h-[200px] flex-col rounded-[16px] border border-separator bg-fill/40 p-4">
          <h4 class="m-0 mb-3 text-footnote font-semibold uppercase tracking-wide text-label-tertiary">
            接口调用 ({{ displayApiDetails.length }})
          </h4>
          <div
            v-if="displayApiDetails.length"
            ref="apiListRef"
            class="max-h-56 flex-1 space-y-1 overflow-y-auto pr-1"
            @scroll="handleApiListScroll"
          >
            <TransitionGroup name="stream-reveal" tag="div" class="space-y-1">
              <button
                v-for="(api, idx) in displayApiDetails"
                :key="getApiItemKey(api, idx)"
                type="button"
                class="detail-item"
                :class="{ active: !liveVisible && activePreviewIndex === api.preview_index && api.preview_index !== null }"
                @click="focusPreview(api.preview_index)"
              >
                <div class="min-w-0 flex-1">
                  <span class="block truncate text-footnote font-semibold text-label" :title="api.name">{{ api.name }}</span>
                  <span class="block truncate text-caption text-label-tertiary" :title="api.summary">{{ api.summary }}</span>
                </div>
                <span class="status-dot shrink-0" :class="apiStatusDot(api)" aria-hidden="true" />
              </button>
            </TransitionGroup>
          </div>
          <p v-else-if="liveVisible || selectedRun?.detailLoaded" class="m-0 text-footnote text-label-tertiary">
            {{ displayApiEmptyText }}
          </p>
        </div>

        <!-- Trades -->
        <div class="flex min-h-[200px] flex-col rounded-[16px] border border-separator bg-fill/40 p-4">
          <h4 class="m-0 mb-3 text-footnote font-semibold uppercase tracking-wide text-label-tertiary">
            交易执行 ({{ displayTradeDetails.length }})
          </h4>
          <div
            v-if="displayTradeDetails.length"
            ref="tradeListRef"
            class="max-h-56 flex-1 space-y-1 overflow-y-auto pr-1"
            @scroll="handleTradeListScroll"
          >
            <TransitionGroup name="stream-reveal" tag="div" class="space-y-1">
              <button
                v-for="(trade, idx) in displayTradeDetails"
                :key="getTradeItemKey(trade, idx)"
                type="button"
                class="detail-item"
                :class="{ active: !liveVisible && activePreviewIndex === trade.preview_index && trade.preview_index !== null }"
                @click="focusPreview(trade.preview_index)"
              >
                <div class="min-w-0 flex-1">
                  <span
                    class="text-footnote font-semibold"
                    :class="trade.action === 'buy' ? 'text-profit-up-text' : trade.action === 'sell' ? 'text-profit-down-text' : 'text-label'"
                  >{{ trade.action_text }}</span>
                  <span class="mt-0.5 block truncate text-caption text-label-tertiary" :title="trade.summary">{{ trade.summary }}</span>
                </div>
                <span class="status-dot shrink-0" :class="tradeStatusDot(trade)" aria-hidden="true" />
              </button>
            </TransitionGroup>
          </div>
          <p v-else-if="liveVisible || selectedRun?.detailLoaded" class="m-0 text-footnote text-label-tertiary">
            {{ displayTradeEmptyText }}
          </p>
        </div>
      </div>

      <p v-if="selectedRunLoading && !liveVisible" class="mt-4 text-footnote text-label-tertiary">
        正在加载本次运行详情...
      </p>

      <div v-if="liveVisible || selectedRun?.output || activePreview" class="mt-4">
        <div
          class="output-surface min-h-[240px] max-h-[min(70vh,720px)] overflow-y-auto rounded-[16px] border border-separator bg-card-solid p-4 shadow-sm"
          @click="handleOutputSurfaceClick"
        >
          <div
            v-if="liveVisible && liveOutputIsPlaceholder"
            ref="liveOutputRef"
            class="whitespace-pre-wrap text-body text-label-tertiary italic"
            @scroll="handleLiveOutputScroll"
          >
            {{ liveOutputText }}
          </div>
          <div
            v-else-if="liveVisible"
            ref="liveOutputRef"
            class="markdown-body"
            @scroll="handleLiveOutputScroll"
            v-html="liveOutputHtml"
          />
          <div
            v-else-if="activePreview"
            class="whitespace-pre-wrap break-words font-mono text-footnote leading-relaxed text-label"
            :class="{ 'opacity-60': activePreviewLoading }"
          >
            {{ activePreviewText }}
          </div>
          <p v-else-if="renderedOutputLoading" class="m-0 text-footnote text-label-tertiary">
            正在渲染分析输出...
          </p>
          <div v-else class="markdown-body" v-html="renderedOutputHtml" />
        </div>
      </div>

      <div
        v-if="runErrorVisible"
        class="mt-4 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
      >
        {{ runErrorMessage }}
      </div>

      <UiEmpty
        v-if="!detailGridVisible && !selectedRunLoading"
        class="mt-4"
        title="暂无运行详情"
        description="当前没有可展示的运行详情。完成一次任务执行后，这里会显示完整分析结果。"
      />
    </UiPanel>
  </div>
</template>


<script setup lang="ts">
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'

import { useAnalysisRuns } from '@/composables/useAnalysisRuns'
import { useRunStream } from '@/composables/useRunStream'
import { api } from '@/services/api'
import { useAppStore } from '@/stores/legacy'
import { formatShortTime, formatTime, statusTone } from '@/utils/formatters'
import type { ApiDetail, TradeDetail } from '@/types'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiPanel from '@/components/ui/UiPanel.vue'

const LIVE_MARKDOWN_RENDER_MIN_INTERVAL_MS = 32
const LIVE_OUTPUT_LOADING_HTML = '<p class="live-output-loading">正在生成最终结论...</p>'

let liveMarkdownRendererPromise: Promise<(content: string) => string> | null = null

async function getLiveMarkdownRenderer() {
  if (!liveMarkdownRendererPromise) {
    liveMarkdownRendererPromise = Promise.all([
      import('dompurify'),
      import('marked'),
    ]).then(([domPurifyModule, markedModule]) => {
      const DOMPurify = domPurifyModule.default
      const { marked } = markedModule
      return (content: string) => {
        const rawHtml = marked.parse(content)
        return DOMPurify.sanitize(typeof rawHtml === 'string' ? rawHtml : '')
      }
    })
  }

  return liveMarkdownRendererPromise
}

const store = useAppStore()

const {
  selectedRun,
  selectedRunLoading,
  todayRuns,
  historyRuns,
  selectedDate,
  errorMessage: analysisError,
  renderedOutputHtml,
  renderedOutputLoading,
  loadInitialRuns,
  selectRun,
  refreshRunDetail,
  ensureRawToolPreview,
  loadHistoryRuns,
} = useAnalysisRuns({
  listRunsPage: api.listRunsPage,
  loadRunDetail: store.loadRunDetail,
  loadRawToolPreview: api.getRunRawToolPreview,
})

onMounted(() => {
  loadInitialRuns({ syncSelection: !liveFocused.value })
  if (!store.schedules.length) {
    store.loadSchedule().catch(() => {})
  }
})

const runStream = useRunStream()
const {
  liveActive,
  liveElapsed,
  liveOutputText,
  manualRunning,
  liveRunTypeLabel,
  liveRunId,
  liveFocused,
  liveStartedAtIso,
  liveVisible,
  pendingPostRunId,
} = runStream

const activeManualAction = ref<'analysis' | 'trade' | null>(null)

const livePlaceholderVisible = computed(() => {
  if (!liveActive.value) return false
  if (liveRunId.value === null) return true
  return !todayRuns.value.some((item) => item.id === liveRunId.value)
})

const preMarketScheduleId = computed(() => {
  const match = store.schedules.find((item) => item.name === '盘前分析')
  return match?.id ?? null
})

const manualRunTypeText = computed(() => {
  const match = preMarketScheduleId.value === null
    ? null
    : store.schedules.find((item) => item.id === preMarketScheduleId.value)

  return match?.run_type === 'trade' ? '交易任务' : '分析任务'
})

const tradeScheduleId = computed(() => {
  const match = store.schedules.find((item) => item.run_type === 'trade')
  return match?.id ?? null
})

const manualTradeRunTypeText = computed(() => '交易任务')

const manualRunButtonTitle = computed(() =>
  preMarketScheduleId.value === null
    ? '未找到盘前分析任务，将使用默认调度执行'
    : '手动执行一次盘前分析',
)

const manualTradeButtonTitle = computed(() =>
  tradeScheduleId.value === null
    ? '未找到交易任务，将使用默认手动交易模板执行'
    : '手动执行一次交易任务',
)

async function startManualRun(options: {
  scheduleId?: number
  runType?: 'analysis' | 'trade'
  action: 'analysis' | 'trade'
  runTypeLabel: string
}) {
  if (manualRunning.value) return
  const startedAt = Date.now()
  manualRunning.value = true
  activeManualAction.value = options.action
  liveFocused.value = true
  try {
    const { run_id } = await api.runNowStream(options.scheduleId, options.runType)
    runStream.start(run_id, {
      startedAt,
      runTypeLabel: options.runTypeLabel,
    }).catch((err) => {
      console.error('[TasksView] stream start failed', err)
    })
  } catch (error) {
    console.error('[TasksView] manual run failed', error)
    manualRunning.value = false
    activeManualAction.value = null
    liveFocused.value = false
  }
}

async function handleManualRun() {
  await startManualRun({
    scheduleId: preMarketScheduleId.value ?? undefined,
    action: 'analysis',
    runTypeLabel: manualRunTypeText.value,
  })
}

async function handleManualTrade() {
  await startManualRun({
    scheduleId: tradeScheduleId.value ?? undefined,
    runType: tradeScheduleId.value == null ? 'trade' : undefined,
    action: 'trade',
    runTypeLabel: manualTradeRunTypeText.value,
  })
}

async function handleDeleteRun(runId: number) {
  const confirmed = window.confirm(`确定删除任务 #${runId} 吗？`)
  if (!confirmed) return

  try {
    await api.deleteRun(runId)
  } catch (error) {
    const message = (error as Error).message || '删除任务失败'
    if (message.includes('运行中的任务不可删除')) {
      const forceConfirmed = window.confirm(
        `任务 #${runId} 仍显示为进行中。若它其实已经卡死，可以强制删除。是否继续？`,
      )
      if (!forceConfirmed) return
      try {
        await api.deleteRun(runId, true)
      } catch (forceError) {
        console.error('[TasksView] force delete run failed', forceError)
        window.alert((forceError as Error).message || '强制删除任务失败')
        return
      }
    } else {
      console.error('[TasksView] delete run failed', error)
      window.alert(message)
      return
    }
  }

  if (selectedRun.value?.id === runId) {
    liveFocused.value = false
  }
  await Promise.all([
    loadInitialRuns({ syncSelection: true }),
    selectedDate.value ? loadHistoryRuns() : Promise.resolve(),
    store.refreshAfterRunCompletion(),
  ])
}

function focusLiveCard() {
  liveFocused.value = true
  clearPreviewFocus()
}

function isTodayRunActive(runId: number) {
  if (liveFocused.value && liveRunId.value === runId) {
    return true
  }
  return !liveFocused.value && selectedRun.value?.id === runId
}

function isTodayRunLive(runId: number) {
  return liveActive.value && liveRunId.value === runId
}

function handleTodayRunSelect(runId: number) {
  if (liveRunId.value === runId && runStream.state.status !== 'idle') {
    focusLiveCard()
    return
  }
  handleSelectRun(runId, todayRuns.value)
}

async function reconcileFinishedRun() {
  const runId = pendingPostRunId.value
  if (runId === null) return
  pendingPostRunId.value = null

  const results = await Promise.allSettled([
    loadInitialRuns({ syncSelection: false }),
    refreshRunDetail(runId),
    store.refreshAfterRunCompletion(),
  ])
  const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
  if (failed) {
    console.error('[TasksView] post-run refresh failed', failed.reason)
  }
}

watch(
  () => runStream.state.status,
  (status) => {
    if (status !== 'connecting' && status !== 'running') {
      activeManualAction.value = null
    }
    if (status === 'completed' || status === 'failed') {
      void reconcileFinishedRun()
    }
  },
)

onMounted(() => {
  // Replay the post-completion reconcile when returning to this view
  // after a run finished while the component was unmounted.
  if (pendingPostRunId.value !== null) {
    void reconcileFinishedRun()
  }
})

onBeforeUnmount(() => {
  // Intentionally NOT stopping runStream here: the singleton keeps the SSE
  // connection alive across route changes so returning to this tab
  // still shows the live progress and the post-run refresh.
})

const historyDateInput = ref<HTMLInputElement | null>(null)
const activePreviewIndex = ref<number | null>(null)
const activePreviewLoading = ref(false)
const activePreviewError = ref('')
const apiListRef = ref<HTMLElement | null>(null)
const tradeListRef = ref<HTMLElement | null>(null)
const liveOutputRef = ref<HTMLElement | null>(null)
const liveRenderedOutputHtml = ref('')
const shouldFollowApiScroll = ref(true)
const shouldFollowTradeScroll = ref(true)
const shouldFollowLiveOutputScroll = ref(true)

let liveMarkdownFrameId: number | null = null
let liveMarkdownLatestContent = ''
let liveMarkdownLastRenderAt = 0
let liveMarkdownRenderTicket = 0

const detailGridVisible = computed(() => liveVisible.value || !!selectedRun.value)

const displayStatusStartTime = computed(() =>
  liveVisible.value ? liveStartedAtIso.value : selectedRun.value?.startTime ?? null,
)

const displayStatusDuration = computed(() =>
  liveVisible.value ? liveElapsed.value : selectedRun.value?.duration ?? '--',
)

const displayTokenBarVisible = computed(() => liveVisible.value || !!selectedRun.value)

const displayTokenSource = computed(() => {
  if (!liveVisible.value) {
    return selectedRun.value
  }

  if (selectedRun.value?.id === liveRunId.value) {
    return selectedRun.value
  }

  return null
})

const displayInputTokens = computed(() => displayTokenSource.value?.inputTokens ?? '--')

const displayOutputTokens = computed(() => displayTokenSource.value?.outputTokens ?? '--')

const displayTotalTokens = computed(() => displayTokenSource.value?.totalTokens ?? '--')

const displayStatusDotClass = computed(() => {
  if (liveVisible.value) {
    if (runStream.state.status === 'failed' || runStream.state.status === 'error') {
      return 'dot-tone-error'
    }
    if (runStream.state.status === 'completed') {
      return 'dot-tone-success'
    }
    return 'dot-running'
  }

  return `dot-${statusTone(selectedRun.value?.status ?? 'idle')}`
})

const displayApiDetails = computed<ApiDetail[]>(() => {
  if (liveVisible.value) {
    return runStream.state.apiDetails
  }

  return selectedRun.value?.apiDetails ?? []
})

const displayTradeDetails = computed<TradeDetail[]>(() => {
  if (liveVisible.value) {
    return runStream.state.tradeDetails
  }

  return selectedRun.value?.tradeDetails ?? []
})

const liveRunStillRunning = computed(
  () => runStream.state.status === 'connecting' || runStream.state.status === 'running',
)

const displayApiEmptyText = computed(() =>
  liveVisible.value
    ? liveRunStillRunning.value
      ? '正在等待接口调用...'
      : '本次分析没有生成可展示的接口调用记录。'
    : '本次分析没有生成可展示的接口调用记录。',
)

const displayTradeEmptyText = computed(() =>
  liveVisible.value
    ? liveRunStillRunning.value
      ? '当前暂无交易执行记录。'
      : '本次分析没有生成可展示的模拟操作。'
    : '本次分析没有生成可展示的模拟操作。',
)

const liveOutputIsPlaceholder = computed(
  () => liveVisible.value && !runStream.state.finalStarted,
)

const liveOutputHtml = computed(() => {
  if (liveOutputIsPlaceholder.value) {
    return ''
  }
  return liveRenderedOutputHtml.value || LIVE_OUTPUT_LOADING_HTML
})

const runErrorVisible = computed(() => {
  if (liveVisible.value) {
    return runStream.state.status === 'failed' || runStream.state.status === 'error'
  }

  return selectedRun.value?.status === 'failed'
})

const runErrorMessage = computed(() => {
  if (liveVisible.value) {
    return runStream.state.errorMessage || '当前实时执行失败，请优先检查后端运行日志、模型配置或事件流连接状态。'
  }

  return '当前记录执行失败，请优先检查后端运行日志、模型配置或妙想接口状态。'
})

function getApiItemKey(api: ApiDetail, idx: number) {
  return api.stream_key || api.tool_call_id || `${api.tool_name}-${api.preview_index ?? 'live'}-${idx}`
}

function getApiItemStatusClass(api: ApiDetail) {
  if (api.status === 'running') {
    return 'is-running'
  }
  if (api.status === 'failed' || api.ok === false) {
    return 'is-failed'
  }
  return 'is-success'
}

function getTradeItemStatusClass(trade: TradeDetail) {
  if (trade.status === 'running') {
    return 'is-running'
  }
  if (trade.status === 'failed' || trade.ok === false) {
    return 'is-failed'
  }
  return 'is-success'
}

function getTradeItemKey(trade: TradeDetail, idx: number) {
  return trade.stream_key || `${trade.action}-${trade.symbol}-${trade.preview_index ?? 'live'}-${idx}`
}

const activePreview = computed(() => {
  if (typeof activePreviewIndex.value !== 'number') {
    return null
  }
  if (!selectedRun.value) {
    return null
  }
  return selectedRun.value.rawToolPreviews.find((item) => item.preview_index === activePreviewIndex.value) ?? null
})

const activePreviewText = computed(() => {
  if (activePreviewError.value) {
    return activePreviewError.value
  }
  if (activePreviewLoading.value && activePreview.value?.truncated) {
    return '正在加载完整原文...'
  }
  return activePreview.value?.preview ?? ''
})

const historyDateDisplay = computed(() => {
  if (!selectedDate.value) {
    return '年/月/日'
  }

  const [year, month, day] = selectedDate.value.split('-')
  if (!year || !month || !day) {
    return '年/月/日'
  }

  return `${year}/${month}/${day}`
})

function openHistoryDatePicker() {
  const input = historyDateInput.value
  if (!input) {
    return
  }

  if ('showPicker' in input && typeof input.showPicker === 'function') {
    input.showPicker()
    return
  }

  input.focus()
  input.click()
}

function handleSelectRun(runId: number, runs: typeof todayRuns.value) {
  const target = runs.find((item) => item.id === runId)
  if (!target) {
    return
  }
  liveFocused.value = false
  void selectRun(target, { force: target.id === liveRunId.value })
}

async function focusPreview(index: number | null) {
  if (typeof index !== 'number') {
    return
  }

  if (activePreviewIndex.value === index) {
    clearPreviewFocus()
    return
  }

  activePreviewIndex.value = index
  activePreviewError.value = ''

  const runId = selectedRun.value?.id
  if (typeof runId !== 'number') {
    return
  }

  const preview = selectedRun.value?.rawToolPreviews.find((item) => item.preview_index === index)
  if (!preview || !preview.truncated) {
    activePreviewLoading.value = false
    return
  }

  activePreviewLoading.value = true
  try {
    await ensureRawToolPreview(runId, index)
  } catch (error) {
    console.error('[TasksView] raw preview load failed', error)
    if (activePreviewIndex.value === index) {
      activePreviewError.value = (error as Error).message || '完整原文加载失败'
    }
  } finally {
    if (activePreviewIndex.value === index) {
      activePreviewLoading.value = false
    }
  }
}

function clearPreviewFocus() {
  activePreviewIndex.value = null
  activePreviewLoading.value = false
  activePreviewError.value = ''
}

function isNearBottom(element: HTMLElement) {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= 28
}

async function scrollContainerToBottom(
  elementRef: typeof apiListRef,
  followRef: typeof shouldFollowApiScroll,
) {
  await nextTick()
  const element = elementRef.value
  if (!element || !followRef.value) {
    return
  }
  window.requestAnimationFrame(() => {
    element.scrollTop = element.scrollHeight
  })
}

function handleApiListScroll() {
  const element = apiListRef.value
  shouldFollowApiScroll.value = !element || isNearBottom(element)
}

function handleTradeListScroll() {
  const element = tradeListRef.value
  shouldFollowTradeScroll.value = !element || isNearBottom(element)
}

function handleLiveOutputScroll() {
  const element = liveOutputRef.value
  shouldFollowLiveOutputScroll.value = !element || isNearBottom(element)
}

function clearLiveMarkdownRenderFrame() {
  if (liveMarkdownFrameId !== null) {
    window.cancelAnimationFrame(liveMarkdownFrameId)
    liveMarkdownFrameId = null
  }
}

async function renderLiveMarkdown(content: string, ticket: number) {
  const renderMarkdown = await getLiveMarkdownRenderer()
  if (ticket !== liveMarkdownRenderTicket) {
    return
  }
  liveRenderedOutputHtml.value = content
    ? renderMarkdown(content)
    : LIVE_OUTPUT_LOADING_HTML
}

function flushLiveMarkdownRender(frameTime: number) {
  liveMarkdownFrameId = null

  if (frameTime - liveMarkdownLastRenderAt < LIVE_MARKDOWN_RENDER_MIN_INTERVAL_MS) {
    liveMarkdownFrameId = window.requestAnimationFrame(flushLiveMarkdownRender)
    return
  }

  liveMarkdownLastRenderAt = frameTime
  liveMarkdownRenderTicket += 1
  const ticket = liveMarkdownRenderTicket
  const content = liveMarkdownLatestContent

  void renderLiveMarkdown(content, ticket).finally(() => {
    if (ticket !== liveMarkdownRenderTicket) {
      return
    }
    if (content !== liveMarkdownLatestContent && liveMarkdownFrameId === null) {
      liveMarkdownFrameId = window.requestAnimationFrame(flushLiveMarkdownRender)
    }
  })
}

function scheduleLiveMarkdownRender(content: string) {
  liveMarkdownLatestContent = content
  if (liveMarkdownFrameId !== null) {
    return
  }
  liveMarkdownFrameId = window.requestAnimationFrame(flushLiveMarkdownRender)
}

function resetLiveMarkdownRenderState() {
  clearLiveMarkdownRenderFrame()
  liveMarkdownRenderTicket += 1
  liveMarkdownLatestContent = ''
  liveMarkdownLastRenderAt = 0
  liveRenderedOutputHtml.value = ''
}

function handleOutputSurfaceClick(event: MouseEvent) {
  if (!activePreview.value) {
    return
  }
  const target = event.target
  if (!(target instanceof HTMLElement)) {
    return
  }
  if (target.closest('.compact-item-button')) {
    return
  }
  if (target.closest('.raw-output-content')) {
    return
  }
  clearPreviewFocus()
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && activePreviewIndex.value !== null) {
    clearPreviewFocus()
  }
}

watch(
  () => selectedRun.value?.id,
  () => {
    clearPreviewFocus()
  },
  { immediate: true },
)

watch(
  () => displayApiDetails.value.length,
  (nextLength, previousLength = 0) => {
    if (!liveVisible.value || nextLength <= previousLength) {
      return
    }
    void scrollContainerToBottom(apiListRef, shouldFollowApiScroll)
  },
)

watch(
  () => displayTradeDetails.value.length,
  (nextLength, previousLength = 0) => {
    if (!liveVisible.value || nextLength <= previousLength) {
      return
    }
    void scrollContainerToBottom(tradeListRef, shouldFollowTradeScroll)
  },
)

watch(
  () => liveOutputIsPlaceholder.value ? liveOutputText.value : liveOutputHtml.value,
  (nextValue, previousValue = '') => {
    if (!liveVisible.value || nextValue === previousValue) {
      return
    }
    void scrollContainerToBottom(liveOutputRef, shouldFollowLiveOutputScroll)
  },
)

watch(
  () => [liveVisible.value, runStream.state.finalStarted, runStream.state.finalAnswer] as const,
  ([visible, finalStarted, content]) => {
    if (!visible || !finalStarted) {
      resetLiveMarkdownRenderState()
      return
    }
    void getLiveMarkdownRenderer()
    scheduleLiveMarkdownRender(content)
  },
  { immediate: true },
)

watch(
  () => liveVisible.value,
  (visible) => {
    if (!visible) {
      return
    }
    shouldFollowApiScroll.value = true
    shouldFollowTradeScroll.value = true
    shouldFollowLiveOutputScroll.value = true
    void scrollContainerToBottom(apiListRef, shouldFollowApiScroll)
    void scrollContainerToBottom(tradeListRef, shouldFollowTradeScroll)
    void scrollContainerToBottom(liveOutputRef, shouldFollowLiveOutputScroll)
  },
)

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown)
  resetLiveMarkdownRenderState()
})


function mapStatusDot(cls: string) {
  if (cls.includes('running') || cls.includes('tone-info') || cls.includes('info')) {
    return 'animate-pulse-dot bg-accent'
  }
  if (cls.includes('success')) return 'bg-success'
  if (cls.includes('failed') || cls.includes('error') || cls.includes('danger')) return 'bg-danger'
  if (cls.includes('warning')) return 'bg-warning'
  return 'bg-label-quaternary'
}

function statusDotClass(status: string | null | undefined) {
  return mapStatusDot(statusTone(status ?? 'idle'))
}

const displayStatusDotTailwind = computed(() => mapStatusDot(displayStatusDotClass.value))

function apiStatusDot(api: ApiDetail) {
  return mapStatusDot(getApiItemStatusClass(api))
}

function tradeStatusDot(trade: TradeDetail) {
  return mapStatusDot(getTradeItemStatusClass(trade))
}

</script>

<style scoped>
@reference "../styles/tailwind.css";

.run-card {
  @apply relative flex min-h-[72px] flex-col items-start gap-0.5 rounded-[14px] border border-separator bg-card-solid px-3 py-2.5 text-left shadow-sm transition-all;
  @apply hover:border-accent/30 hover:bg-accent-soft/40;
}
.run-card.active {
  @apply border-accent/40 bg-accent-soft ring-2 ring-accent-ring;
}
.run-card.live {
  @apply border-accent/30;
}
.run-type {
  @apply mt-1 text-caption font-semibold text-label;
}
.run-time {
  @apply text-footnote font-semibold tabular-nums text-label;
}
.run-duration {
  @apply text-caption text-label-tertiary;
}
.status-dot {
  @apply inline-block size-2 rounded-full;
}
.token-chip {
  @apply inline-flex items-center gap-1.5 rounded-pill bg-card-solid px-2.5 py-1 text-caption text-label-secondary ring-1 ring-separator;
}
.token-chip i {
  @apply not-italic text-label-tertiary;
}
.token-chip b {
  @apply font-semibold tabular-nums text-label;
}
.token-chip.emphasis {
  @apply text-accent-text ring-accent/20;
}
.token-chip.emphasis b {
  @apply text-accent-text;
}
.detail-item {
  @apply flex w-full items-center gap-2 rounded-[12px] border border-transparent bg-card-solid px-3 py-2.5 text-left transition-colors;
  @apply hover:bg-hover;
}
.detail-item.active {
  @apply border-accent/30 bg-accent-soft;
}

.stream-reveal-enter-active,
.stream-reveal-leave-active {
  transition: all 0.2s ease;
}
.stream-reveal-enter-from,
.stream-reveal-leave-to {
  opacity: 0;
  transform: translateY(4px);
}
</style>
