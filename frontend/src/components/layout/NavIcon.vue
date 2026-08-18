<template>
  <!--
    24×24 Lucide-style strokes, optically centered in viewBox.
    Always use display:block — inline SVG has a baseline gap that makes
    icons look 1–2px low inside flex/grid badges.
  -->
  <svg
    class="block size-[18px] shrink-0"
    viewBox="0 0 24 24"
    width="18"
    height="18"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <!-- overview: 2×2 dashboard tiles, symmetric in 24 box -->
    <g v-if="name === 'overview'">
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </g>

    <!-- tasks: clipboard, body centered -->
    <g v-else-if="name === 'tasks'">
      <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2" />
      <rect x="9" y="3" width="6" height="4" rx="1" />
      <path d="M9 12h6" />
      <path d="M9 16h4" />
    </g>

    <!-- uzi: report document with search lens (文件 + 放大镜) -->
    <g v-else-if="name === 'uzi'">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
      <path d="M14 2v6h6" />
      <circle cx="11.6" cy="14.6" r="2.6" />
      <path d="m13.6 16.6 2.6 2.6" />
    </g>

    <!-- chat: rounded bubble, geometric center ≈ (12, 11.5) -->
    <g v-else-if="name === 'chat'">
      <path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" />
    </g>

    <!-- schedule: calendar, hooks balanced -->
    <g v-else-if="name === 'schedule'">
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
    </g>

    <!-- accounts: 多账户卡片/钱包 -->
    <g v-else-if="name === 'accounts'">
      <rect x="2.5" y="6" width="19" height="13" rx="2.5" />
      <path d="M2.5 10.5h19" />
      <path d="M6.5 15.5h4" />
      <circle cx="18" cy="14" r="1.2" fill="currentColor" stroke="none" />
    </g>

    <!-- settings: cog, center circle + even teeth -->
    <g v-else-if="name === 'settings'">
      <path
        d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z"
      />
      <path
        d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
      />
    </g>
  </svg>
</template>

<script setup lang="ts">
import type { AppNavIcon } from '@/config/navigation'

defineProps<{ name: AppNavIcon }>()
</script>
