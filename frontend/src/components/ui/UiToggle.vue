<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :aria-label="ariaLabel || (modelValue ? onLabel : offLabel)"
    :disabled="disabled"
    :class="trackClasses"
    @click="handleClick"
  >
    <!-- Sliding thumb -->
    <span
      class="pointer-events-none absolute top-0.5 left-0.5 size-6 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.22),0_1px_1px_rgba(0,0,0,0.08)] transition-transform duration-200 ease-out will-change-transform"
      :class="modelValue ? 'translate-x-5' : 'translate-x-0'"
      aria-hidden="true"
    />

    <!-- Optional in-track labels (wider variant) -->
    <template v-if="showLabels">
      <span
        class="relative z-[1] w-full select-none px-2 text-center text-[11px] font-semibold leading-none transition-colors duration-200"
        :class="modelValue ? 'pl-1.5 pr-6 text-on-accent' : 'pl-6 pr-1.5 text-label-secondary'"
      >
        {{ modelValue ? onLabel : offLabel }}
      </span>
    </template>
    <span v-else class="sr-only">{{ modelValue ? onLabel : offLabel }}</span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/utils/cn'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
    /** Show 启用/停用 text inside the track (wider control). Default: pure iOS switch. */
    showLabels?: boolean
    onLabel?: string
    offLabel?: string
    ariaLabel?: string
  }>(),
  {
    disabled: false,
    showLabels: false,
    onLabel: '启用',
    offLabel: '停用',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

function handleClick() {
  if (props.disabled) return
  emit('update:modelValue', !props.modelValue)
}

/**
 * Geometry (iOS-like):
 * - track:  h-7 (28px)
 * - thumb:  size-6 (24px), inset 2px (top-0.5 left-0.5)
 * - travel: 20px → translate-x-5
 * - width:  pure = 48px (12*4); labeled = min 72px
 */
const trackClasses = computed(() =>
  cn(
    'relative inline-flex shrink-0 items-center overflow-hidden rounded-full transition-colors duration-200',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-ring focus-visible:ring-offset-2',
    'disabled:cursor-not-allowed disabled:opacity-45',
    'h-7',
    props.showLabels ? 'min-w-[4.5rem] px-0' : 'w-12',
    props.modelValue
      ? 'bg-accent'
      : 'bg-fill-tertiary ring-1 ring-inset ring-black/5',
  ),
)
</script>
