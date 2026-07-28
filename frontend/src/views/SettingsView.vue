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
                  step="1024"
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

        <!-- 妙想接口 -->
        <section class="settings-section">
          <header class="settings-section__header">
            <div>
              <h3 class="settings-section__title">妙想接口</h3>
              <p class="settings-section__desc">东方财富妙想 OpenAPI（行情 / 资讯 / 模拟交易）</p>
            </div>
          </header>
          <UiField label="妙想密钥" help="访问东方财富妙想接口的 apikey。">
            <input
              v-model="settings.mx_api_key"
              type="password"
              placeholder="妙想接口 apikey"
              class="field-input max-w-xl"
            />
          </UiField>
        </section>

        <!-- 交易通知 -->
        <section class="settings-section">
          <header class="settings-section__header">
            <div>
              <h3 class="settings-section__title">交易通知</h3>
              <p class="settings-section__desc">交易执行结果通过 Telegram 推送</p>
            </div>
          </header>

          <div class="settings-card">
            <div class="settings-card__row">
              <div class="min-w-0">
                <p class="settings-card__title">启用 Telegram 通知</p>
                <p class="settings-card__hint">开启后，交易执行时向指定聊天推送通知。</p>
              </div>
              <UiToggle v-model="settings.tg_notify_trade_enabled" />
            </div>

            <div
              class="settings-card__body"
              :class="settings.tg_notify_trade_enabled ? '' : 'settings-card__body--muted'"
            >
              <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
                <UiField label="Bot Token" help="从 @BotFather 获取。">
                  <input
                    v-model="settings.tg_bot_token"
                    type="text"
                    placeholder="123456:ABC-DEF..."
                    class="field-input"
                    autocomplete="off"
                    spellcheck="false"
                  />
                </UiField>
                <UiField label="Chat ID" help="可通过 @userinfobot 查询。">
                  <input
                    v-model="settings.tg_chat_id"
                    type="text"
                    placeholder="-100xxxxxxxxxx"
                    class="field-input"
                    autocomplete="off"
                    spellcheck="false"
                  />
                </UiField>
              </div>
            </div>
          </div>
        </section>

        <!-- 交易约束：上下排列、统一卡片 -->
        <section class="settings-section">
          <header class="settings-section__header">
            <div>
              <h3 class="settings-section__title">交易约束</h3>
              <p class="settings-section__desc">限制可选市场与策略可用资金</p>
            </div>
          </header>

          <div class="space-y-3">
            <div class="settings-card">
              <div class="settings-card__head">
                <div class="min-w-0">
                  <p class="settings-card__title">选股范围</p>
                  <p class="settings-card__hint">
                    勾选允许选股与买入的市场；买入按代码硬拦截，卖出 / 撤单不受限。
                  </p>
                </div>
              </div>
              <div class="settings-card__body">
                <div class="market-grid" role="group" aria-label="选股范围">
                  <label
                    v-for="option in marketOptions"
                    :key="option.key"
                    class="market-chip"
                    :class="{
                      'market-chip--active': settings.allowed_markets.includes(option.key),
                      'market-chip--disabled': busy,
                    }"
                  >
                    <input
                      type="checkbox"
                      class="sr-only"
                      :checked="settings.allowed_markets.includes(option.key)"
                      :disabled="busy"
                      @change="toggleMarket(option.key, ($event.target as HTMLInputElement).checked)"
                    />
                    <span class="market-chip__check" aria-hidden="true">
                      <svg viewBox="0 0 16 16" class="size-3.5" fill="none">
                        <path
                          d="M3.5 8.5 6.5 11.5 12.5 4.5"
                          stroke="currentColor"
                          stroke-width="1.8"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                        />
                      </svg>
                    </span>
                    <span class="market-chip__label">{{ option.label }}</span>
                  </label>
                </div>
                <p class="settings-card__footer-hint">
                  默认仅上证 / 深证 A 股。至少保留一个市场。
                </p>
              </div>
            </div>

            <div class="settings-card">
              <div class="settings-card__row">
                <div class="min-w-0">
                  <p class="settings-card__title">资金封印</p>
                  <p class="settings-card__hint">
                    从模拟户中划出不可用于策略的资金；资产 / 可用资金 / 仓位与收益按「真实值 − 封印」投影，持仓明细不减。
                  </p>
                </div>
                <UiToggle v-model="settings.capital_seal_enabled" :disabled="busy" />
              </div>
              <div
                class="settings-card__body"
                :class="settings.capital_seal_enabled ? '' : 'settings-card__body--muted'"
              >
                <UiField label="封印金额（元）">
                  <input
                    v-model.number="settings.capital_seal_amount"
                    type="number"
                    min="0"
                    step="1000"
                    placeholder="例如 900000"
                    class="field-input max-w-sm"
                    :disabled="busy || !settings.capital_seal_enabled"
                  />
                </UiField>
              </div>
            </div>
          </div>
        </section>

        <!-- 系统提示词 -->
        <section class="settings-section settings-section--last">
          <header class="settings-section__header">
            <div>
              <h3 class="settings-section__title">系统提示词</h3>
              <p class="settings-section__desc">定义 AI 角色、目标与决策风格</p>
            </div>
          </header>
          <UiField help="建议写清角色定位、收益目标、风控偏好与输出要求。">
            <textarea
              v-model="settings.system_prompt"
              rows="12"
              class="field-input field-input--textarea"
            />
          </UiField>
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
import { onMounted, ref } from 'vue'
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
