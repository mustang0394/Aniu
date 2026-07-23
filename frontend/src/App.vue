<template>
  <router-view v-if="isLoginPage" />
  <AppShell v-else>
    <router-view />
  </AppShell>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppShell from '@/components/layout/AppShell.vue'

const route = useRoute()
const isLoginPage = computed(() => route.path === '/login')
</script>
