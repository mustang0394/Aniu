<template>
  <div class="flex min-h-0 flex-col">
    <UiPageHeader
      class="!mb-4"
      title="AI 聊天"
      kicker="Chat"
      :description="`与 ${appDisplayName} 对话，支持附件与工具调用`"
    >
      <select
        v-if="store.hasMultipleAccounts"
        :value="store.selectedAccountId ?? ''"
        class="input h-9 w-auto py-1"
        @change="handleAccountSwitch"
      >
        <option v-for="acc in store.activeAccounts" :key="acc.id" :value="acc.id">
          {{ acc.name }}（{{ acc.slug }}）
        </option>
      </select>
    </UiPageHeader>

    <div class="relative grid min-h-[min(72vh,760px)] grid-cols-1 gap-3 lg:grid-cols-[minmax(240px,280px)_minmax(0,1fr)]">
      <!-- Mobile session backdrop -->
      <div
        class="fixed inset-0 z-40 glass-overlay lg:hidden"
        :class="sidebarOpen ? 'block' : 'hidden'"
        aria-hidden="true"
        @click="sidebarOpen = false"
      />

      <!-- Session sidebar -->
      <div
        class="z-50 flex min-h-0 flex-col max-lg:fixed max-lg:inset-y-0 max-lg:left-0 max-lg:w-[min(280px,85vw)] max-lg:transition-transform max-lg:duration-200"
        :class="sidebarOpen ? 'max-lg:translate-x-0' : 'max-lg:-translate-x-full lg:translate-x-0'"
      >
        <ChatSessionSidebar
          class="h-full min-h-0"
          :sessions="sessions"
          :persistent-session="persistentSession"
          :persistent-selected="persistentSelected"
          :current-session-id="currentSessionId"
          :loading="sessionsLoading"
          @select="handleSelect"
          @select-persistent="handleSelectPersistent"
          @create="handleCreate"
          @delete="handleDelete"
        />
      </div>

      <ChatConversation
        class="min-h-0 min-w-0"
        :session="persistentSelected ? persistentSession : currentSession"
        :messages="persistentSelected ? persistentMessages : messages"
        v-model="input"
        :pending-attachments="pendingAttachments"
        :sending="sending"
        :loading="persistentSelected ? persistentLoading : loading"
        :loading-older-messages="persistentSelected ? persistentLoadingOlder : loadingOlderMessages"
        :has-more-messages="persistentSelected ? persistentHasMoreMessages : hasMoreMessages"
        :can-send="persistentSelected ? false : canSend"
        :error-message="persistentSelected ? persistentErrorMessage : errorMessage"
        :read-only="persistentSelected"
        :ensure-session-ready="ensureSessionReady"
        :load-older-messages="persistentSelected ? loadOlderPersistentMessages : loadOlderMessages"
        :show-sidebar-toggle="true"
        @submit="handleSubmit"
        @attach="addAttachment"
        @remove-attachment="removeAttachment"
        @upload-error="handleUploadError"
        @open-sidebar="sidebarOpen = true"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'

import ChatConversation from '@/components/chat/ChatConversation.vue'
import ChatSessionSidebar from '@/components/chat/ChatSessionSidebar.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import { useChatSession } from '@/composables/useChatSession'
import { useChatSessions } from '@/composables/useChatSessions'
import { usePersistentSession } from '@/composables/usePersistentSession'
import { useRunStream } from '@/composables/useRunStream'
import { useTradingAccountsStore } from '@/stores/tradingAccounts'
import { useAppStore } from '@/stores/legacy'

const store = useTradingAccountsStore()
const accountScope = () => store.selectedAccountId
const legacyStore = useAppStore()
const appDisplayName = computed(() => legacyStore.appDisplayName)

const {
  sessions,
  currentSession,
  currentSessionId,
  loading: sessionsLoading,
  loadSessions,
  createSession,
  deleteSession,
  selectSession,
  touchSession,
} = useChatSessions(accountScope)

const {
  messages,
  input,
  pendingAttachments,
  sending,
  loading,
  loadingOlderMessages,
  errorMessage,
  canSend,
  activeSessionId,
  hasMoreMessages,
  loadSession,
  loadOlderMessages,
  sendMessage,
  addAttachment,
  removeAttachment,
} = useChatSession(accountScope)

const {
  session: persistentSession,
  messages: persistentMessages,
  loading: persistentLoading,
  loadingOlderMessages: persistentLoadingOlder,
  errorMessage: persistentErrorMessage,
  hasMoreMessages: persistentHasMoreMessages,
  loadSession: loadPersistentSession,
  loadOlderMessages: loadOlderPersistentMessages,
  refreshSummaryOnly: refreshPersistentSummaryOnly,
  appendSystemMessage: appendPersistentSystemMessage,
  clear: clearPersistentSession,
} = usePersistentSession(accountScope)

