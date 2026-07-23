<template>
  <article class="chat-message" :class="`role-${message.role}`">
    <div class="chat-message-head">
      <div class="chat-message-role">{{ roleLabel }}</div>
      <button
        v-if="canCopy"
        type="button"
        class="chat-message-copy"
        :class="{ 'is-copied': copyState === 'copied' }"
        :disabled="streaming || copyState === 'copying'"
        :aria-label="copyAriaLabel"
        :title="copyTitle"
        @click="handleCopy"
      >
        <svg v-if="copyState === 'copied'" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 13l4 4L19 7" />
        </svg>
        <svg v-else viewBox="0 0 24 24" aria-hidden="true">
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      </button>
    </div>

    <div class="chat-message-body">
      <div v-if="toolCalls.length" class="chat-tool-call-card">
        <button
          type="button"
          class="chat-tool-call-toggle"
          :aria-expanded="toolCallsExpanded"
          @click="toolCallsExpanded = !toolCallsExpanded"
        >
          <span>工具调用 {{ toolCalls.length }} 项</span>
          <span class="chat-tool-call-toggle-icon">{{ toolCallsExpanded ? '收起' : '展开' }}</span>
        </button>

        <ul v-show="toolCallsExpanded" class="chat-tool-calls">
          <li
            v-for="(tool, ti) in toolCalls"
            :key="`${tool.tool_name}-${ti}`"
            :class="toolStatusClass(tool)"
          >
            <span class="chat-tool-name">{{ tool.tool_name }}</span>
            <span class="chat-tool-status">{{ toolStatusText(tool) }}</span>
            <span v-if="tool.summary" class="chat-tool-summary">{{ tool.summary }}</span>
          </li>
        </ul>
      </div>

      <div v-if="attachmentList.length" class="chat-message-attachments">
        <ChatAttachmentChip
          v-for="attachment in attachmentList"
          :key="attachment.id"
          :attachment="attachment"
        />
      </div>

      <MarkdownMessage v-if="displayContent" :content="displayContent" />
      <div v-else-if="streaming" class="chat-message-text is-placeholder">正在生成…</div>
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
