<template>
  <article
    class="chat-message group relative max-w-[82%] px-3.5 py-3"
    :class="{
      'role-user self-end rounded-[18px_18px_4px_18px] bg-accent text-on-accent shadow-[0_2px_8px_rgba(0,122,255,0.22)]': message.role === 'user',
      'role-assistant self-start rounded-[18px_18px_18px_4px] border border-separator bg-card-solid shadow-sm': message.role === 'assistant',
      'role-system max-w-[min(100%,640px)] self-center px-3 py-1 shadow-none': message.role === 'system',
    }"
  >
    <div class="mb-1.5 flex items-center justify-between gap-2">
      <div
        class="text-[11px] font-bold"
        :class="{
          'text-white/80': message.role === 'user',
          'text-accent-text': message.role === 'assistant',
          'text-label-tertiary': message.role === 'system',
        }"
      >
        {{ roleLabel }}
      </div>
      <button
        v-if="canCopy"
        type="button"
        class="inline-flex size-[26px] shrink-0 items-center justify-center rounded-[8px] transition-all"
        :class="[
          message.role === 'user'
            ? 'text-white/75 hover:bg-white/15 hover:text-white'
            : 'text-label-tertiary hover:bg-fill hover:text-accent-text',
          copyState === 'copied' ? 'opacity-100 text-success-text' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100 max-lg:opacity-100',
        ]"
        :disabled="streaming || copyState === 'copying'"
        :aria-label="copyAriaLabel"
        :title="copyTitle"
        @click="handleCopy"
      >
        <svg v-if="copyState === 'copied'" class="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M5 13l4 4L19 7" />
        </svg>
        <svg v-else class="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      </button>
    </div>

    <div class="leading-relaxed" :class="message.role === 'system' ? 'text-caption text-label-tertiary' : message.role === 'user' ? 'text-white' : 'text-label'">
      <div v-if="toolCalls.length" class="mb-2.5 overflow-hidden rounded-[10px] border border-separator bg-accent-soft">
        <button
          type="button"
          class="flex w-full items-center justify-between gap-2.5 px-3 py-2.5 text-left text-caption font-semibold text-label hover:bg-accent/10"
          :aria-expanded="toolCallsExpanded"
          @click="toolCallsExpanded = !toolCallsExpanded"
        >
          <span>工具调用 {{ toolCalls.length }} 项</span>
          <span class="font-medium text-accent-text">{{ toolCallsExpanded ? '收起' : '展开' }}</span>
        </button>
        <ul v-show="toolCallsExpanded" class="m-0 flex list-none flex-col gap-1 px-3 pb-3">
          <li
            v-for="(tool, ti) in toolCalls"
            :key="`${tool.tool_name}-${ti}`"
            class="flex flex-wrap items-center gap-2 text-caption text-label-secondary"
          >
            <span class="font-semibold text-accent-text">{{ tool.tool_name }}</span>
            <span
              :class="{
                'text-warning': tool.status === 'running',
                'text-success-text': tool.status !== 'running' && tool.ok !== false,
                'text-danger-text': tool.status !== 'running' && tool.ok === false,
              }"
            >{{ toolStatusText(tool) }}</span>
            <span v-if="tool.summary" class="basis-full text-[11px] text-label-tertiary">{{ tool.summary }}</span>
          </li>
        </ul>
      </div>

      <div v-if="attachmentList.length" class="mb-2 flex flex-wrap gap-2">
        <ChatAttachmentChip
          v-for="attachment in attachmentList"
          :key="attachment.id"
          :attachment="attachment"
        />
      </div>

      <MarkdownMessage v-if="displayContent" :content="displayContent" />
      <div v-else-if="streaming" class="italic text-label-tertiary">正在生成…</div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, defineComponent, h, onBeforeUnmount, ref, watch } from 'vue'

import ChatAttachmentChip from './ChatAttachmentChip.vue'
import type { ChatMessage, ChatToolCall } from '@/types'
import { copyText } from '@/utils/clipboard'

const MarkdownMessage = defineAsyncComponent({
  loader: () => import('./MarkdownMessage.vue'),
  delay: 0,
  loadingComponent: defineComponent({
    name: 'MarkdownMessageFallback',
    props: {
      content: {
        type: String,
        required: true,
      },
    },
    setup(fallbackProps) {
      return () => h('div', { class: 'chat-message-text' }, fallbackProps.content)
    },
  }),
})

const props = defineProps<{
  message: ChatMessage
  streaming?: boolean
}>()

const attachmentList = computed(() => props.message.attachments ?? [])
const toolCalls = computed(() => props.message.tool_calls ?? [])
const roleLabel = computed(() => {
  if (props.message.role === 'user') return '我'
  if (props.message.role === 'system') return '系统'
  return 'Aniu'
})

const displayContent = computed(() => {
  if (props.message.content) return props.message.content
  if (props.streaming) return '正在生成…'
  return ''
})

const canCopy = computed(() => {
  if (props.message.role !== 'user' && props.message.role !== 'assistant') {
    return false
  }
  return Boolean(props.message.content?.trim())
})

const copyState = ref<'idle' | 'copying' | 'copied' | 'failed'>('idle')
let copyResetTimer: ReturnType<typeof setTimeout> | null = null

const copyAriaLabel = computed(() => {
  if (copyState.value === 'copied') return '已复制'
  if (copyState.value === 'failed') return '复制失败'
  return '复制消息'
})

const copyTitle = computed(() => {
  if (props.streaming) return '生成中，完成后可复制'
  if (copyState.value === 'copied') return '已复制'
  if (copyState.value === 'failed') return '复制失败，请重试'
  return '复制消息'
})

async function handleCopy() {
  if (props.streaming || !canCopy.value || copyState.value === 'copying') {
    return
  }

  const text = props.message.content?.trim() ?? ''
  if (!text) {
    return
  }

  copyState.value = 'copying'
  const ok = await copyText(text)
  copyState.value = ok ? 'copied' : 'failed'

  if (copyResetTimer !== null) {
    clearTimeout(copyResetTimer)
  }
  copyResetTimer = setTimeout(() => {
    copyState.value = 'idle'
    copyResetTimer = null
  }, 1600)
}

const toolCallsExpanded = ref(Boolean(props.streaming))

watch(
  toolCalls,
  (value) => {
    if (value.length && props.streaming) {
      toolCallsExpanded.value = true
    }
  },
  { deep: true },
)

function toolStatusClass(tool: ChatToolCall): string {
  if (tool.status === 'running') return 'is-running'
  return tool.ok === false ? 'is-failed' : 'is-done'
}

function toolStatusText(tool: ChatToolCall): string {
  if (tool.status === 'running') return '调用中'
  return tool.ok === false ? '失败' : '完成'
}

onBeforeUnmount(() => {
  if (copyResetTimer !== null) {
    clearTimeout(copyResetTimer)
    copyResetTimer = null
  }
})
</script>
