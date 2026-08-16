<template>
  <div v-if="summary" class="space-y-5">
    <!-- 评分与结论 -->
    <div class="flex flex-wrap items-start gap-4">
      <div class="min-w-[120px]">
        <p class="m-0 text-footnote font-semibold text-label-tertiary">综合评分</p>
        <p class="m-0 mt-1 text-large-number font-bold tabular-nums" :class="scoreClass">
          {{ summary.overall_score != null ? summary.overall_score.toFixed(1) : '--' }}
        </p>
      </div>
      <div class="min-w-0 flex-1">
        <div class="flex flex-wrap items-center gap-2">
          <UiBadge tone="accent">{{ summary.verdict || '未给出结论' }}</UiBadge>
          <UiBadge tone="neutral">
            {{ summary.valuation?.rating || '估值未评级' }}
          </UiBadge>
        </div>
        <p v-if="summary.one_liner" class="m-0 mt-2 text-callout text-label break-words">
          {{ summary.one_liner }}
        </p>
      </div>
    </div>

    <!-- 估值 / 数据覆盖 -->
    <dl class="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-3">
      <div>
        <dt class="m-0 text-footnote font-semibold text-label-tertiary">目标价</dt>
        <dd class="m-0 mt-1 text-callout font-semibold tabular-nums text-label">
          {{ summary.valuation?.target_price ? summary.valuation.target_price.toFixed(2) : '--' }}
        </dd>
      </div>
      <div>
        <dt class="m-0 text-footnote font-semibold text-label-tertiary">上涨空间</dt>
        <dd class="m-0 mt-1 text-callout font-semibold tabular-nums text-label">
          {{
            summary.valuation?.upside_pct != null
              ? `${summary.valuation.upside_pct > 0 ? '+' : ''}${summary.valuation.upside_pct.toFixed(1)}%`
              : '--'
          }}
        </dd>
      </div>
      <div>
        <dt class="m-0 text-footnote font-semibold text-label-tertiary">数据覆盖率</dt>
        <dd class="m-0 mt-1 text-callout font-semibold tabular-nums text-label">
          {{ summary.data_gaps?.coverage_pct != null ? `${summary.data_gaps.coverage_pct.toFixed(0)}%` : '--' }}
          <span v-if="summary.data_gaps?.unresolved" class="ml-2 text-footnote text-warning-text">
            未解决缺口 {{ summary.data_gaps.unresolved }}
          </span>
        </dd>
      </div>
    </dl>

    <!-- 风险与催化剂 -->
    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <section>
        <h3 class="m-0 mb-2 text-footnote font-semibold text-label-tertiary">主要风险</h3>
        <ul v-if="summary.risks?.length" class="m-0 list-none space-y-1.5 p-0">
          <li
            v-for="(risk, index) in summary.risks"
            :key="index"
            class="flex items-start gap-2 text-footnote text-label-secondary break-words"
          >
            <span class="mt-1.5 inline-block size-1.5 shrink-0 rounded-full bg-danger" />
            <span>{{ risk }}</span>
          </li>
        </ul>
        <p v-else class="m-0 text-footnote text-label-tertiary">暂无风险条目。</p>
      </section>
      <section>
        <h3 class="m-0 mb-2 text-footnote font-semibold text-label-tertiary">催化剂</h3>
        <ul v-if="summary.catalysts?.length" class="m-0 list-none space-y-1.5 p-0">
          <li
            v-for="(catalyst, index) in summary.catalysts"
            :key="index"
            class="flex items-start gap-2 text-footnote text-label-secondary break-words"
          >
            <span class="mt-1.5 inline-block size-1.5 shrink-0 rounded-full bg-accent" />
            <span>{{ catalyst }}</span>
          </li>
        </ul>
        <p v-else class="m-0 text-footnote text-label-tertiary">暂无催化剂条目。</p>
      </section>
    </div>

    <!-- 投资者分歧 -->
    <section>
      <h3 class="m-0 mb-2 text-footnote font-semibold text-label-tertiary">投资者分歧面板</h3>
      <div class="flex flex-wrap items-center gap-2">
        <UiBadge tone="success">看多 {{ summary.panel?.bullish ?? 0 }}</UiBadge>
        <UiBadge tone="neutral">中性 {{ summary.panel?.neutral ?? 0 }}</UiBadge>
        <UiBadge tone="danger">看空 {{ summary.panel?.bearish ?? 0 }}</UiBadge>
      </div>
      <ul v-if="summary.panel?.key_disagreements?.length" class="m-0 mt-2 list-none space-y-1.5 p-0">
        <li
          v-for="(disagreement, index) in summary.panel.key_disagreements"
          :key="index"
          class="flex items-start gap-2 text-footnote text-label-secondary break-words"
        >
          <span class="mt-1.5 inline-block size-1.5 shrink-0 rounded-full bg-warning" />
          <span>{{ disagreement }}</span>
        </li>
      </ul>
    </section>

    <!-- 数据缺口与来源 -->
    <section v-if="summary.data_gaps?.items?.length">
      <h3 class="m-0 mb-2 text-footnote font-semibold text-label-tertiary">数据缺口</h3>
      <ul class="m-0 list-none space-y-1.5 p-0">
        <li
          v-for="(gap, index) in summary.data_gaps.items"
          :key="index"
          class="flex items-start gap-2 text-footnote text-label-secondary break-words"
        >
          <span class="mt-1.5 inline-block size-1.5 shrink-0 rounded-full bg-separator-strong" />
          <span>{{ gap }}</span>
        </li>
      </ul>
    </section>

    <section v-if="summary.sources?.length">
      <h3 class="m-0 mb-2 text-footnote font-semibold text-label-tertiary">信息来源（{{ summary.sources.length }}）</h3>
      <ul class="m-0 flex list-none flex-wrap gap-1.5 p-0">
        <li v-for="(source, index) in summary.sources" :key="index">
          <UiBadge tone="neutral">{{ source }}</UiBadge>
        </li>
      </ul>
    </section>

    <p class="m-0 border-t border-separator pt-3 text-footnote text-label-tertiary">
      {{ summary.disclaimer || '历史研究资料，不构成投资建议。' }}
    </p>
  </div>

  <UiEmpty
    v-else
    title="暂无报告摘要"
    description="报告完成或数据可用后，这里会展示评分解读与风险提示。"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'

import UiBadge from '@/components/ui/UiBadge.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import type { UziSummaryPayload } from '@/types'

const props = defineProps<{
  summary: UziSummaryPayload | null
}>()

const summary = computed(() => props.summary)

const scoreClass = computed(() => {
  const score = props.summary?.overall_score
  if (score == null) return 'text-label-tertiary'
  if (score >= 70) return 'text-success-text'
  if (score >= 50) return 'text-warning-text'
  return 'text-danger-text'
})
</script>