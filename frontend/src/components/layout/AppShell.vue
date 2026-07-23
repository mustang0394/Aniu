<template>
  <div class="min-h-dvh">
    <!-- Desktop fixed sidebar -->
    <div class="fixed inset-y-0 left-0 z-40 hidden w-sidebar lg:block">
      <AppSidebar @logout="handleLogout" />
    </div>

    <!-- Mobile drawer: separate transitions so Vue can measure duration on each root -->
    <Teleport to="body">
      <div class="lg:hidden" aria-hidden="true">
        <!-- Backdrop fade -->
        <Transition name="mobile-backdrop">
          <div
            v-if="mobileNavOpen"
            class="fixed inset-0 z-50 bg-black/40"
            @click="closeMobileNav"
          />
        </Transition>

        <!-- Panel slide — solid white so page content never bleeds through -->
        <Transition name="mobile-panel">
          <div
            v-if="mobileNavOpen"
            class="fixed inset-y-0 left-0 z-50 flex w-sidebar max-w-[85vw] flex-col bg-white shadow-[8px_0_32px_rgba(0,0,0,0.18)]"
            role="dialog"
            aria-modal="true"
            aria-label="导航菜单"
          >
            <AppSidebar
              surface="solid"
              @navigate="closeMobileNav"
              @logout="handleLogout"
            />
          </div>
        </Transition>
      </div>
    </Teleport>

    <!-- Main column -->
    <div class="flex min-h-dvh flex-col lg:pl-sidebar">
      <AppTopBar
        :title="pageTitle"
        @open-menu="openMobileNav"
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

function openMobileNav() {
  mobileNavOpen.value = true
}

function closeMobileNav() {
  mobileNavOpen.value = false
}

watch(
  () => route.fullPath,
  () => {
    closeMobileNav()
  },
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeMobileNav()
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
  closeMobileNav()
  clearStoredToken()
  clearStoredLoginFlag()
  clearStoredLoginNotice()
  clearStoredLoginRedirect()
  store.resetState()
  router.replace('/login')
}
</script>

<style scoped>
/* Backdrop: fade */
.mobile-backdrop-enter-active,
.mobile-backdrop-leave-active {
  transition: opacity 0.28s ease;
}

.mobile-backdrop-enter-from,
.mobile-backdrop-leave-to {
  opacity: 0;
}

/* Panel: slide from left (iOS-like ease) */
.mobile-panel-enter-active,
.mobile-panel-leave-active {
  transition: transform 0.32s cubic-bezier(0.32, 0.72, 0, 1);
  will-change: transform;
}

.mobile-panel-enter-from,
.mobile-panel-leave-to {
  transform: translateX(-100%);
}

@media (prefers-reduced-motion: reduce) {
  .mobile-backdrop-enter-active,
  .mobile-backdrop-leave-active,
  .mobile-panel-enter-active,
  .mobile-panel-leave-active {
    transition-duration: 0.01ms;
  }
}
</style>
