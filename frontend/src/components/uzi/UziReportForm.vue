<template>
  <div class="flex flex-col gap-3 sm:flex-row sm:items-start">
    <UiField
      label="股票代码或名称"
      help="支持 A 股代码（如 600519.SH）、名称（如 贵州茅台）；仅支持完整深度分析。"
      :error="error"
      class="min-w-0 flex-1"
    >
      <input
        v-model.trim="ticker"
        type="text"
        maxlength="64"
        autocomplete="off"
        placeholder="例如 600519.SH 或 贵州茅台"
        class="h-11 w-full rounded-[12px] border border-separator-strong bg-card-solid px-4 text-body text-label outline-none transition-colors placeholder:text-label-tertiary focus:border-accent focus:ring-2 focus:ring-accent-ring"
        :disabled="disabled || submitting"
        @keyup.enter="handleSubmit"
      />
    </UiField>
    <UiButton
      variant="primary"
      size="lg"
      class="mt-1 sm:mt-6"
      :loading="submitting"
      :disabled="disabled || submitting || !ticker"
      @click="handleSubmit"
    >
      生成深度报告
    </UiButton>
  </div>

  <div class="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
    <UiBadge v-if="disabled" tone="warning">UZI 当前不可用</UiBadge>
    <span v-if="disabledReason" class="text-footnote text-label-tertiary break-all">
      {{ disabledReason }}
    </span>
    <span v-else class="text-footnote text-label-tertiary">
      报告由 UZI 引擎生成，通常需要数分钟；进度将在下方实时展示。
    </span>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'

import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiField from '@/components/ui/UiField.vue'

const props = withDefaults(
  defineProps<{
    disabled?: boolean
    disabledReason?: string | null
    submitting?: boolean
    error?: string
    /** 历史条目点「重新生成」时预填的股票代码/名称。 */
    presetTicker?: string
  }>(),
  {
    disabled: false,
    disabledReason: null,
    submitting: false,
    error: '',
    presetTicker: '',
  },
)

const emit = defineEmits<{
  submit: [ticker: string]
}>()

const ticker = ref('')

watch(
  () => props.presetTicker,
  (value) => {
    if (value && value.trim()) {
      ticker.value = value.trim()
    }
  },
  { immediate: true },
)

function handleSubmit(): void {
  const value = ticker.value.trim()
  if (!value || props.disabled || props.submitting) return
  emit('submit', value)
}
</script>