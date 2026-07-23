<template>
  <label class="flex flex-col gap-1.5">
    <span v-if="label" class="text-footnote font-semibold text-label">{{ label }}</span>
    <slot />
    <p v-if="help" class="m-0 text-caption text-label-tertiary leading-snug">{{ help }}</p>
    <p v-if="error" class="m-0 text-caption font-medium text-danger-text">{{ error }}</p>
  </label>
</template>

<script setup lang="ts">
defineProps<{
  label?: string
  help?: string
  error?: string
}>()
</script>
