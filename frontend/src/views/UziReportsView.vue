<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader
      title="个股深度报告"
      kicker="UZI Reports"
      description="提交股票代码或名称，由 UZI 引擎生成完整深度研究（仅支持完整深度分析）。"
    >
      <span class="text-footnote text-label-tertiary">
        UZI {{ sourceStatus?.current_version || '版本未知' }}
      </span>
      <UiButton
        variant="ghost"
        size="sm"
        :loading="sourceUpdating"
        :disabled="!workerReady || sourceUpdating || activeItems.length > 0 || sourceStatus?.can_update === false"
        :title="sourceStatus?.reason || '检查并更新 UZI 上游源码'"
        @click="handleSourceUpdate"
      >
        更新上游
      </UiButton>
    </UiPageHeader>

    <p
      v-if="sourceUpdateNotice || sourceUpdateError"
      class="-mt-3 rounded-[10px] border px-3.5 py-2.5 text-footnote font-medium break-words"
      :class="sourceUpdateError ? 'border-danger/20 bg-danger-soft text-danger-text' : 'border-accent/20 bg-accent-soft text-accent-text'"
      role="status"
    >
      {{ sourceUpdateError || sourceUpdateNotice }}
    </p>

    <!-- 生成面板 -->
    <UiPanel title="生成深度报告" kicker="Generate">
      <UziReportForm
        :disabled="!workerReady"
        :disabled-reason="unavailableReason"
        :submitting="creating"
        :error="createError"
        :preset-ticker="presetTicker"
        @submit="handleSubmit"
      />
      <p
        v-if="createNotice"
        class="mb-0 mt-3 rounded-[10px] border border-accent/20 bg-accent-soft px-3.5 py-2.5 text-footnote font-medium text-accent-text break-words"
        role="status"
      >
        {{ createNotice }}
      </p>
    </UiPanel>

    <!-- 活动任务（SSE 实时进度，断线回退轮询） -->
    <UiPanel v-if="activeItems.length" title="活动任务" kicker="Active Jobs">
      <div class="space-y-3">
        <UziReportProgress
          v-for="item in activeItems"
          :key="item.id"
          :report="item"
          :cancelling="cancellingId === item.id"
          @terminal="handleTerminal"
          @cancel="handleCancel"
        />
      </div>
    </UiPanel>

    <!-- 历史记录 -->
    <UiPanel title="历史报告" kicker="History">
      <UziReportHistory
        :items="items"
        :total="total"
        :page="page"
        :total-pages="totalPages"
        :loading="listLoading"
        :error="listError"
        :can-run="workerReady"
        :deleting-id="deletingId"
        @filter-change="handleFilterChange"
        @page-change="handlePageChange"
        @view="handleView"
        @regenerate="handleRegenerate"
        @delete="handleDelete"
      />
    </UiPanel>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiPanel from '@/components/ui/UiPanel.vue'
import UziReportForm from '@/components/uzi/UziReportForm.vue'
import UziReportHistory from '@/components/uzi/UziReportHistory.vue'
import UziReportProgress from '@/components/uzi/UziReportProgress.vue'
import { useUziReports } from '@/composables/useUziReports'
import type { UziReportStatus, UziReportSummary } from '@/types'

const router = useRouter()
const presetTicker = ref('')
const {
  workerReady,
  unavailableReason,
  creating,
  createError,
  createNotice,
  cancellingId,
  deletingId,
  items,
  total,
  page,
  totalPages,
  listLoading,
  listError,
  activeItems,
  refresh,
  createReport,
  cancelReport,
  deleteReport,
  goToPage,
  applyFilter,
  fetchStatus,
  fetchSourceStatus,
  sourceStatus,
  sourceUpdating,
  sourceUpdateError,
  sourceUpdateNotice,
  updateSource,
  fetchList,
  tickerFilter,
  statusFilter,
} = useUziReports()

async function handleSubmit(ticker: string): Promise<void> {
  const result = await createReport(ticker)
  if (result) {
    // 新任务入队后立即刷新活动任务列表（SSE 由 Progress 组件接管）。
    await refresh()
  }
}

async function handleCancel(reportId: number): Promise<void> {
  await cancelReport(reportId)
  await refresh()
}

function handleTerminal(_status: UziReportStatus): void {
  void refresh()
}

async function handleSourceUpdate(): Promise<void> {
  if (!workerReady.value || sourceUpdating.value) return
  if (!window.confirm('确认检查并更新 UZI 上游源码吗？请先确保没有正在运行的 UZI 任务。')) {
    return
  }
  await updateSource()
}

function handleView(reportId: number): void {
  void router.push(`/uzi-reports/${reportId}`)
}

function handleRegenerate(ticker: string): void {
  presetTicker.value = ticker
  void handleSubmit(ticker)
}

async function handleDelete(item: UziReportSummary): Promise<void> {
  if (!window.confirm(`确定删除报告「${item.ticker_input}」吗？删除后记录与文件将不可恢复。`)) {
    return
  }
  await deleteReport(item.id)
}

function handleFilterChange(ticker: string, status: string): void {
  tickerFilter.value = ticker
  statusFilter.value = status ? (status as UziReportStatus) : ''
  applyFilter()
}

function handlePageChange(targetPage: number): void {
  goToPage(targetPage)
}

onMounted(() => {
  void fetchStatus()
  void fetchSourceStatus()
  void fetchList()
})
</script>
