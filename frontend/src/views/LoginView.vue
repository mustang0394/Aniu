<template>
  <div
    class="relative flex min-h-dvh items-center justify-center px-4 py-10"
  >
    <!-- Ambient background -->
    <div
      class="pointer-events-none absolute inset-0 -z-10"
      aria-hidden="true"
    >
      <div
        class="absolute -top-24 left-1/2 h-[420px] w-[720px] -translate-x-1/2 rounded-full bg-accent/10 blur-3xl"
      />
      <div
        class="absolute bottom-0 right-0 h-[320px] w-[420px] rounded-full bg-success/10 blur-3xl"
      />
    </div>

    <section
      class="w-full max-w-[400px] rounded-xl border border-separator p-8 shadow-lg glass-card sm:p-9"
    >
      <div class="flex flex-col items-center text-center">
        <div
          class="mb-5 flex size-[72px] items-center justify-center rounded-[22px] bg-accent-soft shadow-md ring-1 ring-accent/15"
        >
          <img src="/aniu.ico" alt="Aniu" class="size-14 rounded-[16px]" />
        </div>
        <h1 class="m-0 text-title-1 font-semibold tracking-tight text-label">Aniu</h1>
        <p class="mt-2 mb-0 text-body text-label-secondary">
          输入密码登录 AI 模拟交易系统
        </p>
      </div>

      <form class="mt-8 flex flex-col gap-4" @submit.prevent="handleSubmit">
        <UiField label="密码">
          <input
            v-model="password"
            type="password"
            placeholder="请输入密码"
            autocomplete="current-password"
            class="h-11 w-full rounded-[12px] border border-separator-strong bg-fill px-3.5 text-body text-label outline-none transition-colors placeholder:text-label-quaternary focus:border-accent focus:bg-accent-soft/40 focus:ring-2 focus:ring-accent-ring"
          />
        </UiField>

        <label class="flex cursor-pointer items-center gap-2.5 text-footnote text-label-secondary">
          <input
            v-model="rememberCredentials"
            type="checkbox"
            class="size-4 rounded border-separator-strong"
          />
          <span>默认记住密码</span>
        </label>

        <p
          v-if="errorMessage"
          class="m-0 rounded-[10px] border border-danger/25 bg-danger-soft px-3 py-2.5 text-footnote font-medium text-danger-text"
          role="alert"
        >
          {{ errorMessage }}
        </p>

        <UiButton type="submit" size="lg" block :loading="submitting" :disabled="submitting">
          {{ submitting ? '登录中…' : '登录' }}
        </UiButton>
      </form>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import UiButton from '@/components/ui/UiButton.vue'
import UiField from '@/components/ui/UiField.vue'
import {
  api,
  clearStoredLoginFlag,
  clearStoredLoginNotice,
  clearStoredToken,
  consumeStoredLoginNotice,
  consumeStoredLoginRedirect,
  getStoredLoginFlag,
  getStoredToken,
  setStoredLoginFlag,
  setStoredToken,
} from '@/services/api'
import {
  REMEMBERED_PASSWORD_STORAGE_KEY,
} from '@/constants'

const router = useRouter()
const password = ref('')
const rememberCredentials = ref(true)
const errorMessage = ref('')
const submitting = ref(false)

function resolvePostLoginPath() {
  return consumeStoredLoginRedirect() || '/overview'
}

onMounted(() => {
  password.value = window.localStorage.getItem(REMEMBERED_PASSWORD_STORAGE_KEY) ?? ''
  const pendingNotice = consumeStoredLoginNotice()
  if (pendingNotice) {
    errorMessage.value = pendingNotice
  }

  if (getStoredLoginFlag() && getStoredToken()) {
    router.replace(resolvePostLoginPath())
  }
})

async function handleSubmit() {
  if (!password.value.trim()) {
    errorMessage.value = '请输入密码。'
    return
  }

  submitting.value = true
  try {
    const response = await api.login({
      password: password.value,
    })
    if (!response.authenticated || !response.token) {
      throw new Error('登录失败，请检查密码。')
    }
    setStoredToken(response.token)
    setStoredLoginFlag(response.authenticated)
    if (rememberCredentials.value) {
      window.localStorage.setItem(REMEMBERED_PASSWORD_STORAGE_KEY, password.value)
    } else {
      window.localStorage.removeItem(REMEMBERED_PASSWORD_STORAGE_KEY)
    }
    errorMessage.value = ''
    clearStoredLoginNotice()
    router.replace(resolvePostLoginPath())
  } catch (error) {
    clearStoredToken()
    clearStoredLoginFlag()
    errorMessage.value = (error as Error).message
  } finally {
    submitting.value = false
  }
}
</script>
