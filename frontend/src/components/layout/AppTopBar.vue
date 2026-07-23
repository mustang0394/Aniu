<template>
  <header
    class="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-separator px-4 glass-nav lg:hidden"
  >
    <button
      type="button"
      class="inline-flex size-11 items-center justify-center rounded-[12px] border border-separator-strong bg-fill text-label-secondary transition-colors hover:bg-hover hover:text-label"
      aria-label="打开导航菜单"
      @click="emit('open-menu')"
    >
      <svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M4 7h16M4 12h16M4 17h16" />
      </svg>
    </button>

    <div class="flex min-w-0 flex-1 items-center gap-2">
      <img src="/aniu.ico" alt="" class="size-7 rounded-[8px] shadow-sm" />
      <div class="min-w-0">
        <p class="truncate text-title-3 font-semibold text-label leading-tight">{{ title }}</p>
      </div>
    </div>

    <button
      type="button"
      class="inline-flex h-10 items-center rounded-full border border-danger/25 bg-danger-soft px-3.5 text-footnote font-semibold text-danger-text transition-colors hover:bg-danger/15"
      @click="emit('logout')"
    >
      退出
    </button>
  </header>
</template>

<script setup lang="ts">
defineProps<{ title: string }>()

const emit = defineEmits<{
  'open-menu': []
  logout: []
}>()
</script>
