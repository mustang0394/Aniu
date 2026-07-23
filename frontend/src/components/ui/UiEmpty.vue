<template>
  <div
    class="flex flex-col items-center justify-center gap-2 rounded-[16px] border border-dashed border-separator-strong bg-fill/60 px-6 py-12 text-center"
  >
    <p class="m-0 text-body font-medium text-label-secondary">{{ title }}</p>
    <p v-if="description" class="m-0 max-w-sm text-footnote text-label-tertiary">
      {{ description }}
    </p>
    <div v-if="$slots.default" class="mt-3">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>
