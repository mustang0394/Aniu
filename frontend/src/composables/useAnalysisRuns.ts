import { computed, ref, watch } from 'vue'

import type { ApiDetail, RawToolPreview, RawToolPreviewDetail, RunDetail, RunSummary, RunSummaryPage, TradeDetail, TradeOrder } from '@/types'

export interface AnalysisRunViewModel {
  id: number
  analysisType: string
  startTime: string
  endTime: string | null
  duration: string
  status: string
  apiCalls: number
  tradeCount: number
  inputTokens: string
  outputTokens: string
  totalTokens: string
  apiDetails: ApiDetail[]
  rawToolPreviews: RawToolPreview[]
  tradeDetails: TradeDetail[]
  output: string | null
  summary: string
  detailLoaded: boolean
}

const RUNS_PAGE_SIZE = 100

function formatTokenValue(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? String(value) : '--'
}

function getTokenUsage(detail: RunDetail) {
  // 后端已预计算并持久化 token 用量，前端不再反序列化超大 payload
  return {
    input: formatTokenValue(detail.input_tokens),
    output: formatTokenValue(detail.output_tokens),
    total: formatTokenValue(detail.total_tokens),
  }
}

function getDuration(startedAt: string, finishedAt: string | null) {
  if (!finishedAt) {
    return '进行中'
  }

  const start = new Date(startedAt).getTime()
  const end = new Date(finishedAt).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) {
    return '--'
  }

  const totalSeconds = Math.floor((end - start) / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}分${String(seconds).padStart(2, '0')}秒`
}

function getRunTypeText(detail: Pick<RunDetail, 'run_type' | 'trigger_source'>) {
  if (detail.run_type === 'trade') return '交易任务'
  if (detail.run_type === 'analysis') return '分析任务'
  if (detail.trigger_source === 'manual') return '手动运行'
  return '任务运行'
}

function extractTradeName(payload: unknown) {
  if (!payload || typeof payload !== 'object') {
    return ''
  }

  const candidates = [
    (payload as { name?: unknown }).name,
    (payload as { stock_name?: unknown }).stock_name,
    (payload as { stockName?: unknown }).stockName,
    (payload as { security_name?: unknown }).security_name,
    (payload as { securityName?: unknown }).securityName,
  ]

  for (const candidate of candidates) {
    const value = String(candidate ?? '').trim()
    if (value) {
      return value
    }
  }

  const result = (payload as { result?: unknown }).result
  if (result && result !== payload) {
    return extractTradeName(result)
  }

  return ''
}

function getTradeSummary(action: 'buy' | 'sell', symbol: string, name: string, volume: number, price: number | null, amount: number | null) {
  void name
  void price
  void amount

  const displaySymbol = symbol || '--'
  const actionText = action === 'sell' ? '卖出' : '买入'

  return `挂单${actionText}${displaySymbol}共计${volume}股。`
}

function resolveTradeDetailStatus(value: unknown): 'done' | 'failed' {
  const text = String(value ?? '').trim().toLowerCase()
  if (text && ['fail', 'error', 'reject'].some((flag) => text.includes(flag))) {
    return 'failed'
  }
  return 'done'
}

function mapTradeDetails(tradeOrders: TradeOrder[]): TradeDetail[] {
  return tradeOrders.map((order) => {
    const price = order.price
    const action = String(order.action).toUpperCase() === 'SELL' ? 'sell' : 'buy'
    const name = extractTradeName(order.response_payload) || order.symbol
    const amount = price == null ? null : Number((price * order.quantity).toFixed(2))
    const status = resolveTradeDetailStatus(order.status)
    return {
      action,
      action_text: action === 'sell' ? '模拟卖出' : '模拟买入',
      symbol: order.symbol,
      name,
      volume: order.quantity,
      price,
      amount,
      summary: getTradeSummary(action, order.symbol, name, order.quantity, price, amount),
      tool_name: null,
      preview_index: null,
      status,
      ok: status !== 'failed',
    }
  })
}

function mapRunSummaryToViewModel(summary: RunSummary): AnalysisRunViewModel {
  return {
    id: summary.id,
    analysisType: getRunTypeText(summary),
    startTime: summary.started_at,
    endTime: summary.finished_at,
    duration: getDuration(summary.started_at, summary.finished_at),
    status: summary.status,
    apiCalls: summary.api_call_count,
    tradeCount: summary.executed_trade_count,
    inputTokens: formatTokenValue(summary.input_tokens),
    outputTokens: formatTokenValue(summary.output_tokens),
    totalTokens: formatTokenValue(summary.total_tokens),
    apiDetails: [],
    rawToolPreviews: [],
    tradeDetails: [],
    output: null,
    summary: summary.analysis_summary || '--',
    detailLoaded: false,
  }
}

function mapRunDetailToViewModel(detail: RunDetail): AnalysisRunViewModel {
  const tokenUsage = getTokenUsage(detail)
  const apiDetails = Array.isArray(detail.api_details) ? detail.api_details : []
  const rawToolPreviews = Array.isArray(detail.raw_tool_previews) ? detail.raw_tool_previews : []
  const tradeDetails = detail.trade_details?.length ? detail.trade_details : mapTradeDetails(detail.trade_orders)
  const output = detail.output_markdown || detail.final_answer || detail.analysis_summary || detail.error_message || '暂无分析输出'

  return {
    id: detail.id,
    analysisType: getRunTypeText(detail),
    startTime: detail.started_at,
    endTime: detail.finished_at,
    duration: getDuration(detail.started_at, detail.finished_at),
    status: detail.status,
    apiCalls: apiDetails.length,
    tradeCount: tradeDetails.length,
    inputTokens: tokenUsage.input,
    outputTokens: tokenUsage.output,
    totalTokens: tokenUsage.total,
    apiDetails,
    rawToolPreviews,
    tradeDetails,
    output,
    summary: detail.analysis_summary || '--',
    detailLoaded: true,
  }
}

function isSameDay(value: string, target: Date) {
  const date = new Date(value)
  return date.getFullYear() === target.getFullYear()
    && date.getMonth() === target.getMonth()
    && date.getDate() === target.getDate()
}

function getLatestRun(runs: AnalysisRunViewModel[]) {
  if (runs.length === 0) {
    return null
  }

  return runs.reduce((latest, current) => {
    const latestTime = new Date(latest.startTime).getTime()
    const currentTime = new Date(current.startTime).getTime()
    return currentTime > latestTime ? current : latest
  })
}

export function useAnalysisRuns(options: {
  listRunsPage: (options?: { limit?: number, date?: string, status?: string, beforeId?: number }) => Promise<RunSummaryPage>
  loadRunDetail: (runId: number, options?: { force?: boolean }) => Promise<RunDetail>
  loadRawToolPreview: (runId: number, previewIndex: number) => Promise<RawToolPreviewDetail>
}) {
  const selectedRun = ref<AnalysisRunViewModel | null>(null)
  const selectedRunLoading = ref(false)
  const renderedOutputHtml = ref('')
  const renderedOutputLoading = ref(false)
  const todayRuns = ref<AnalysisRunViewModel[]>([])
  const historyRuns = ref<AnalysisRunViewModel[]>([])
  const selectedDate = ref('')
  const loading = ref(false)
  const errorMessage = ref('')
  const runCache = new Map<number, AnalysisRunViewModel>()
  const markdownCache = new Map<string, string>()
  const sourceSummaries = ref<RunSummary[]>([])
  const rawToolPreviewRequests = new Map<string, Promise<RawToolPreviewDetail>>()

  let markdownRendererPromise: Promise<((content: string) => string)> | null = null

  const allRuns = computed(() => sourceSummaries.value)

  function shouldIncludeRun(run: AnalysisRunViewModel) {
    return !!run
  }

  function filterVisibleRuns(runs: AnalysisRunViewModel[]) {
    return runs.filter(shouldIncludeRun)
  }

  async function hydrateSelectedRun(runId: number, force = false) {
    selectedRunLoading.value = true

    try {
      const detail = await ensureRunDetail(runId, force)
      if (selectedRun.value?.id === runId) {
        selectedRun.value = detail
      }
    } finally {
      if (selectedRun.value?.id === runId) {
        selectedRunLoading.value = false
      }
    }
  }

  async function syncSelectedRun(runs: AnalysisRunViewModel[]) {
    if (selectedRun.value && runs.some((run) => run.id === selectedRun.value?.id)) {
      selectedRun.value = runs.find((run) => run.id === selectedRun.value?.id) ?? selectedRun.value
      await hydrateSelectedRun(selectedRun.value.id)
      return
    }

    selectedRun.value = getLatestRun(runs)
    if (selectedRun.value) {
      await hydrateSelectedRun(selectedRun.value.id)
      return
    }

    selectedRunLoading.value = false
  }

  async function ensureRunDetail(runId: number, force = false) {
    if (!force && runCache.has(runId)) {
      return runCache.get(runId)!
    }

    const detail = await options.loadRunDetail(runId, { force })
    const mapped = mapRunDetailToViewModel(detail)
    runCache.set(runId, mapped)
    return mapped
  }

  /** 删除运行记录后调用：清除该 run 的详情缓存与预览请求，防止 rowid 复用时命中旧数据。 */
  function evictRunDetail(runId: number) {
    runCache.delete(runId)
    for (const key of [...rawToolPreviewRequests.keys()]) {
      if (key.startsWith(`${runId}:`)) {
        rawToolPreviewRequests.delete(key)
      }
    }
    if (selectedRun.value?.id === runId) {
      selectedRun.value = null
    }
  }

  /** 切换交易账户后调用：runCache 无账户维度，必须整体清空防止跨账户串数据。 */
  function clearRunDetailCache() {
    runCache.clear()
    rawToolPreviewRequests.clear()
    markdownCache.clear()
  }

  async function refreshRunDetail(runId: number) {
    const detail = await ensureRunDetail(runId, true)
    if (selectedRun.value?.id === runId) {
      selectedRun.value = detail
    }
    return detail
  }

  async function ensureRawToolPreview(runId: number, previewIndex: number): Promise<RawToolPreview> {
    const run = runCache.get(runId)
    const cachedPreview = run?.rawToolPreviews.find((item) => item.preview_index === previewIndex) ?? null
    if (cachedPreview && !cachedPreview.truncated) {
      return cachedPreview
    }

    const requestKey = `${runId}:${previewIndex}`
    const pendingRequest = rawToolPreviewRequests.get(requestKey)
    if (pendingRequest) {
      const detail = await pendingRequest
      return applyRawToolPreviewDetail(runId, detail)
    }

    const request = options.loadRawToolPreview(runId, previewIndex)
    rawToolPreviewRequests.set(requestKey, request)
    try {
      const detail = await request
      return applyRawToolPreviewDetail(runId, detail)
    } finally {
      rawToolPreviewRequests.delete(requestKey)
    }
  }

  function applyRawToolPreviewDetail(runId: number, detail: RawToolPreviewDetail): RawToolPreview {
    const run = runCache.get(runId)
    const nextPreview: RawToolPreview = {
      preview_index: detail.preview_index,
      tool_name: detail.tool_name,
      display_name: detail.display_name,
      summary: detail.summary,
      preview: detail.full_preview,
      truncated: false,
    }

    if (!run) {
      return nextPreview
    }

    const nextRun: AnalysisRunViewModel = {
      ...run,
      rawToolPreviews: run.rawToolPreviews.map((item) => (
        item.preview_index === detail.preview_index ? nextPreview : item
      )),
    }
    runCache.set(runId, nextRun)

    if (selectedRun.value?.id === runId) {
      selectedRun.value = nextRun
    }

    todayRuns.value = todayRuns.value.map((item) => (item.id === runId ? nextRun : item))
    historyRuns.value = historyRuns.value.map((item) => (item.id === runId ? nextRun : item))

    return nextPreview
  }

  async function loadInitialRuns(config: { syncSelection?: boolean } = {}) {
    const { syncSelection = true } = config
    loading.value = true
    errorMessage.value = ''

    try {
      const page = await options.listRunsPage({ limit: RUNS_PAGE_SIZE })
      sourceSummaries.value = page.items
      const today = new Date()
      const todaysSummaries = sourceSummaries.value.filter((item) => isSameDay(item.started_at, today))
      const mappedTodayRuns = todaysSummaries.map(mapRunSummaryToViewModel)

      todayRuns.value = filterVisibleRuns(mappedTodayRuns)

      if (syncSelection) {
        await syncSelectedRun(todayRuns.value)
      }
    } catch (error) {
      errorMessage.value = (error as Error).message
      todayRuns.value = []
      selectedRun.value = null
    } finally {
      loading.value = false
    }
  }

  async function selectRun(run: AnalysisRunViewModel, options?: { force?: boolean }) {
    selectedRun.value = run
    if (run.detailLoaded && !options?.force) {
      selectedRunLoading.value = false
      return
    }

    await hydrateSelectedRun(run.id, options?.force === true)
  }

  async function loadHistoryRuns() {
    if (!selectedDate.value) {
      historyRuns.value = []
      return
    }

    errorMessage.value = ''

    try {
      const page = await options.listRunsPage({
        limit: RUNS_PAGE_SIZE,
        date: selectedDate.value,
      })
      const matched = page.items
      sourceSummaries.value = mergeSourceSummaries(sourceSummaries.value, matched)
      historyRuns.value = filterVisibleRuns(matched.map(mapRunSummaryToViewModel))

      if (selectedDate.value) {
        await syncSelectedRun(historyRuns.value)
      }
    } catch (error) {
      errorMessage.value = (error as Error).message
      historyRuns.value = []
    }
  }

  async function getMarkdownRenderer() {
    if (!markdownRendererPromise) {
      markdownRendererPromise = Promise.all([
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

    return markdownRendererPromise
  }

  async function renderSelectedOutput(content: string | null) {
    if (!content) {
      renderedOutputHtml.value = ''
      renderedOutputLoading.value = false
      return
    }

    const cached = markdownCache.get(content)
    if (cached) {
      renderedOutputHtml.value = cached
      renderedOutputLoading.value = false
      return
    }

    renderedOutputLoading.value = true
    const renderMarkdown = await getMarkdownRenderer()
    const sanitized = renderMarkdown(content)
    markdownCache.set(content, sanitized)
    if (selectedRun.value?.output === content) {
      renderedOutputHtml.value = sanitized
      renderedOutputLoading.value = false
    }
  }

  watch(
    () => selectedRun.value?.output ?? null,
    (content) => {
      void renderSelectedOutput(content)
    },
    { immediate: true },
  )

  function mergeSourceSummaries(existing: RunSummary[], incoming: RunSummary[]) {
    const merged = new Map<number, RunSummary>()
    for (const item of existing) {
      merged.set(item.id, item)
    }
    for (const item of incoming) {
      merged.set(item.id, item)
    }
    return [...merged.values()].sort((a, b) => {
      const timeDelta = new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
      if (timeDelta !== 0) {
        return timeDelta
      }
      return b.id - a.id
    })
  }

  return {
    selectedRun,
    selectedRunLoading,
    todayRuns,
    historyRuns,
    selectedDate,
    loading,
    errorMessage,
    renderedOutputHtml,
    renderedOutputLoading,
    loadInitialRuns,
    selectRun,
    refreshRunDetail,
    ensureRawToolPreview,
    evictRunDetail,
    clearRunDetailCache,
    loadHistoryRuns,
  }
}
