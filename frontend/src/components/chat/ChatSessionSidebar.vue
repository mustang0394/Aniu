<template>
  <aside
    class="flex h-full min-h-0 flex-col gap-2.5 overflow-hidden rounded-[16px] border border-separator p-3 shadow-sm glass-card"
  >
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="inline-flex size-9 shrink-0 items-center justify-center rounded-[10px] border border-accent/20 bg-accent-soft text-accent-text transition-colors hover:bg-accent/15 disabled:opacity-45"
        :disabled="creating"
        title="新建对话"
        @click="handleNew"
      >
        <svg class="size-[18px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
          <path d="M12 7v10M7 12h10" />
        </svg>
      </button>
      <input
        v-model="query"
        type="search"
        placeholder="搜索对话"
        class="h-9 min-w-0 flex-1 rounded-[10px] border border-separator-strong bg-fill px-3 text-footnote text-label outline-none placeholder:text-label-quaternary focus:border-accent focus:bg-accent-soft/40 focus:ring-2 focus:ring-accent-ring"
      />
    </div>

    <div
      v-if="loading && !sessions.length && !persistentSession"
      class="px-2 py-8 text-center text-footnote text-label-tertiary"
    >
      加载中…
    </div>

    <div class="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto pr-0.5">
      <section>
        <h4 class="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-label-tertiary">
          持久会话
        </h4>
        <ul class="m-0 list-none space-y-1 p-0">
          <li
            class="flex cursor-pointer items-center gap-2 rounded-[10px] border border-dashed border-separator-strong px-2.5 py-2 transition-colors hover:bg-accent-soft"
            :class="persistentSelected ? 'border-solid border-accent/30 bg-accent-soft' : ''"
            @click="$emit('selectPersistent')"
          >
            <div class="min-w-0 flex-1">
              <span class="block truncate text-footnote font-medium text-label">持久会话</span>
              <span class="block truncate text-[11px] text-label-tertiary">{{ persistentMeta }}</span>
            </div>
          </li>
        </ul>
      </section>

      <div v-if="!groupedSessions.length" class="px-2 py-6 text-center text-footnote text-label-tertiary">
        点击左侧 + 号新建对话
      </div>

      <section v-for="group in groupedSessions" :key="group.label">
        <h4 class="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-label-tertiary">
          {{ group.label }}
        </h4>
        <ul class="m-0 list-none space-y-1 p-0">
          <li
            v-for="session in group.sessions"
            :key="session.id"
            class="group flex cursor-pointer items-center gap-2 rounded-[10px] border border-transparent px-2.5 py-2 transition-colors hover:bg-accent-soft"
            :class="session.id === currentSessionId ? 'border-accent/30 bg-accent-soft' : ''"
            @click="$emit('select', session.id)"
          >
            <div class="min-w-0 flex-1">
              <span class="block truncate text-footnote font-medium text-label">{{ session.title || '新对话' }}</span>
              <span class="block truncate text-[11px] text-label-tertiary">
                {{ formatTime(session.last_message_at ?? session.updated_at) }}
              </span>
            </div>
            <div class="opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 max-lg:opacity-100" @click.stop>
              <button
                type="button"
                class="rounded-md px-2 py-1 text-[11px] font-semibold text-label-tertiary hover:bg-danger-soft hover:text-danger-text"
                title="删除"
                @click="handleDelete(session)"
              >
                删除
              </button>
            </div>
          </li>
        </ul>
      </section>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ChatSession, PersistentSession } from '@/types'
import { formatChatSessionTime, getBeijingDayDifference } from '@/utils/formatters'

const props = defineProps<{
  sessions: ChatSession[]
  persistentSession: PersistentSession | null
  persistentSelected: boolean
  currentSessionId: number | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'select', sessionId: number): void
  (e: 'selectPersistent'): void
  (e: 'create'): void
  (e: 'delete', sessionId: number): void
}>()

const query = ref('')
const creating = ref(false)

function handleNew() {
  if (creating.value) return
  creating.value = true
  emit('create')
  window.setTimeout(() => {
    creating.value = false
  }, 500)
}

function handleDelete(session: ChatSession) {
  const confirmed = window.confirm(`确定删除对话“${session.title || '新对话'}”吗？`)
  if (!confirmed) return
  emit('delete', session.id)
}

function formatTime(value: string | null): string {
  return formatChatSessionTime(value)
}

const persistentMeta = computed(() => {
  const session = props.persistentSession
  if (!session) return '查看自动化持续上下文'
  return formatTime(session.last_message_at ?? session.updated_at)
})

interface SessionGroup {
  label: string
  sessions: ChatSession[]
}

const filteredSessions = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return props.sessions
  return props.sessions.filter((item) => (item.title || '').toLowerCase().includes(q))
})

const groupedSessions = computed<SessionGroup[]>(() => {
  const list = filteredSessions.value
  if (!list.length) return []

  const today: ChatSession[] = []
  const yesterday: ChatSession[] = []
  const week: ChatSession[] = []
  const older: ChatSession[] = []

  for (const session of list) {
    const dayDifference = getBeijingDayDifference(session.last_message_at ?? session.updated_at)

    if (dayDifference === null) {
      older.push(session)
    } else if (dayDifference <= 0) {
      today.push(session)
    } else if (dayDifference === 1) {
      yesterday.push(session)
    } else if (dayDifference < 7) {
      week.push(session)
    } else {
      older.push(session)
    }
  }

  const groups: SessionGroup[] = []
  if (today.length) groups.push({ label: '今天', sessions: today })
  if (yesterday.length) groups.push({ label: '昨天', sessions: yesterday })
  if (week.length) groups.push({ label: '7 天内', sessions: week })
  if (older.length) groups.push({ label: '更早', sessions: older })
  return groups
})
</script>
