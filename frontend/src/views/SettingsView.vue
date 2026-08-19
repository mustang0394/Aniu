<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader
      title="功能设置"
      kicker="Configuration"
      description="大模型、妙想接口、通知与技能管理"
    />

    <UiPanel title="核心配置" kicker="Settings">
      <div class="settings-stack">
        <!-- 基本信息 -->
        <section class="settings-section">
          <header class="settings-section__header">
            <div>
              <h3 class="settings-section__title">基本信息</h3>
              <p class="settings-section__desc">产品对外展示名称</p>
            </div>
          </header>
          <UiField label="系统名称" help="显示在侧边栏、浏览器标签页、聊天助手等位置，默认 Aniu。">
            <input
              v-model="settings.app_display_name"
              maxlength="64"
              placeholder="Aniu"
              class="field-input max-w-md"
            />
          </UiField>
        </section>

        <!-- 大模型 -->
        <section class="settings-section">
          <header class="settings-section__header">
            <div>
              <h3 class="settings-section__title">大模型</h3>
              <p class="settings-section__desc">OpenAI 兼容接口与推理参数</p>
            </div>
          </header>

          <div class="space-y-4">
            <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
              <UiField label="Base URL" help="大模型 API 基础地址，填写 OpenAI 兼容端点。">
                <input
                  v-model="settings.llm_base_url"
                  placeholder="https://api.openai.com/v1"
                  class="field-input"
                />
              </UiField>
              <UiField label="API Key" help="访问大模型 API 的密钥。">
                <input
                  v-model="settings.llm_api_key"
                  type="password"
                  placeholder="sk-..."
                  class="field-input"
                />
              </UiField>
            </div>

            <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <UiField label="模型名" help="例如 gpt-4o-mini、o3-mini。">
                <input v-model="settings.llm_model" class="field-input" />
              </UiField>
              <UiField
                label="思考等级"
                help="请求参数 reasoning_effort；留空则不传。"
              >
                <input
                  v-model="settings.llm_reasoning_effort"
                  type="text"
                  placeholder="low / medium / high"
                  class="field-input"
                  autocomplete="off"
                  spellcheck="false"
                />
              </UiField>
              <UiField
                label="最大上下文"
                help="默认 128K；后端按 85% 触发自动化会话压缩。"
              >
                <input
                  v-model.number="settings.automation_context_window_tokens"
                  type="number"
                  min="4096"
                  class="field-input"
                />
              </UiField>
              <UiField
                label="请求重试次数"
                help="单次大模型调用失败后的额外重试次数，默认 3；设为 0 关闭。适用于超时、限流与 5xx。"
              >
                <input
                  v-model.number="settings.llm_max_retries"
                  type="number"
                  min="0"
                  max="10"
                  step="1"
                  class="field-input"
                />
              </UiField>
            </div>

            <div class="settings-card settings-card--flat">
              <div class="settings-card__row">
                <div class="min-w-0">
                  <p class="settings-card__title">回传思考内容</p>
                  <p class="settings-card__hint">
                    将推理模型返回的 thinking 在下次请求中回传，避免部分模型报 400。
                  </p>
                </div>
                <UiToggle v-model="settings.llm_enable_reasoning_content_echo" />
              </div>
            </div>
          </div>
        </section>

        <!-- UZI 妙想密钥（全局） -->
        <section class="settings-section">
          <header class="settings-section__header">
            <div>
              <h3 class="settings-section__title">UZI 妙想密钥</h3>
              <p class="settings-section__desc">UZI 深度报告数据采集使用的全局妙想 Key（不随交易账户变化）</p>
            </div>
          </header>
          <UiField label="妙想密钥" help="访问东方财富妙想接口的 apikey，仅用于 UZI 报告生成。">
            <input
              :value="uziMxKey"
              type="password"
              placeholder="妙想接口 apikey"
              class="field-input max-w-xl"
              @input="handleUziKeyInput"
            />
          </UiField>
          <p class="mt-2 text-footnote text-label-tertiary">
            各交易账户的妙想 Key 请前往「交易账户」页面配置。
          </p>
        </section>






      </div>

      <div
        v-if="errorMessage"
        class="mt-5 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
        role="alert"
      >
        {{ errorMessage }}
      </div>

      <div class="mt-6 flex justify-end border-t border-separator pt-5">
        <UiButton variant="primary" :loading="busy" :disabled="busy" @click="saveSettings">
          保存设置
        </UiButton>
      </div>
    </UiPanel>

    <!-- Skills -->
    <UiPanel title="技能管理" kicker="Skills">
      <template #actions>
        <UiButton
          variant="tinted"
          size="sm"
          :loading="skillsBusy"
          :disabled="skillsBusy"
          @click="reloadSkills"
        >
          重新扫描
        </UiButton>
      </template>

      <div class="mb-5 grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div class="rounded-[14px] border border-separator bg-fill/40 p-4">
          <p class="m-0 text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">已安装技能</p>
          <p class="m-0 mt-1 text-title-2 font-semibold tabular-nums text-label">总数 {{ installedOverview.total }}</p>
          <p class="m-0 mt-2 text-caption text-label-secondary">
            运行时 {{ installedOverview.runtime }} · 标准 {{ installedOverview.standard }}
          </p>
        </div>
        <div class="rounded-[14px] border border-separator bg-fill/40 p-4">
          <p class="m-0 text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">已启用技能</p>
          <p class="m-0 mt-1 text-title-2 font-semibold tabular-nums text-label">总数 {{ enabledOverview.total }}</p>
          <p class="m-0 mt-2 text-caption text-label-secondary">
            运行时 {{ enabledOverview.runtime }} · 标准 {{ enabledOverview.standard }}
          </p>
        </div>
        <div class="rounded-[14px] border border-separator bg-fill/40 p-4 lg:col-span-1">
          <p class="m-0 mb-2 text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">
            导入技能
          </p>
          <div class="flex flex-col gap-2 sm:flex-row">
            <div class="flex min-w-0 flex-1 items-center gap-2 rounded-[12px] border border-separator-strong bg-card-solid px-2">
              <input
                v-model="importInput"
                placeholder="SkillHub 链接或技能名称"
                class="h-10 min-w-0 flex-1 border-0 bg-transparent text-body text-label outline-none placeholder:text-label-quaternary"
                :disabled="skillsBusy"
                @input="handleImportInput"
              />
              <UiButton
                variant="ghost"
                size="sm"
                :disabled="skillsBusy"
                @click="openImportFileDialog"
              >
                {{ selectedArchive ? '更换文件' : '添加文件' }}
              </UiButton>
              <input
                ref="skillArchiveInputRef"
                class="hidden"
                type="file"
                accept=".zip,application/zip"
                :disabled="skillsBusy"
                @change="handleImportFileChange"
              />
            </div>
            <UiButton
              variant="primary"
              :loading="skillsBusy"
              :disabled="skillsBusy"
              @click="importSkill"
            >
              导入技能
            </UiButton>
          </div>
          <p v-if="selectedArchive" class="m-0 mt-2 text-caption text-label-secondary">
            已选择文件：{{ selectedArchive.name }}
          </p>
        </div>
      </div>

      <div
        v-if="skillsErrorMessage"
        class="mb-4 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
        role="alert"
      >
        {{ skillsErrorMessage }}
      </div>

      <div v-if="skills.length" class="flex flex-col gap-3">
        <article
          v-for="skill in skills"
          :key="skill.id"
          class="flex flex-col gap-3 rounded-[16px] border border-separator bg-card-solid/80 p-4 sm:flex-row sm:items-stretch sm:justify-between"
        >
          <div class="min-w-0 flex-1">
            <div class="mb-2 flex flex-wrap items-center gap-2">
              <strong class="text-callout font-semibold text-label">{{ skill.name }}</strong>
              <UiBadge :tone="skill.role === 'runtime' ? 'accent' : skill.source === 'builtin' ? 'neutral' : 'analysis'">
                {{ skill.role === 'runtime' ? '运行时技能' : skill.source === 'builtin' ? '内置技能' : '用户技能' }}
              </UiBadge>
            </div>
            <p class="m-0 text-footnote leading-relaxed text-label-secondary">
              {{ skill.description || '暂无技能描述。' }}
            </p>
          </div>
          <div class="flex shrink-0 items-center gap-2 sm:flex-col sm:items-end sm:justify-between">
            <UiButton
              v-if="skill.can_delete"
              variant="danger-soft"
              size="sm"
              :disabled="skillsBusy"
              @click="deleteSkill(skill)"
            >
              删除
            </UiButton>
            <span
              v-else
              class="inline-flex h-8 items-center rounded-[10px] px-3 text-footnote text-label-quaternary"
            >
              不可删除
            </span>
            <UiToggle
              :model-value="skill.enabled"
              :disabled="skillsBusy || !canToggleSkill(skill)"
              @update:model-value="toggleSkill(skill)"
            />
          </div>
        </article>
      </div>
      <UiEmpty v-else title="暂无技能" description="当前还没有可展示的技能。" />
    </UiPanel>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { useSkillManager } from '@/composables/useSkillManager'
