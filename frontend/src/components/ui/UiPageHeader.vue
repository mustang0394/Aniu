<template>
  <div class="mb-5 flex flex-wrap items-end justify-between gap-3 sm:mb-6">
    <div class="min-w-0">
      <p v-if="kicker" class="mb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-label-quaternary">
        {{ kicker }}
      </p>
      <h1 class="m-0 text-title-1 font-semibold tracking-tight text-label">
        {{ title }}
      </h1>
      <p v-if="description" class="mt-1.5 max-w-2xl text-body text-label-secondary">
        {{ description }}
      </p>
    </div>
    <div v-if="$slots.default" class="flex flex-wrap items-center gap-2">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  kicker?: string
  description?: string
}>()
</script>
