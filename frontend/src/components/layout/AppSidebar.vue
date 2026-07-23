<template>
  <aside
    class="flex h-full w-full flex-col border-r border-separator"
    :class="surface === 'solid' ? 'bg-white' : 'glass-nav'"
  >
    <!-- Brand -->
    <div class="flex items-center gap-3 px-5 pb-5 pt-6">
      <img
        src="/aniu.ico"
        alt="Aniu"
        class="size-11 shrink-0 rounded-[14px] shadow-md"
      />
      <div class="min-w-0">
        <div class="flex items-baseline gap-2">
          <strong class="text-[22px] font-semibold leading-none tracking-tight text-label">
            Aniu
          </strong>
          <span class="text-caption font-medium text-label-tertiary">v{{ appVersion }}</span>
        </div>
        <p class="mt-1 truncate text-footnote text-label-tertiary">AI 模拟交易</p>
      </div>
    </div>

    <!-- Nav -->
    <nav class="flex-1 overflow-y-auto px-3 pb-4" aria-label="主导航">
      <p class="mb-2 px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-label-quaternary">
        菜单
      </p>
      <ul class="flex flex-col gap-1">
        <li v-for="item in appNavigation" :key="item.id">
          <router-link
            :to="item.path"
            class="group flex items-center gap-3 rounded-[12px] px-3 py-[11px] text-callout font-medium leading-none transition-colors duration-150"
            :class="
              isActive(item.path)
                ? 'bg-accent-soft text-accent-text shadow-sm'
                : 'text-label-secondary hover:bg-hover hover:text-label'
            "
            @click="emit('navigate')"
          >
            <span
              class="inline-flex size-8 shrink-0 items-center justify-center rounded-[10px] leading-none transition-colors"
              :class="
                isActive(item.path)
                  ? 'bg-accent text-on-accent'
                  : 'bg-fill text-label-secondary group-hover:text-label'
              "
            >
              <NavIcon :name="item.icon" />
            </span>
            <span class="truncate leading-normal">{{ item.name }}</span>
          </router-link>
        </li>
      </ul>
    </nav>

    <!-- Footer actions -->
    <div class="space-y-2 border-t border-separator px-3 py-4">
      <a
        class="flex h-10 items-center gap-2.5 rounded-[12px] px-3 text-footnote font-semibold text-label-secondary transition-colors hover:bg-hover hover:text-label"
        href="https://github.com/AnacondaKC/Aniu"
        target="_blank"
        rel="noopener noreferrer"
      >
        <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M12 .5C5.65.5.5 5.65.5 12a11.5 11.5 0 0 0 7.86 10.92c.58.1.79-.25.79-.56v-2.16c-3.2.7-3.88-1.36-3.88-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.02 1.75 2.67 1.24 3.32.95.1-.74.4-1.24.72-1.53-2.56-.29-5.24-1.28-5.24-5.7 0-1.26.45-2.28 1.18-3.08-.12-.29-.51-1.47.11-3.05 0 0 .97-.31 3.17 1.18a11.02 11.02 0 0 1 5.78 0c2.19-1.49 3.16-1.18 3.16-1.18.63 1.58.24 2.76.12 3.05.74.8 1.18 1.82 1.18 3.08 0 4.43-2.69 5.41-5.25 5.69.41.35.78 1.03.78 2.08v3.08c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" />
        </svg>
        GitHub
      </a>
      <button
        type="button"
        class="flex h-10 w-full items-center gap-2.5 rounded-[12px] px-3 text-footnote font-semibold text-danger-text transition-colors hover:bg-danger-soft"
        @click="emit('logout')"
      >
        <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
          <path d="M16 17l5-5-5-5" />
          <path d="M21 12H9" />
        </svg>
        退出登录
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import appPackage from '../../../package.json'
import { appNavigation } from '@/config/navigation'
import NavIcon from './NavIcon.vue'

withDefaults(
  defineProps<{
    /**
     * glass — desktop frosted bar (content sits beside, not under)
     * solid — mobile drawer (must fully cover page content underneath)
     */
    surface?: 'glass' | 'solid'
  }>(),
  { surface: 'glass' },
)

const emit = defineEmits<{
  navigate: []
  logout: []
}>()

const route = useRoute()
const appVersion = appPackage.version

function isActive(path: string) {
  return route.path === path || route.path.startsWith(path + '/')
}
</script>