import { useAppStore } from '@/stores/legacy'
import type { MarketKey, SkillListItem } from '@/types'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiField from '@/components/ui/UiField.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiPanel from '@/components/ui/UiPanel.vue'
import UiToggle from '@/components/ui/UiToggle.vue'

const marketOptions: { key: MarketKey; label: string }[] = [
  { key: 'sh_main', label: '上证A股' },
  { key: 'sz_main', label: '深证A股' },
  { key: 'chinext', label: '创业板' },
  { key: 'star', label: '科创板' },
  { key: 'bse', label: '北交所' },
]

const store = useAppStore()
const { settings, busy, errorMessage } = storeToRefs(store)
const { saveSettings } = store

const uziMxKey = computed(() => {
  return settings.value.uzi_mx_api_key ?? settings.value.mx_api_key ?? ''
})

function handleUziKeyInput(event: Event) {
  settings.value.uzi_mx_api_key = (event.target as HTMLInputElement).value
}
const {
  skills,
  importInput,
  selectedArchive,
  busy: skillsBusy,
  errorMessage: skillsErrorMessage,
  installedOverview,
  enabledOverview,
  loadSkills,
  setImportFile,
  importSkill: submitSkillImport,
  reloadSkills: reloadSkillList,
  toggleSkill: toggleManagedSkill,
  deleteSkill: deleteManagedSkill,
} = useSkillManager()
const skillArchiveInputRef = ref<HTMLInputElement | null>(null)

