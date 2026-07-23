<template>
  <section :class="classes">
    <header
      v-if="title || $slots.actions || $slots.header"
      class="mb-5 flex flex-wrap items-start justify-between gap-3"
    >
      <slot name="header">
        <div class="min-w-0">
          <h2 v-if="title" class="m-0 text-title-2 font-semibold tracking-tight text-label">
            {{ title }}
          </h2>
          <p v-if="kicker" class="mt-1 text-footnote font-medium text-label-tertiary">
            {{ kicker }}
          </p>
        </div>
      </slot>
      <div v-if="$slots.actions" class="flex flex-wrap items-center gap-2 shrink-0">
        <slot name="actions" />
      </div>
    </header>
    <slot />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/utils/cn'

const props = withDefaults(
  defineProps<{
    title?: string
    kicker?: string
    padding?: boolean
    solid?: boolean
  }>(),
  {
    padding: true,
    solid: false,
  },
)

const classes = computed(() =>
  cn(
    'rounded-xl border border-separator shadow-sm',
    props.solid ? 'bg-card-solid' : 'glass-card',
    props.padding && 'p-5 sm:p-6',
  ),
)
</script>
