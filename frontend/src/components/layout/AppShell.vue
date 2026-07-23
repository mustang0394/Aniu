<template>
  <div class="min-h-dvh">
    <!-- Desktop fixed sidebar -->
    <div class="fixed inset-y-0 left-0 z-40 hidden w-sidebar lg:block">
      <AppSidebar @logout="handleLogout" />
    </div>

    <!-- Mobile drawer -->
    <div
      v-if="mobileNavOpen"
      class="fixed inset-0 z-50 lg:hidden"
      role="dialog"
      aria-modal="true"
      aria-label="导航菜单"
    >
      <div
        class="absolute inset-0 glass-overlay"
        @click="mobileNavOpen = false"
      />
      <div class="absolute inset-y-0 left-0 w-sidebar max-w-[85vw] shadow-lg">
        <AppSidebar @navigate="mobileNavOpen = false" @logout="handleLogout" />
      </div>
    </div>

    <!-- Main column -->
    <div class="flex min-h-dvh flex-col lg:pl-sidebar">
      <AppTopBar
        :title="pageTitle"
        @open-menu="mobileNavOpen = true"
        @logout="handleLogout"
      />

      <div
        v-if="errorMessage"
        class="mx-4 mt-3 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text lg:mx-8"
        role="alert"
      >
        {{ errorMessage }}
      </div>

      <main class="min-w-0 flex-1 px-4 py-5 sm:px-6 lg:px-8 lg:py-7">
        <div class="mx-auto w-full max-w-[1480px] animate-fade-in">
          <slot />
        </div>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { navTitleForPath } from '@/config/navigation'
import { useAppStore } from '@/stores/legacy'
import {
  clearStoredLoginFlag,
  clearStoredLoginNotice,
  clearStoredLoginRedirect,
  clearStoredToken,
} from '@/services/api'
import AppSidebar from './AppSidebar.vue'
import AppTopBar from './AppTopBar.vue'

const store = useAppStore()
const router = useRouter()
const route = useRoute()
const { errorMessage } = storeToRefs(store)
const mobileNavOpen = ref(false)

const pageTitle = computed(() => navTitleForPath(route.path))

watch(
  () => route.fullPath,
  () => {
    mobileNavOpen.value = false
  },
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') mobileNavOpen.value = false
}

watch(mobileNavOpen, (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
})

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})

function handleLogout() {
  mobileNavOpen.value = false
  clearStoredToken()
  clearStoredLoginFlag()
  clearStoredLoginNotice()
  clearStoredLoginRedirect()
  store.resetState()
  router.replace('/login')
}
</script>
