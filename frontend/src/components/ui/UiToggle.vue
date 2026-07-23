<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled"
    :class="classes"
    @click="emit('update:modelValue', !modelValue)"
  >
    <span
      class="pointer-events-none absolute top-0.5 size-6 rounded-full bg-white shadow-sm transition-transform duration-200"
      :class="modelValue ? 'translate-x-[22px]' : 'translate-x-0.5'"
      aria-hidden="true"
    />
    <span class="sr-only">{{ modelValue ? onLabel : offLabel }}</span>
    <span
      class="relative z-[1] pl-[30px] pr-2 text-[11px] font-semibold transition-colors"
      :class="modelValue ? 'text-on-accent' : 'text-label-tertiary pl-2 pr-[30px]'"
    >
      {{ modelValue ? onLabel : offLabel }}
    </span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/utils/cn'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
    onLabel?: string
    offLabel?: string
  }>(),
  {
    disabled: false,
    onLabel: '启用',
    offLabel: '停用',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const classes = computed(() =>
  cn(
    'relative inline-flex h-7 min-w-[56px] items-center rounded-pill transition-colors duration-200',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring focus-visible:ring-offset-2',
    'disabled:opacity-45 disabled:pointer-events-none',
    props.modelValue ? 'bg-accent' : 'bg-fill-tertiary',
  ),
)
</script>
