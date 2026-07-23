<template>
  <component
    :is="tag"
    :type="tag === 'button' ? type : undefined"
    :disabled="tag === 'button' ? disabled || loading : undefined"
    :href="tag === 'a' ? href : undefined"
    :class="classes"
    v-bind="$attrs"
  >
    <span
      v-if="loading"
      class="size-3.5 shrink-0 rounded-full border-2 border-current border-t-transparent animate-spin-slow"
      aria-hidden="true"
    />
    <slot />
  </component>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/utils/cn'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'tinted' | 'ghost' | 'danger' | 'danger-soft' | 'plain'
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    disabled?: boolean
    type?: 'button' | 'submit' | 'reset'
    href?: string
    block?: boolean
  }>(),
  {
    variant: 'primary',
    size: 'md',
    loading: false,
    disabled: false,
    type: 'button',
    block: false,
  },
)

const tag = computed(() => (props.href ? 'a' : 'button'))

const classes = computed(() =>
  cn(
    'inline-flex items-center justify-center gap-2 font-semibold transition-all duration-150 select-none',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring focus-visible:ring-offset-2',
    'disabled:opacity-45 disabled:pointer-events-none',
    props.block && 'w-full',
    // size
    props.size === 'sm' && 'h-8 px-3 text-footnote rounded-[10px]',
    props.size === 'md' && 'h-10 px-4 text-body rounded-[12px]',
    props.size === 'lg' && 'h-12 px-5 text-callout rounded-[14px]',
    // variant
    props.variant === 'primary' &&
      'bg-accent text-on-accent shadow-sm hover:bg-accent-hover active:bg-accent-active active:scale-[0.98]',
    props.variant === 'tinted' &&
      'bg-accent-soft text-accent-text border border-accent/15 hover:bg-accent/15 active:scale-[0.98]',
    props.variant === 'ghost' &&
      'bg-transparent text-label-secondary border border-separator-strong hover:bg-hover hover:text-label active:scale-[0.98]',
    props.variant === 'danger' &&
      'bg-danger text-on-accent shadow-sm hover:bg-danger-text active:scale-[0.98]',
    props.variant === 'danger-soft' &&
      'bg-danger-soft text-danger-text border border-danger/20 hover:bg-danger/15 active:scale-[0.98]',
    props.variant === 'plain' &&
      'bg-transparent text-accent-text hover:bg-accent-soft active:scale-[0.98]',
  ),
)
</script>
