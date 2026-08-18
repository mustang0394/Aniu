<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader :title="headerTitle" kicker="UZI Report" :description="headerDescription">
      <UiButton variant="ghost" size="sm" @click="router.push('/uzi-reports')">
        ← 返回报告列表
      </UiButton>
    </UiPageHeader>

    <div
      v-if="loadError"
      class="rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text break-all"
      role="alert"
    >
      {{ loadError }}
    </div>

    <template v-if="detail">
      <!-- 状态与时间 -->
      <UiPanel>
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="m-0 text-title-2 font-semibold text-label">
                {{ detail.ticker_input }}
              </h2>
              <template v-if="detail.company_name">
                <span class="text-body text-label-secondary">{{ detail.company_name }}</span>
              </template>
              <span v-if="detail.ticker_normalized" class="text-footnote text-label-tertiary">
                {{ detail.ticker_normalized }}
              </span>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-2">
              <UiBadge :tone="uziStatusTone(detail.status)">{{ uziStatusLabel(detail.status) }}</UiBadge>
              <span v-if="!isTerminal" class="text-footnote text-label-secondary tabular-nums">
                进度 {{ detail.progress }}%
              </span>
              <span v-if="detail.llm_model" class="text-footnote text-label-tertiary">
                模型 {{ detail.llm_model }}
              </span>
              <span v-if="detail.uzi_commit" class="text-footnote text-label-tertiary">
                UZI {{ detail.uzi_commit }}
              </span>
            </div>
          </div>
          <div class="flex shrink-0 flex-wrap items-center gap-2">
            <UiButton
              v-if="!isTerminal"
              variant="danger-soft"
              size="sm"
              :loading="cancelling"
              @click="handleCancel"
            >
              取消任务
            </UiButton>
            <UiButton
              variant="danger-soft"
              size="sm"
              :loading="deleting"
              @click="handleDelete"
            >
              删除报告
            </UiButton>
          </div>
        </div>

        <dl v-if="!isTerminal" class="mt-4 grid grid-cols-1 gap-3 border-t border-separator pt-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt class="m-0 text-footnote font-semibold text-label-tertiary">当前阶段</dt>
            <dd class="m-0 mt-1 text-footnote text-label break-words">
              {{ detail.phase || uziStatusLabel(detail.status) }}
            </dd>
          </div>
          <div>
            <dt class="m-0 text-footnote font-semibold text-label-tertiary">耗时</dt>
            <dd class="m-0 mt-1 text-footnote text-label tabular-nums">
              {{ formatReportDuration(detail.created_at, detail.finished_at) }}
            </dd>
          </div>
          <div class="col-span-1 sm:col-span-2">
            <dt class="m-0 text-footnote font-semibold text-label-tertiary">进度消息</dt>
            <dd class="m-0 mt-1 text-footnote text-label-secondary break-words">
              {{ detail.progress_message || '--' }}
            </dd>
          </div>
        </dl>

        <dl v-else class="mt-4 grid grid-cols-1 gap-3 border-t border-separator pt-4 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt class="m-0 text-footnote font-semibold text-label-tertiary">创建时间</dt>
            <dd class="m-0 mt-1 text-footnote text-label tabular-nums">{{ formatTime(detail.created_at) }}</dd>
          </div>
          <div>
            <dt class="m-0 text-footnote font-semibold text-label-tertiary">完成时间</dt>
            <dd class="m-0 mt-1 text-footnote text-label tabular-nums">
              {{ detail.finished_at ? formatTime(detail.finished_at) : '--' }}
            </dd>
          </div>
          <div>
            <dt class="m-0 text-footnote font-semibold text-label-tertiary">数据时间</dt>
            <dd class="m-0 mt-1 text-footnote text-label tabular-nums">
              {{ detail.summary?.data_as_of ? formatTime(detail.summary.data_as_of) : '--' }}
            </dd>
          </div>
          <div>
            <dt class="m-0 text-footnote font-semibold text-label-tertiary">耗时</dt>
            <dd class="m-0 mt-1 text-footnote text-label tabular-nums">
              {{ formatReportDuration(detail.created_at, detail.finished_at) }}
            </dd>
          </div>
        </dl>

        <div
          v-if="detail.error_code || detail.error_message"
          class="mt-4 rounded-[10px] border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-footnote text-danger-text break-words"
          role="alert"
        >
          <span v-if="detail.error_code" class="font-semibold">{{ detail.error_code }}</span>
          <template v-if="detail.error_code && detail.error_message">：</template>
          <span>{{ detail.error_message || '任务未能完成。' }}</span>
        </div>
      </UiPanel>

      <!-- 摘要（评分/结论/风险/催化剂/分歧） -->
      <UiPanel v-if="isTerminal" title="报告摘要" kicker="Summary">
        <UziReportSummary :summary="detail.summary" />
      </UiPanel>

      <!-- HTML 预览（sandbox iframe 隔离，Blob 生命周期受控） -->
      <UiPanel title="报告预览" kicker="Preview">
        <template #actions>
          <div class="flex flex-wrap items-center gap-2">
            <UiButton
              v-if="hasHtmlArtifact && isTerminal"
              variant="ghost"
              size="sm"
              :disabled="openingTab || htmlPreviewLoading"
              @click="handleOpenInNewTab"
            >
              {{ openingTab ? '打开中…' : '新标签页预览' }}
            </UiButton>
            <UiButton
              v-for="artifact in detail.artifacts"
              :key="artifact.key"
              variant="ghost"
              size="sm"
              :disabled="downloadingKey === artifact.key"
              @click="handleDownload(artifact.key)"
            >
              {{ downloadingKey === artifact.key ? '下载中…' : `下载${UZI_ARTIFACT_LABELS[artifact.key]}` }}
            </UiButton>
          </div>
        </template>

        <div v-if="htmlPreviewLoaded" class="overflow-hidden rounded-[12px] border border-separator bg-white">
          <iframe
            :src="htmlPreviewUrl ?? undefined"
            sandbox="allow-scripts allow-downloads allow-same-origin"
            class="block h-[70vh] min-h-[420px] w-full border-0 bg-white"
            title="UZI 报告预览"
            loading="lazy"
          />
        </div>
        <div v-else-if="htmlPreviewLoading" class="flex items-center justify-center gap-2 py-10 text-footnote text-label-secondary">
          <span class="size-4 rounded-full border-2 border-current border-t-transparent animate-spin-slow" />
          正在加载报告…
        </div>
        <div v-else class="rounded-[10px] border border-dashed border-separator-strong bg-fill/60 px-6 py-8 text-center">
          <p class="m-0 text-body font-medium text-label-secondary">报告内容暂不可预览</p>
          <p v-if="htmlPreviewError" class="m-0 mt-1 text-footnote text-label-tertiary break-words">
            {{ htmlPreviewError }}
          </p>
          <p v-if="!hasHtmlArtifact" class="m-0 mt-1 text-footnote text-label-tertiary">
            报告产物尚未就绪或未包含 HTML 文件。
          </p>
        </div>
      </UiPanel>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiPanel from '@/components/ui/UiPanel.vue'