const runStream = useRunStream()

const skipNextSessionLoad = ref(false)
const persistentSelected = ref(false)
const sidebarOpen = ref(false)
const DEFAULT_SESSION_TITLE = '\u65b0\u5bf9\u8bdd'
const DEFAULT_SESSION_TITLES = new Set([DEFAULT_SESSION_TITLE, '\u65b0\u4f1a\u8bdd'])

function deriveSessionTitle(currentTitle: string | undefined, content: string): string {
  const normalizedTitle = currentTitle?.trim() ?? ''
  if (normalizedTitle && !DEFAULT_SESSION_TITLES.has(normalizedTitle)) {
    return normalizedTitle
  }

  const firstLine = content.trim().split(/\r?\n/u)[0]?.trim() ?? ''
  return firstLine.slice(0, 30) || DEFAULT_SESSION_TITLE
}

async function restoreCurrentSession(forceReload = false) {
  await loadSessions()

  const currentId = currentSessionId.value
  const hasCurrentSession = currentId !== null && sessions.value.some((item) => item.id === currentId)
  const nextSessionId = hasCurrentSession ? currentId : (sessions.value[0]?.id ?? null)

  if (nextSessionId !== currentSessionId.value) {
    selectSession(nextSessionId)
    return
  }

  activeSessionId.value = nextSessionId
  if (forceReload || nextSessionId === null) {
    await loadSession(nextSessionId)
  }
}

onMounted(async () => {
  try {
    await store.loadAccounts()
  } catch {
    // 账户加载失败不阻塞聊天
  }
  await Promise.all([
    restoreCurrentSession(true),
    refreshPersistentSummaryOnly(),
  ])
})

const disposeRunStreamListener = runStream.onEvent((event) => {
  if (event.type !== 'context_compacted') {
    return
  }
  const content = String(event.content || '').trim()
  if (!content) {
    return
  }
  appendPersistentSystemMessage(content, new Date().toISOString())
})

function handleAccountSwitch(event: Event) {
  const target = event.target as HTMLSelectElement
  const accountIdValue = Number(target.value)
  if (Number.isFinite(accountIdValue)) {
    store.selectAccount(accountIdValue)
  }
}

watch(
  () => store.selectedAccountId,
  (newId, oldId) => {
    if (newId === null || newId === oldId) {
      return
    }
    persistentSelected.value = false
    clearPersistentSession()
    skipNextSessionLoad.value = false
    void restoreCurrentSession(true).then(() => refreshPersistentSummaryOnly())
  },
)

watch(currentSessionId, async (sessionId) => {
  if (persistentSelected.value) {
    return
  }
  activeSessionId.value = sessionId
  if (skipNextSessionLoad.value && sessionId !== null) {
    skipNextSessionLoad.value = false
    return
  }
  await loadSession(sessionId)
})

function handleSelect(sessionId: number) {
  persistentSelected.value = false
  selectSession(sessionId)
  sidebarOpen.value = false
}

async function handleSelectPersistent() {
  persistentSelected.value = true
  activeSessionId.value = null
  await loadPersistentSession()
  sidebarOpen.value = false
}

async function ensureSessionReady(): Promise<number | null> {
  if (currentSessionId.value !== null) {
    return currentSessionId.value
  }

  try {
    skipNextSessionLoad.value = true
    const created = await createSession()
    activeSessionId.value = created.id
    return created.id
  } catch (error) {
    skipNextSessionLoad.value = false
    errorMessage.value = (error as Error).message
    return null
  }
}

async function handleCreate() {
  persistentSelected.value = false
  try {
    const created = await createSession()
    activeSessionId.value = created.id
  } catch (error) {
    errorMessage.value = (error as Error).message
  }
}

async function handleDelete(sessionId: number) {
  try {
    await deleteSession(sessionId)
    if (currentSessionId.value === null) {
      await loadSession(null)
    }
  } catch (error) {
    errorMessage.value = (error as Error).message
  }
}

async function handleSubmit() {
  const sessionId = await ensureSessionReady()
  if (sessionId === null) {
    return
  }

  const submittedContent = input.value.trim()
  const currentTitle = currentSession.value?.title
  const currentMessageCount = currentSession.value?.message_count ?? 0
  const result = await sendMessage()
  if (result) {
    touchSession(result.sessionId, {
      title: deriveSessionTitle(currentTitle, submittedContent),
      message_count: currentMessageCount + 2,
    })
  }
}

function handleUploadError(message: string) {
  errorMessage.value = message
}

watch(persistentSelected, (selected) => {
  if (!selected) {
    clearPersistentSession()
  }
})

onBeforeUnmount(() => {
  disposeRunStreamListener()
})
</script>
