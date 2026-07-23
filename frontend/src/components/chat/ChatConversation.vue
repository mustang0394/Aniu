<template>
  <section
    class="flex h-[min(72vh,760px)] min-h-0 min-w-0 flex-col gap-3 overflow-hidden rounded-[16px] border border-separator p-3.5 shadow-sm glass-card"
  >
    <header class="flex items-center justify-between gap-3 border-b border-separator pb-2.5">
      <div class="flex min-w-0 items-center gap-2.5">
        <button
          v-if="showSidebarToggle"
          type="button"
          class="inline-flex size-9 shrink-0 items-center justify-center rounded-[10px] border border-separator-strong bg-fill text-label-secondary transition-colors hover:bg-hover hover:text-label lg:hidden"
          aria-label="打开会话列表"
          @click="$emit('openSidebar')"
        >
          <svg class="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        <h3 class="m-0 truncate text-title-3 font-semibold text-label">{{ title }}</h3>
        <span v-if="session" class="shrink-0 text-caption text-label-tertiary">
          {{ session.message_count || messages.length }} 条消息
        </span>
      </div>
    </header>

    <div
      v-if="errorMessage"
      class="rounded-[12px] border border-danger/25 bg-danger-soft px-3 py-2.5 text-footnote font-medium text-danger-text"
      role="alert"
    >
      {{ errorMessage }}
    </div>

    <div
      ref="scrollRef"
      class="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto rounded-[12px] bg-gradient-to-b from-accent-soft/40 to-[#f7f8fa] px-2 py-2.5"
      @scroll="handleScroll"
    >
      <div
        v-if="session && messages.length > 0 && (hasMoreMessages || loadingOlderMessages)"
        class="flex justify-center"
      >
        <button
          type="button"
          class="h-8 rounded-pill px-3 text-footnote font-semibold text-accent-text hover:bg-accent-soft disabled:opacity-45"
          :disabled="loadingOlderMessages"
          @click="handleLoadOlder"
        >
          {{ loadingOlderMessages ? '加载中...' : '加载更早消息' }}
        </button>
      </div>
      <div
        v-if="loading"
        class="flex flex-1 items-center justify-center px-4 text-center text-footnote text-label-tertiary"
      >
        加载会话中...
      </div>
      <div
        v-else-if="!session"
        class="flex flex-1 items-center justify-center px-4 text-center text-footnote text-label-tertiary"
      >
        直接输入并发送消息，即可自动开始一个新对话。
      </div>
      <div
        v-else-if="messages.length === 0"
        class="flex flex-1 items-center justify-center px-4 text-center text-footnote text-label-tertiary"
      >
        {{ readOnly ? '当前持久会话暂无消息。' : '开始聊天吧。' }}
      </div>
      <template v-else>
        <ChatMessageItem
          v-for="(message, index) in messages"
          :key="`${message.role}-${message.id ?? index}`"
          :message="message"
          :streaming="isStreamingAssistant(message, index)"
        />
      </template>
      <div ref="bottomRef" class="h-px w-full shrink-0" aria-hidden="true"></div>
    </div>

    <ChatComposer
      v-if="!readOnly"
      v-model="inputValue"
      :session-id="session?.id ?? null"
      :pending-attachments="pendingAttachments"
      :sending="sending"
      :can-send="canSend"
      :ensure-session-ready="ensureSessionReady"
      @submit="$emit('submit')"
      @attach="(attachment) => $emit('attach', attachment)"
      @remove-attachment="(id) => $emit('remove-attachment', id)"
      @upload-error="(msg) => $emit('upload-error', msg)"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ChatComposer from './ChatComposer.vue'
import ChatMessageItem from './ChatMessageItem.vue'
import type { ChatAttachment, ChatMessage, ChatSession, PersistentSession } from '@/types'
import { isChatScrollNearBottom, shouldAutoFollowChatScroll } from '@/utils/chatScrollFollow'

const props = defineProps<{
  session: ChatSession | PersistentSession | null
  messages: ChatMessage[]
  modelValue: string
  pendingAttachments: ChatAttachment[]
  sending: boolean
  loading: boolean
  loadingOlderMessages: boolean
  hasMoreMessages: boolean
  canSend: boolean
  errorMessage: string
  readOnly?: boolean
  ensureSessionReady: () => Promise<number | null>
  loadOlderMessages: () => Promise<void>
  showSidebarToggle?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'submit'): void
  (e: 'attach', attachment: ChatAttachment): void
  (e: 'remove-attachment', id: number): void
  (e: 'upload-error', message: string): void
  (e: 'openSidebar'): void
}>()

const scrollRef = ref<HTMLElement | null>(null)
const bottomRef = ref<HTMLElement | null>(null)
const title = computed(() => props.session?.title || '\u672a\u9009\u62e9\u4f1a\u8bdd')

const inputValue = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

let scrollFrameId: number | null = null
const shouldFollowScroll = ref(true)

function isStreamingAssistant(message: ChatMessage, index: number): boolean {
  return props.sending && message.role === 'assistant' && index === props.messages.length - 1
}

function shouldAutoFollow(): boolean {
  return shouldAutoFollowChatScroll({
    sending: props.sending,
    messageCount: props.messages.length,
    followScroll: shouldFollowScroll.value,
  })
}

function syncScrollToBottom() {
  const messageList = scrollRef.value
  if (messageList) {
    messageList.scrollTop = messageList.scrollHeight
    return
  }

  bottomRef.value?.scrollIntoView({
    block: 'end',
    inline: 'nearest',
    behavior: 'auto',
  })
}

function scrollToBottom() {
  if (scrollFrameId !== null) {
    window.cancelAnimationFrame(scrollFrameId)
  }

  scrollFrameId = window.requestAnimationFrame(() => {
    scrollFrameId = null
    syncScrollToBottom()
  })
}

async function handleLoadOlder() {
  if (props.loadingOlderMessages || !props.hasMoreMessages) {
    return
  }

  const element = scrollRef.value
  const previousScrollHeight = element?.scrollHeight ?? 0
  const previousScrollTop = element?.scrollTop ?? 0
  shouldFollowScroll.value = false

  await props.loadOlderMessages()
  await nextTick()

  if (!element) {
    return
  }

  const heightDelta = element.scrollHeight - previousScrollHeight
  element.scrollTop = previousScrollTop + heightDelta
  handleScroll()
}

function handleScroll() {
  const element = scrollRef.value
  shouldFollowScroll.value = !element || isChatScrollNearBottom({
    scrollHeight: element.scrollHeight,
    scrollTop: element.scrollTop,
    clientHeight: element.clientHeight,
  })
}

watch(
  () => ({
    messageCount: props.messages.length,
    lastContent: props.messages[props.messages.length - 1]?.content ?? '',
    sending: props.sending,
  }),
  (current, previous) => {
    const startedSending = current.sending && !previous?.sending
    if (startedSending) {
      shouldFollowScroll.value = true
    }

    const hasNewMessage = current.messageCount !== (previous?.messageCount ?? 0)
    const hasStreamUpdate = current.sending && current.lastContent !== (previous?.lastContent ?? '')
    if (!hasNewMessage && !hasStreamUpdate && !startedSending) {
      return
    }

    if (!shouldAutoFollow()) return
    scrollToBottom()
  },
  { flush: 'post' },
)

onMounted(() => {
  if (props.sending && shouldAutoFollow()) {
    shouldFollowScroll.value = true
    scrollToBottom()
  }
})

onBeforeUnmount(() => {
  if (scrollFrameId !== null) {
    window.cancelAnimationFrame(scrollFrameId)
    scrollFrameId = null
  }
})
</script>