import UziReportSummary from '@/components/uzi/UziReportSummary.vue'
import { api } from '@/services/api'
import type { UziArtifactKey, UziReportDetail } from '@/types'
import { formatTime } from '@/utils/formatters'
import {
  UZI_ARTIFACT_FILENAMES,
  UZI_ARTIFACT_LABELS,
  formatReportDuration,
  uziStatusLabel,
  uziStatusTone,
} from '@/utils/uzi'

const route = useRoute()
const router = useRouter()

const detail = ref<UziReportDetail | null>(null)
const loadError = ref('')
const cancelling = ref(false)
const deleting = ref(false)
const downloadingKey = ref<UziArtifactKey | null>(null)

// HTML 预览（sandbox iframe + Blob URL 生命周期管理）
const htmlPreviewUrl = ref<string | null>(null)
const htmlPreviewLoading = ref(false)
const htmlPreviewError = ref('')
const htmlPreviewLoaded = ref(false)
const openingTab = ref(false)

const isTerminal = computed(() => {
  const status = detail.value?.status
  return status === 'completed' || status === 'failed' || status === 'cancelled'
})

const hasHtmlArtifact = computed(() =>
  detail.value?.artifacts.some((item) => item.key === 'html') === true,
)

const headerTitle = computed(() => {
  if (!detail.value) return '报告详情'
  return detail.value.company_name || detail.value.ticker_input || `报告 #${detail.value.id}`
})

const headerDescription = computed(() => {
  if (!detail.value) return '查看 UZI 深度报告详情与产物。'
  return `股票代码 ${detail.value.ticker_input}${detail.value.ticker_normalized ? `（${detail.value.ticker_normalized}）` : ''}的深度研究报告。`
})

let pollTimer: number | null = null
let htmlBlobUrl: string | null = null

async function loadDetail(reportId: number): Promise<void> {
  loadError.value = ''
  try {
    const wasNonTerminal = detail.value && !isTerminal.value
    detail.value = await api.getUziReport(reportId)
    if (isTerminal.value) {
      stopPolling()
      // 从运行中变为终态时加载 HTML 预览（阻断项10：此前只停止轮询未加载预览）。
      if (wasNonTerminal && detail.value && detail.value.status === 'completed') {
        void loadHtmlPreview(reportId)
      }
    }
  } catch (err) {
    loadError.value = (err as Error).message || '加载报告详情失败。'
    detail.value = null
  }
}

