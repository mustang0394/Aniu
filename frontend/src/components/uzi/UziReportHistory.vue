<template>
  <div>
    <!-- 筛选：搜索 + 状态 + 刷新 -->
    <div class="mb-4 flex flex-wrap items-center gap-2">
      <div class="relative min-w-0 flex-1 sm:max-w-xs">
        <input
          v-model.trim="searchText"
          type="text"
          maxlength="64"
          placeholder="搜索股票代码、名称或公司名"
          class="h-9 w-full rounded-[10px] border border-separator-strong bg-card-solid px-3.5 pr-16 text-footnote text-label outline-none transition-colors placeholder:text-label-tertiary focus:border-accent focus:ring-2 focus:ring-accent-ring"
          @keyup.enter="applyFilter"
        />
        <button
          type="button"
          class="absolute inset-y-0 right-0 flex items-center px-3 text-footnote font-semibold text-accent-text hover:text-label"
          @click="applyFilter"
        >
          搜索
        </button>
      </div>
      <select
        v-model="selectedStatus"
        class="h-9 rounded-[10px] border border-separator-strong bg-card-solid px-3 text-foot font-semibold text-label outline-none focus:border-accent focus:ring-2 focus:ring-accent-ring"
        @change="applyFilter"
      >
        <option value="">全部状态</option>
        <option v-for="option in statusOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
      <UiButton variant="ghost" size="sm" :loading="loading" @click="applyFilter">
        刷新
      </UiButton>
    </div>

    <!-- 错误提示 -->
    <div
      v-if="error"
      class="mb-4 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text break-all"
      role="alert"
    >
      {{ error }}
    </div>

    <!-- 桌面表格 -->
    <div v-if="items.length" class="hidden overflow-x-auto md:block">
      <table class="w-full min-w-[640px] border-collapse text-left">
        <thead>
          <tr class="border-b border-separator text-footnote font-semibold text-label-tertiary">
            <th class="py-2.5 pr-3 font-semibold">股票 / 公司</th>
            <th class="py-2.5 pr-3 font-semibold">评分 / 结论</th>
            <th class="py-2.5 pr-3 font-semibold">状态 / 进度</th>
            <th class="py-2.5 pr-3 font-semibold">生成时间</th>
            <th class="py-2.5 pr-3 font-semibold">模型</th>
            <th class="py-2.5 font-semibold text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in items"
            :key="item.id"
            class="border-b border-separator/60 align-middle last:border-0"
          >
            <td class="max-w-[180px] py-3 pr-4">
              <button
                type="button"
                class="block min-w-0 text-left"
                @click="$emit('view', item.id)"
              >
                <span class="block truncate text-body font-semibold text-label hover:text-accent-text">
                  {{ item.ticker_input }}
                </span>
                <span v-if="item.company_name" class="block truncate text-footnote text-label-tertiary">
                  {{ item.company_name }}
                  <template v-if="item.ticker_normalized">（{{ item.ticker_normalized }}）</template>
                </span>
                <span v-else-if="item.ticker_normalized" class="block truncate text-footnote text-label-tertiary">
                  {{ item.ticker_normalized }}
                </span>
              </button>
            </td>
            <td class="max-w-[180px] py-3 pr-4">
              <template v-if="item.overall_score != null || item.verdict">
                <span class="text-body font-bold tabular-nums">
                  {{ item.overall_score != null ? item.overall_score.toFixed(1) : '--' }}
                </span>
                <span v-if="item.verdict" class="ml-1.5 text-footnote text-label-secondary break-words">
                  {{ item.verdict }}
                </span>
              </template>
              <span v-else class="text-footnote text-label-tertiary">--</span>
            </td>
            <td class="py-3 pr-4">
              <div class="flex items-center gap-2">
                <UiBadge :tone="uziStatusTone(item.status)">
                  {{ uziStatusLabel(item.status) }}
                </UiBadge>
                <span class="text-footnote text-label-tertiary tabular-nums">
                  {{ item.progress }}%
                </span>
              </div>
              <span v-if="item.finished_at" class="mt-1 block text-footnote text-label-tertiary">
                耗时 {{ formatReportDuration(item.created_at, item.finished_at) }}
              </span>
            </td>
            <td class="py-3 pr-4 text-footnote text-label-secondary tabular-nums whitespace-nowrap">
              {{ formatTime(item.created_at) }}
            </td>
            <td class="max-w-[140px] py-3 pr-4">
              <span class="block truncate text-footnote text-label-secondary" :title="item.llm_model ?? undefined">
                {{ item.llm_model || '--' }}
              </span>
            </td>
            <td class="py-3 text-right">
              <div class="flex items-center justify-end gap-1.5">
                <UiButton variant="ghost" size="sm" @click="$emit('view', item.id)">
                  查看
                </UiButton>
                <UiButton
                  variant="tinted"
                  size="sm"
                  :disabled="!canRun"
                  :title="canRun ? undefined : 'Worker 或 LLM 不可用'"
                  @click="$emit('regenerate', item.ticker_input)"
                >
                  重新生成
                </UiButton>
                <UiButton
                  variant="danger-soft"
                  size="sm"
                  :loading="deletingId === item.id"
                  @click="$emit('delete', item)"
                >
                  删除
                </UiButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 移动端纵向条目 -->
    <div v-if="items.length" class="space-y-3 md:hidden">
      <article
        v-for="item in items"
        :key="item.id"
        class="rounded-xl border border-separator bg-card-solid p-4 shadow-sm"
      >
        <div class="flex items-start justify-between gap-2">
          <button type="button" class="min-w-0 text-left" @click="$emit('view', item.id)">
            <p class="m-0 truncate text-callout font-semibold text-label">
              {{ item.ticker_input }}
            </p>
            <p v-if="item.company_name" class="m-0 mt-0.5 truncate text-footnote text-label-tertiary">
              {{ item.company_name }}<template v-if="item.ticker_normalized">（{{ item.ticker_normalized }}）</template>
            </p>
            <p v-else-if="item.ticker_normalized" class="m-0 mt-0.5 truncate text-footnote text-label-tertiary">
              {{ item.ticker_normalized }}
            </p>
          </button>
          <div class="flex shrink-0 items-center gap-2">
            <UiBadge :tone="uziStatusTone(item.status)">
              {{ uziStatusLabel(item.status) }}
            </UiBadge>
            <span class="text-footnote text-label-tertiary tabular-nums">{{ item.progress }}%</span>
          </div>
        </div>

        <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-footnote text-label-secondary">
          <span v-if="item.overall_score != null">
            评分 <strong class="tabular-nums text-label">{{ item.overall_score.toFixed(1) }}</strong>
          </span>
          <span v-if="item.verdict">{{ item.verdict }}</span>
          <span class="tabular-nums whitespace-nowrap">{{ formatTime(item.created_at) }}</span>
          <span v-if="item.finished_at" class="tabular-nums">
            耗时 {{ formatReportDuration(item.created_at, item.finished_at) }}
          </span>
        </div>
        <p v-if="item.llm_model" class="m-0 mt-1 truncate text-footnote text-label-tertiary">
          模型：{{ item.llm_model }}
        </p>

        <div class="mt-2.5 flex flex-wrap items-center gap-1.5">
          <UiButton variant="ghost" size="sm" class="min-h-[32px]" @click="$emit('view', item.id)">
            查看
          </UiButton>
          <UiButton
            variant="tinted"
            size="sm"
            class="min-h-[32px]"
            :disabled="!canRun"
            @click="$emit('regenerate', item.ticker_input)"
          >
            重新生成
          </UiButton>
          <UiButton
            variant="danger-soft"
            size="sm"
            class="min-h-[32px]"
            :loading="deletingId === item.id"
            @click="$emit('delete', item)"
          >
            删除
          </UiButton>
        </div>
      </article>
    </div>

    <UiEmpty
      v-else-if="!loading"
      title="暂无报告记录"
      description="提交股票代码或名称后，生成的深度报告会出现在这里。"
    />

    <!-- 分页 -->
    <div v-if="items.length" class="mt-4 flex flex-wrap items-center justify-between gap-2">
      <span class="text-footnote text-label-tertiary tabular-nums">
        共 {{ total }} 条，第 {{ page }} / {{ totalPages }} 页
      </span>
      <div class="flex items-center gap-2">
        <UiButton variant="ghost" size="sm" :disabled="page <= 1" @click="$emit('page-change', page - 1)">
          上一页
        </UiButton>
        <UiButton
          variant="ghost"
          size="sm"
          :disabled="page >= totalPages"
          @click="$emit('page-change', page + 1)"
        >
          下一页
        </UiButton>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import type { UziReportStatus, UziReportSummary } from '@/types'
import { formatTime } from '@/utils/formatters'
import { formatReportDuration, uziStatusLabel, uziStatusTone } from '@/utils/uzi'

const props = withDefaults(
  defineProps<{
    items: UziReportSummary[]
    total: number
    page: number
    totalPages: number
    loading: boolean
    error?: string
    /** Worker / LLM 不可用时应禁用「重新生成」。 */
    canRun: boolean
    deletingId?: number | null
  }>(),
  {
    error: '',
    deletingId: null,
  },
)

const emit = defineEmits<{
  'filter-change': [ticker: string, status: string]
  'page-change': [page: number]
  view: [reportId: number]
  regenerate: [ticker: string]
  delete: [item: UziReportSummary]
}>()

const searchText = ref('')
const selectedStatus = ref('')

const statusOptions: Array<{ value: string; label: string }> = [
  { value: 'queued', label: '已入队' },
  { value: 'stage1_running', label: '数据采集' },
  { value: 'llm_review', label: '深度评审' },
  { value: 'stage2_running', label: '报告渲染' },
  { value: 'completed', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]

function applyFilter(): void {
  emit('filter-change', searchText.value, selectedStatus.value)
}
</script>