function toggleMarket(key: MarketKey, checked: boolean) {
  const current = new Set(settings.value.allowed_markets)
  if (checked) {
    current.add(key)
  } else {
    current.delete(key)
  }
  if (current.size === 0) return
  const order: MarketKey[] = ['sh_main', 'sz_main', 'chinext', 'star', 'bse']
  settings.value.allowed_markets = order.filter((item) => current.has(item))
}

function openImportFileDialog() {
  if (skillArchiveInputRef.value) {
    skillArchiveInputRef.value.value = ''
    skillArchiveInputRef.value.click()
  }
}

function resetNativeSkillInput() {
  if (skillArchiveInputRef.value) {
    skillArchiveInputRef.value.value = ''
  }
}

function handleImportInput() {
  if (!importInput.value.trim()) return
  setImportFile(null)
  resetNativeSkillInput()
}

function handleImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement | null
  const file = input?.files?.[0] ?? null
  setImportFile(file)
}

async function importSkill() {
  const imported = await submitSkillImport()
  if (imported) resetNativeSkillInput()
}

async function reloadSkills() {
  await reloadSkillList()
}

async function toggleSkill(skill: SkillListItem) {
  if (!canToggleSkill(skill)) return
  await toggleManagedSkill(skill)
}

async function deleteSkill(skill: SkillListItem) {
  if (!skill.can_delete) return
  await deleteManagedSkill(skill)
}

function canToggleSkill(skill: SkillListItem) {
  return skill.can_disable
}

onMounted(async () => {
  try {
    await Promise.all([
      store.loadSettings(),
      loadSkills(),
    ])
  } catch (error) {
    errorMessage.value = (error as Error).message
  }
})
</script>

<style scoped>
@reference "../styles/tailwind.css";

.settings-stack {
  @apply flex flex-col;
}

.settings-section {
  @apply border-b border-separator py-7 first:pt-0;
}

.settings-section--last {
  @apply border-b-0 pb-1;
}

.settings-section__header {
  @apply mb-4 flex items-start justify-between gap-3;
}

.settings-section__title {
  @apply m-0 text-title-3 font-semibold tracking-tight text-label;
}

.settings-section__desc {
  @apply m-0 mt-1 text-footnote text-label-secondary;
}

/* 统一子卡片：选股范围 / 资金封印 / Telegram / 开关行 同一视觉语言 */
.settings-card {
  @apply overflow-hidden rounded-[16px] border border-separator bg-fill/35;
}

.settings-card--flat {
  @apply bg-fill/40;
}

.settings-card__head,
.settings-card__row {
  @apply flex items-start justify-between gap-4 px-4 py-3.5;
}

.settings-card__title {
  @apply m-0 text-footnote font-semibold text-label;
}

.settings-card__hint {
  @apply m-0 mt-1 text-caption leading-snug text-label-tertiary;
}

.settings-card__body {
  @apply border-t border-separator/80 px-4 py-3.5 transition-opacity;
}

.settings-card__body--muted {
  @apply opacity-45;
}

.settings-card__footer-hint {
  @apply m-0 mt-3 text-caption leading-snug text-label-tertiary;
}

.market-grid {
  @apply grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5;
}

.market-chip {
  @apply flex cursor-pointer items-center gap-2 rounded-[12px] border border-separator bg-card-solid/70 px-3 py-2.5 text-footnote font-medium text-label transition-all;
  @apply hover:border-separator-strong hover:bg-hover;
}

.market-chip--active {
  @apply border-accent/35 bg-accent-soft text-label shadow-sm;
}

.market-chip--disabled {
  @apply cursor-not-allowed opacity-50;
}

.market-chip__check {
  @apply flex size-5 shrink-0 items-center justify-center rounded-full border border-separator-strong text-transparent transition-colors;
}

.market-chip--active .market-chip__check {
  @apply border-accent bg-accent text-white;
}

.market-chip__label {
  @apply min-w-0 truncate;
}

.field-input {
  @apply h-11 w-full rounded-[12px] border border-separator-strong bg-card-solid/80 px-3.5 text-body text-label outline-none transition-colors;
  @apply placeholder:text-label-quaternary;
  @apply focus:border-accent focus:bg-accent-soft/30 focus:ring-2 focus:ring-accent-ring;
  @apply disabled:cursor-not-allowed disabled:opacity-50;
}

.field-input--textarea {
  @apply h-auto min-h-[220px] py-3 font-mono text-[13px] leading-relaxed sm:min-h-[280px];
}

textarea.field-input {
  @apply h-auto;
}
</style>
