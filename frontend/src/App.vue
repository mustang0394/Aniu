<template>
  <router-view v-if="isLoginPage" />
  <AppShell v-else>
    <router-view />
  </AppShell>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'
import { useAppStore } from '@/stores/legacy'

const route = useRoute()
const store = useAppStore()
const { appDisplayName } = storeToRefs(store)
const isLoginPage = computed(() => route.path === '/login')

// Keep the browser tab title in sync with the configured system name.
watch(
  appDisplayName,
  (name) => {
    document.title = name
  },
  { immediate: true },
)
</script>
