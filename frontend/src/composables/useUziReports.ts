/**
 * UZI 报告列表页状态（文档 §10 / §15.2）。
 *
 * 负责：模块状态、历史列表（分页/筛选）、创建、取消、删除。
 * 活动任务的 SSE 实时更新由 useUziReportStream 负责。
 */
import { computed, ref } from 'vue'

import { api } from '@/services/api'
import type {
  CreateUziReportResponse,
  UziReportStatus,
  UziReportSummary,
  UziSourceStatus,
  UziStatus,
} from '@/types'
import { UZI_TERMINAL_STATUSES } from '@/types'

const PAGE_SIZE = 20

export function useUziReports() {
  const uziStatus = ref<UziStatus | null>(null)
  const sourceStatus = ref<UziSourceStatus | null>(null)
  const sourceUpdating = ref(false)
  const sourceUpdateError = ref('')
  const sourceUpdateNotice = ref('')
  const statusLoading = ref(false)

  const items = ref<UziReportSummary[]>([])
  const total = ref(0)
  const limit = ref(PAGE_SIZE)
  const offset = ref(0)
  const listLoading = ref(false)
  const listError = ref('')

  const tickerFilter = ref('')
  const statusFilter = ref<UziReportStatus | ''>('')

  const creating = ref(false)
  const createError = ref('')
  const createNotice = ref('')

  const cancellingId = ref<number | null>(null)
  const deletingId = ref<number | null>(null)

  const page = computed(() => Math.floor(offset.value / limit.value) + 1)
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))

  const moduleEnabled = computed(() => uziStatus.value?.enabled === true)
  const workerReady = computed(
    () => uziStatus.value?.enabled === true && uziStatus.value?.worker_available === true,
  )
  const unavailableReason = computed(() => uziStatus.value?.reason || null)

  const activeItems = computed(() =>
    items.value.filter((item) => !UZI_TERMINAL_STATUSES.includes(item.status)),
  )

  async function fetchStatus(): Promise<void> {
    statusLoading.value = true
    try {
      uziStatus.value = await api.getUziStatus()
    } catch (err) {
      uziStatus.value = null
      listError.value = (err as Error).message || '获取 UZI 状态失败。'
      createError.value = listError.value || 'UZI 状态检测失败，暂时无法创建报告。'
    } finally {
      statusLoading.value = false
    }
  }

  async function fetchSourceStatus(): Promise<void> {
    try {
      sourceStatus.value = await api.getUziSourceStatus()
    } catch (err) {
      sourceStatus.value = null
      sourceUpdateError.value = (err as Error).message || '获取 UZI 上游版本失败。'
    }
  }

  async function updateSource(): Promise<boolean> {
    sourceUpdating.value = true
    sourceUpdateError.value = ''
    sourceUpdateNotice.value = ''
    try {
      sourceStatus.value = await api.updateUziSource()
      sourceUpdateNotice.value = sourceStatus.value.message || 'UZI 上游源码已更新。'
      await fetchStatus()
      return true
    } catch (err) {
      sourceUpdateError.value = (err as Error).message || 'UZI 上游更新失败。'
      return false
    } finally {
      sourceUpdating.value = false
    }
  }

  async function fetchList(): Promise<void> {
    listLoading.value = true
    listError.value = ''
    try {
      const result = await api.listUziReports({
        limit: limit.value,
        offset: offset.value,
        ticker: tickerFilter.value.trim() || undefined,
        status: statusFilter.value || undefined,
      })
      items.value = result.items
      total.value = result.total
      limit.value = result.limit
      offset.value = result.offset
    } catch (err) {
      listError.value = (err as Error).message || '获取报告列表失败。'
    } finally {
      listLoading.value = false
    }
  }

  function goToPage(targetPage: number): void {
    const next = Math.max(1, Math.min(totalPages.value, targetPage))
    if (next === page.value) return
    offset.value = (next - 1) * limit.value
    void fetchList()
  }

  function applyFilter(): void {
    offset.value = 0
    void fetchList()
  }

  async function createReport(ticker: string): Promise<CreateUziReportResponse | null> {
    const normalized = ticker.trim()
    if (!normalized) {
      createError.value = '请输入股票代码或名称。'
      return null
    }
    creating.value = true
    createError.value = ''
    createNotice.value = ''
    try {
      const result = await api.createUziReport({ ticker: normalized })
      if (result.reused) {
        createNotice.value =
          '该股票已有进行中的任务，已为你订阅其进度（不会重复创建）。'
      }
      await fetchStatus()
      await fetchList()
      return result
    } catch (err) {
      createError.value = (err as Error).message || '创建报告失败。'
      return null
    } finally {
      creating.value = false
    }
  }

  async function cancelReport(reportId: number): Promise<boolean> {
    cancellingId.value = reportId
    try {
      await api.cancelUziReport(reportId)
      await fetchList()
      return true
    } catch (err) {
      listError.value = (err as Error).message || '取消失败。'
      return false
    } finally {
      cancellingId.value = null
    }
  }

  async function deleteReport(reportId: number): Promise<boolean> {
    deletingId.value = reportId
    try {
      await api.deleteUziReport(reportId)
      await fetchList()
      return true
    } catch (err) {
      listError.value = (err as Error).message || '删除失败。'
      return false
    } finally {
      deletingId.value = null
    }
  }

  function refresh(): void {
    void fetchStatus()
    void fetchList()
  }

  return {
    uziStatus,
    sourceStatus,
    sourceUpdating,
    sourceUpdateError,
    sourceUpdateNotice,
    statusLoading,
    moduleEnabled,
    workerReady,
    unavailableReason,
    items,
    total,
    limit,
    offset,
    page,
    totalPages,
    listLoading,
    listError,
    tickerFilter,
    statusFilter,
    creating,
    createError,
    createNotice,
    cancellingId,
    deletingId,
    activeItems,
    fetchStatus,
    fetchSourceStatus,
    updateSource,
    fetchList,
    goToPage,
    applyFilter,
    createReport,
    cancelReport,
    deleteReport,
    refresh,
  }
}