/** 非终态任务轮询刷新（详情页不强制 SSE，保持简单可靠）。 */
function ensurePolling(reportId: number): void {
  stopPolling()
  if (detail.value && !isTerminal.value) {
    pollTimer = window.setInterval(() => {
      void loadDetail(reportId)
    }, 5000)
  }
}

function stopPolling(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

function revokeHtmlPreview(): void {
  if (htmlBlobUrl !== null) {
    URL.revokeObjectURL(htmlBlobUrl)
    htmlBlobUrl = null
  }
  htmlPreviewUrl.value = null
  htmlPreviewLoaded.value = false
  htmlPreviewLoading.value = false
}

async function loadHtmlPreview(reportId: number): Promise<void> {
  revokeHtmlPreview()
  htmlPreviewError.value = ''
  if (!detail.value || detail.value.status !== 'completed') {
    return
  }
  htmlPreviewLoading.value = true
  try {
    const blob = await api.fetchUziReportArtifactBlob(reportId, 'html')
    htmlBlobUrl = URL.createObjectURL(blob)
    htmlPreviewUrl.value = htmlBlobUrl
    htmlPreviewLoaded.value = true
  } catch (err) {
    htmlPreviewError.value = (err as Error).message || '报告预览加载失败。'
  } finally {
    htmlPreviewLoading.value = false
  }
}

async function handleOpenInNewTab(): Promise<void> {
  if (!detail.value || detail.value.status !== 'completed') return
  openingTab.value = true
  try {
    // 复用已加载的 blob URL；未加载时按需拉取一份独立的（不干扰内嵌预览状态）。
    let url = htmlBlobUrl
    if (url === null) {
      const blob = await api.fetchUziReportArtifactBlob(detail.value.id, 'html')
      url = URL.createObjectURL(blob)
    }
    // 注意：不能传 'noopener' 特性——它会让 window.open 始终返回 null，
    // 导致下面无法区分“成功打开新标签页”和“弹窗被浏览器拦截”，
    // 从而把成功打开误判为被拦截，回退时把当前标签页也带走了。
    // 这里改用手动清除 opener 引用，达到与 noopener 相同的窗口隔离效果。
    const win = window.open(url, '_blank')
    if (win !== null) {
      win.opener = null
    } else {
      // 被浏览器拦截时回退到当前标签页跳转。
      window.location.href = url
    }
  } catch (err) {
    loadError.value = (err as Error).message || '打开报告预览失败。'
  } finally {
    openingTab.value = false
  }
}

async function handleDownload(key: UziArtifactKey): Promise<void> {
  if (!detail.value || downloadingKey.value) return
  const reportId = detail.value.id
  downloadingKey.value = key
  try {
    const blob = await api.fetchUziReportArtifactBlob(reportId, key)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${reportId}-${UZI_ARTIFACT_FILENAMES[key]}`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    loadError.value = (err as Error).message || '下载产物失败。'
  } finally {
    downloadingKey.value = null
  }
}

async function handleCancel(): Promise<void> {
  if (!detail.value || isTerminal.value) return
  cancelling.value = true
  try {
    await api.cancelUziReport(detail.value.id)
    await loadDetail(detail.value.id)
  } catch (err) {
    loadError.value = (err as Error).message || '取消任务失败。'
  } finally {
    cancelling.value = false
  }
}

async function handleDelete(): Promise<void> {
  if (!detail.value) return
  const target = detail.value
  const confirmed = window.confirm(
    `确定删除报告「${target.ticker_input}」吗？删除后记录与文件将不可恢复。`,
  )
  if (!confirmed) return
  deleting.value = true
  try {
    await api.deleteUziReport(target.id)
    void router.replace('/uzi-reports')
  } catch (err) {
    loadError.value = (err as Error).message || '删除报告失败。'
  } finally {
    deleting.value = false
  }
}

function reportIdFromRoute(): number | null {
  const raw = route.params.reportId
  const value = Array.isArray(raw) ? raw[0] : raw
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : null
}

watch(
  () => route.params.reportId,
  async (raw) => {
    const id = reportIdFromRoute()
    if (id == null) {
      loadError.value = '无效的报告 ID。'
      return
    }
    stopPolling()
    revokeHtmlPreview()
    detail.value = null
    await loadDetail(id)
    ensurePolling(id)
    await loadHtmlPreview(id)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopPolling()
  revokeHtmlPreview()
})
</script>