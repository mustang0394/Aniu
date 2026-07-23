<template>
  <article
    class="relative overflow-hidden rounded-lg border p-5 shadow-sm transition-transform duration-150 hover:-translate-y-px"
    :class="toneClasses.shell"
  >
    <div
      class="absolute inset-x-0 top-0 h-[3px] rounded-t-lg"
      :class="toneClasses.bar"
      aria-hidden="true"
    />
    <span
      class="inline-flex items-center rounded-pill px-2.5 py-0.5 text-[11px] font-semibold"
      :class="toneClasses.badge"
    >
      {{ label }}
    </span>
    <strong class="mt-3 block text-large-number font-semibold tracking-tight tabular-nums" :class="valueClass">
      <slot name="value">{{ value }}</slot>
    </strong>
    <p class="mt-1 mb-0 text-footnote text-label-secondary">{{ description }}</p>
    <dl v-if="$slots.default || rows?.length" class="mt-4 space-y-2 border-t border-black/5 pt-3">
      <slot>
        <div
          v-for="row in rows"
          :key="row.label"
          class="flex items-center justify-between gap-3 text-footnote"
        >
          <dt class="m-0 text-label-tertiary">{{ row.label }}</dt>
          <dd class="m-0 font-semibold tabular-nums text-label" :class="row.valueClass">
            {{ row.value }}
          </dd>
        </div>
      </slot>
    </dl>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'

export interface StatRow {
  label: string
  value: string
  valueClass?: string
}

const props = withDefaults(
  defineProps<{
    label: string
    value?: string
    description?: string
    tone?: 'status' | 'capital' | 'position' | 'cumulative' | 'daily' | 'accent'
    valueClass?: string
    rows?: StatRow[]
  }>(),
  {
    tone: 'accent',
    description: '',
  },
)

const toneMap = {
  status: {
    shell: 'border-indigo/20 bg-gradient-to-br from-indigo-soft/90 to-white',
    bar: 'bg-indigo',
    badge: 'bg-indigo-soft text-indigo-text',
  },
  capital: {
    shell: 'border-accent/20 bg-gradient-to-br from-accent-soft/90 to-white',
    bar: 'bg-accent',
    badge: 'bg-accent-soft text-accent-text',
  },
  position: {
    shell: 'border-teal/25 bg-gradient-to-br from-teal-soft/90 to-white',
    bar: 'bg-teal',
    badge: 'bg-teal-soft text-teal-text',
  },
  cumulative: {
    shell: 'border-pink/20 bg-gradient-to-br from-pink-soft/90 to-white',
    bar: 'bg-pink',
    badge: 'bg-pink-soft text-pink-text',
  },
  daily: {
    shell: 'border-warning/25 bg-gradient-to-br from-warning-soft/90 to-white',
    bar: 'bg-warning',
    badge: 'bg-warning-soft text-warning-text',
  },
  accent: {
    shell: 'border-accent/20 bg-gradient-to-br from-accent-soft/90 to-white',
    bar: 'bg-accent',
    badge: 'bg-accent-soft text-accent-text',
  },
} as const

const toneClasses = computed(() => toneMap[props.tone])
</script>
