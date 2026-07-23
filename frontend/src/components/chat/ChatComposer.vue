<template>
  <div
    class="relative mt-auto flex flex-col gap-2"
    :class="{ 'is-dragging': dragActive }"
    @dragenter.prevent="handleDragEnter"
    @dragover.prevent="handleDragOver"
    @dragleave.prevent="handleDragLeave"
    @drop.prevent="handleDrop"
  >
    <div v-if="pendingAttachments.length" class="flex flex-wrap gap-2 px-1">
      <ChatAttachmentChip
        v-for="attachment in pendingAttachments"
        :key="attachment.id"
        :attachment="attachment"
        removable
        @remove="$emit('remove-attachment', $event)"
      />
    </div>

    <div
      v-if="dragActive"
      class="pointer-events-none absolute inset-0 z-[2] flex items-center justify-center rounded-[18px] border border-dashed border-accent/50 bg-glass-thin text-body font-semibold text-label backdrop-blur-sm"
    >
      松开以上传图片或文件
    </div>

    <div
      class="relative grid grid-cols-[42px_minmax(0,1fr)_44px] items-end gap-3 rounded-[18px] border border-black/[0.07] px-3.5 py-2.5 shadow-md glass-card transition-all"
      :class="[
        disabled ? 'opacity-70' : '',
        dragActive ? 'border-accent/40 shadow-md' : '',
        'focus-within:border-accent/30 focus-within:shadow-md',
      ]"
    >
      <button
        type="button"
        class="inline-flex size-10 items-center justify-center self-end rounded-[12px] border border-accent/15 bg-accent-soft text-accent-text transition-all hover:-translate-y-px hover:bg-accent/15 hover:text-accent-hover disabled:opacity-40"
        title="添加附件"
        :disabled="disabled || uploading"
        @click="triggerFilePicker"
      >
        <svg class="size-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="8.5" />
          <path d="M12 8v8M8 12h8" />
        </svg>
      </button>

      <div class="relative flex min-w-0 items-stretch">
        <div
          v-if="!inputValue && !isInputFocused"
          class="pointer-events-none absolute inset-0 flex items-center overflow-hidden text-ellipsis whitespace-nowrap text-callout text-label-tertiary"
          aria-hidden="true"
        >
          {{ placeholder }}
        </div>
        <textarea
          ref="textareaRef"
          v-model="inputValue"
          rows="1"
          class="max-h-[200px] min-h-10 w-full resize-none overflow-y-hidden border-0 bg-transparent py-2 text-callout leading-6 text-label outline-none disabled:cursor-not-allowed disabled:text-label-tertiary"
          placeholder=""
          :aria-label="placeholder"
          :disabled="disabled"
          @input="syncTextareaHeight"
          @keydown="handleKeyDown"
          @paste="handlePaste"
          @focus="handleFocus"
          @blur="handleBlur"
          @compositionstart="handleCompositionStart"
          @compositionend="handleCompositionEnd"
        />
      </div>

      <button
        type="button"
        class="inline-flex size-10 items-center justify-center self-end rounded-[12px] transition-all"
        :class="canSend
          ? 'bg-accent text-on-accent hover:-translate-y-px hover:bg-accent-hover'
          : 'cursor-not-allowed bg-fill-tertiary text-label-quaternary'"
        :disabled="!canSend"
        @click="handleSubmit"
      >
        <span
          v-if="sending"
          class="size-4 rounded-full border-2 border-white/35 border-t-white animate-spin-slow"
          aria-hidden="true"
        />
        <svg v-else class="size-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M20.5 4.6a1 1 0 0 0-1.1-.2L4.3 11a1 1 0 0 0 .1 1.9l5.6 1.9 1.9 5.6a1 1 0 0 0 1.9.1l6.7-15.1a1 1 0 0 0-.1-.8Z" />
          <path d="M10.1 14 19 5.1" />
        </svg>
      </button>
    </div>

    <div v-if="uploading" class="px-2 text-caption text-warning">上传中…</div>

    <input
      ref="fileInputRef"
      type="file"
      multiple
      class="hidden"
      :accept="acceptTypes"
      @change="handleFileChange"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import ChatAttachmentChip from './ChatAttachmentChip.vue'
import { api } from '@/services/api'
import type { ChatAttachment } from '@/types'

