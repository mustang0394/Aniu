<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader
      title="功能设置"
      kicker="Configuration"
      description="大模型、妙想接口、通知与技能管理"
    />

    <UiPanel title="核心配置" kicker="Settings">
      <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <!-- Left column -->
        <div class="flex flex-col gap-4">
          <UiField label="Base URL" help="大模型 API 的基础地址，默认可填写 OpenAI 兼容地址。">
            <input
              v-model="settings.llm_base_url"
              placeholder="https://api.openai.com/v1"
              class="field-input"
            />
          </UiField>

          <UiField label="API Key" help="用于访问大模型 API 的密钥。">
            <input
              v-model="settings.llm_api_key"
              type="password"
              placeholder="sk-..."
              class="field-input"
            />
          </UiField>

          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <UiField label="模型名" help="要使用的大模型名称，例如 gpt-4o-mini。">
              <input v-model="settings.llm_model" class="field-input" />
            </UiField>
            <UiField
              label="最大上下文"
              help="默认 128K。后端会按该值的 85% 作为自动化会话上下文压缩触发预算。"
            >
              <input
                v-model.number="settings.automation_context_window_tokens"
                type="number"
                min="4096"
                step="1024"
                class="field-input"
              />
            </UiField>
          </div>

          <div class="flex items-center justify-between gap-4 rounded-[12px] border border-separator bg-fill/40 px-3.5 py-3">
            <div class="min-w-0">
              <p class="m-0 text-footnote font-semibold text-label">回传思考内容</p>
              <p class="m-0 mt-0.5 text-caption text-label-tertiary">
                启用后，推理模型返回的 thinking 内容会在下次请求时回传，避免部分模型报 400。
              </p>
            </div>
            <UiToggle v-model="settings.llm_enable_reasoning_content_echo" />
          </div>

          <UiField label="妙想密钥" help="用于访问东方财富妙想接口的密钥。">
            <input
              v-model="settings.mx_api_key"
              type="password"
              placeholder="妙想接口 apikey"
              class="field-input"
            />
          </UiField>

          <div class="flex items-center justify-between gap-4 rounded-[12px] border border-separator bg-fill/40 px-3.5 py-3">
            <div class="min-w-0">
              <p class="m-0 text-footnote font-semibold text-label">交易通知 (Telegram)</p>
              <p class="m-0 mt-0.5 text-caption text-label-tertiary">启用后，交易执行时将向 Telegram 推送通知。</p>
            </div>
            <UiToggle v-model="settings.tg_notify_trade_enabled" />
          </div>

          <UiField label="Bot Token" help="Telegram Bot 的 API Token，从 @BotFather 获取。">
            <input
              v-model="settings.tg_bot_token"
              type="password"
              placeholder="123456:ABC-DEF..."
              class="field-input"
            />
          </UiField>

          <UiField label="Chat ID" help="接收通知的 Telegram 聊天 ID，可通过 @userinfobot 查询。">
            <input
              v-model="settings.tg_chat_id"
              type="password"
              placeholder="-100xxxxxxxxxx"
              class="field-input"
            />
          </UiField>

          <div>
            <p class="mb-2 text-footnote font-semibold text-label">选股范围</p>
            <div class="grid grid-cols-2 gap-2 sm:grid-cols-3" role="group" aria-label="选股范围">
              <label
                v-for="option in marketOptions"
                :key="option.key"
                class="flex cursor-pointer items-center gap-2 rounded-[12px] border border-separator bg-fill/30 px-3 py-2.5 text-footnote font-medium text-label transition-colors hover:bg-hover has-[:checked]:border-accent/30 has-[:checked]:bg-accent-soft"
              >
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="settings.allowed_markets.includes(option.key)"
                  :disabled="busy"
                  @change="toggleMarket(option.key, ($event.target as HTMLInputElement).checked)"
                />
                <span>{{ option.label }}</span>
              </label>
            </div>
            <p class="mt-2 mb-0 text-caption text-label-tertiary">
              勾选允许选股与买入的市场。买入会按代码硬拦截；卖出/撤单不受限制。默认仅上证/深证 A 股。
            </p>
          </div>

          <div class="flex items-center justify-between gap-4 rounded-[12px] border border-separator bg-fill/40 px-3.5 py-3">
            <div class="min-w-0">
              <p class="m-0 text-footnote font-semibold text-label">资金封印</p>
            </div>
            <UiToggle v-model="settings.capital_seal_enabled" :disabled="busy" />
          </div>

          <UiField
            label="封印金额（元）"
            help="从模拟户中划出不可用于策略的资金。工具返回的总资产/可用资金/仓位与收益统计均按「真实值 − 封印」投影；持仓明细不减。"
          >
            <input
              v-model.number="settings.capital_seal_amount"
              type="number"
              min="0"
              step="1000"
              placeholder="例如 900000"
              class="field-input"
              :disabled="busy || !settings.capital_seal_enabled"
            />
          </UiField>
        </div>

        <!-- Right column -->
        <div>
          <UiField label="系统提示词" help="指导大模型行为的系统提示词，会影响 AI 的分析和决策方式。">
            <textarea
              v-model="settings.system_prompt"
              rows="18"
              class="field-input min-h-[280px] py-3"
            />
          </UiField>
        </div>
      </div>

      <div
        v-if="errorMessage"
        class="mt-4 rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
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

.field-input {
  @apply h-11 w-full rounded-[12px] border border-separator-strong bg-fill px-3.5 text-body text-label outline-none transition-colors;
  @apply placeholder:text-label-quaternary;
  @apply focus:border-accent focus:bg-accent-soft/40 focus:ring-2 focus:ring-accent-ring;
  @apply disabled:cursor-not-allowed disabled:opacity-50;
}

textarea.field-input {
  @apply h-auto;
}
</style>
