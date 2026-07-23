<template>
  <span :class="classes">
    <slot />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/utils/cn'

const props = withDefaults(
  defineProps<{
    tone?: 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'trade' | 'analysis'
  }>(),
  { tone: 'neutral' },
)

const classes = computed(() =>
  cn(
    'inline-flex items-center gap-1 rounded-pill px-2.5 py-0.5 text-[11px] font-semibold tracking-wide',
    props.tone === 'neutral' && 'bg-fill-secondary text-label-secondary',
    props.tone === 'accent' && 'bg-accent-soft text-accent-text',
    props.tone === 'success' && 'bg-success-soft text-success-text',
    props.tone === 'warning' && 'bg-warning-soft text-warning-text',
    props.tone === 'danger' && 'bg-danger-soft text-danger-text',
    props.tone === 'trade' && 'bg-pink-soft text-pink-text',
    props.tone === 'analysis' && 'bg-indigo-soft text-indigo-text',
  ),
)
</script>