const props = defineProps<{
  modelValue: string
  sessionId: number | null
  pendingAttachments: ChatAttachment[]
  sending: boolean
  canSend: boolean
  ensureSessionReady?: () => Promise<number | null>
  disabled?: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
  (e: 'submit'): void
  (e: 'attach', attachment: ChatAttachment): void
  (e: 'remove-attachment', id: number): void
  (e: 'upload-error', message: string): void
}>()

const textareaRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const dragActive = ref(false)
const dragDepth = ref(0)
const isComposing = ref(false)
const isInputFocused = ref(false)

const acceptTypes = [
  'image/*',
  'text/*',
  '.md',
  '.markdown',
  '.txt',
  '.csv',
  '.json',
  '.jsonl',
  '.xml',
  '.yaml',
  '.yml',
  '.log',
  '.docx',
  '.xlsx',
  '.pptx',
].join(',')

const placeholder = computed(
  () => props.placeholder ?? (props.disabled ? '请先选择一个会话。' : '发送消息...'),
)

const inputValue = computed({
  get: () => props.modelValue,
  set: (value: string) => emit('update:modelValue', value),
})

function handleSubmit() {
  if (!props.canSend) return
  emit('submit')
}

async function resolveSessionId(): Promise<number | null> {
  if (props.sessionId !== null) {
    return props.sessionId
  }
  if (!props.ensureSessionReady) {
    emit('upload-error', '创建会话失败，请稍后重试。')
    return null
  }
  return props.ensureSessionReady()
}

function triggerFilePicker() {
  if (props.disabled || uploading.value) return
  fileInputRef.value?.click()
}

function syncTextareaHeight() {
  const textarea = textareaRef.value
  if (!textarea) return

  textarea.style.height = '0px'
  const nextHeight = Math.min(Math.max(textarea.scrollHeight, 44), 220)
  textarea.style.height = `${nextHeight}px`
  textarea.style.overflowY = textarea.scrollHeight > 220 ? 'auto' : 'hidden'
}

function handleKeyDown(event: KeyboardEvent) {
  if (
    event.key === 'Enter'
    && !event.shiftKey
    && !event.isComposing
    && !isComposing.value
  ) {
    event.preventDefault()
    handleSubmit()
  }
}

function handleCompositionStart() {
  isComposing.value = true
}

function handleCompositionEnd() {
  isComposing.value = false
  syncTextareaHeight()
}

function handleFocus() {
  isInputFocused.value = true
}

function handleBlur() {
  isInputFocused.value = false
}

async function uploadFiles(files: File[]) {
  if (!files.length) return
  const sessionId = await resolveSessionId()
  if (sessionId === null) {
    return
  }

  uploading.value = true
  try {
    for (const file of files) {
      try {
        const attachment = await api.uploadChatAttachment(file, sessionId)
        emit('attach', attachment)
      } catch (error) {
        emit('upload-error', (error as Error).message)
      }
    }
  } finally {
    uploading.value = false
  }
}

async function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  if (!target.files) return
  const files = Array.from(target.files)
  target.value = ''
  await uploadFiles(files)
}

async function handlePaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return

  const files: File[] = []
  for (const item of Array.from(items)) {
    if (item.kind === 'file') {
      const file = item.getAsFile()
      if (file) files.push(file)
    }
  }
  if (files.length) {
    event.preventDefault()
    await uploadFiles(files)
  }
}

function hasFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

function handleDragEnter(event: DragEvent) {
  if (props.disabled || !hasFiles(event)) return
  dragDepth.value += 1
  dragActive.value = true
}

function handleDragOver(event: DragEvent) {
  if (props.disabled || !hasFiles(event)) return
  if (event.dataTransfer) {
    event.dataTransfer.dropEffect = 'copy'
  }
  dragActive.value = true
}

function handleDragLeave(event: DragEvent) {
  if (props.disabled || !hasFiles(event)) return
  dragDepth.value = Math.max(0, dragDepth.value - 1)
  if (dragDepth.value === 0) {
    dragActive.value = false
  }
}

async function handleDrop(event: DragEvent) {
  dragDepth.value = 0
  dragActive.value = false
  if (props.disabled || !event.dataTransfer?.files?.length) return
  await uploadFiles(Array.from(event.dataTransfer.files))
}

watch(
  () => props.modelValue,
  () => {
    nextTick(syncTextareaHeight)
  },
  { immediate: true },
)

onMounted(() => {
  syncTextareaHeight()
})

defineExpose({
  focus: () => textareaRef.value?.focus(),
})
</script>

<style scoped>
.chat-composer-file-input {
  display: none;
}
</style>
