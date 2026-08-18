<template>
  <div class="space-y-5">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-title font-bold tracking-tight text-label">交易账户</h1>
        <p class="mt-1 text-caption text-label-tertiary">
          每个账户绑定独立妙想 Key，拥有独立的提示词、市场范围、Skills、定时任务、自动化会话与运行历史。
        </p>
      </div>
      <button
        type="button"
        class="btn-primary"
        :disabled="creating"
        @click="startCreate"
      >
        {{ creating ? '创建中…' : '＋ 新建账户' }}
      </button>
    </div>

    <div v-if="error" class="rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text" role="alert">
      {{ error }}
    </div>

    <div v-if="store.loadingAccounts && store.accounts.length === 0" class="py-12 text-center text-caption text-label-tertiary">
      正在加载账户…
    </div>

    <div v-else-if="store.accounts.length === 0" class="py-12 text-center text-caption text-label-tertiary">
      暂无账户，点击右上角「新建账户」开始。
    </div>

    <div v-else class="space-y-4">
      <div
        v-for="account in store.accounts"
        :key="account.id"
        class="rounded-2xl border border-separator bg-card p-5 shadow-sm transition-shadow hover:shadow-md"
      >
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex min-w-0 items-center gap-3">
            <div
              class="flex size-10 shrink-0 items-center justify-center rounded-[12px] text-base font-semibold"
              :class="account.archived ? 'bg-fill text-label-tertiary' : 'bg-accent-soft text-accent-text'"
            >
              {{ account.name.slice(0, 1) }}
            </div>
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <strong class="truncate text-body font-semibold text-label">{{ account.name }}</strong>
                <span v-if="account.archived" class="rounded-full bg-fill px-2 py-0.5 text-[11px] font-medium text-label-tertiary">已归档</span>
                <span v-else-if="!account.enabled" class="rounded-full bg-warning-soft px-2 py-0.5 text-[11px] font-medium text-warning-text">已停用</span>
                <span v-else class="rounded-full bg-success-soft px-2 py-0.5 text-[11px] font-medium text-success-text">运行中</span>
                <span v-if="account.slug === 'default'" class="rounded-full bg-fill px-2 py-0.5 text-[11px] font-medium text-label-tertiary">默认账户</span>
              </div>
              <p class="mt-1 truncate text-footnote text-label-tertiary">
                <span class="font-mono">{{ account.slug }}</span>
                <span class="mx-1.5">·</span>
                <span :class="account.has_mx_api_key ? 'text-success-text' : 'text-warning-text'">
                  {{ account.has_mx_api_key ? '已配置妙想 Key' : '未配置妙想 Key' }}
                </span>
                <span class="mx-1.5">·</span>
                LLM：{{ llmSourceText(account) }}
              </p>
              <p class="mt-0.5 truncate text-footnote text-label-tertiary">
                禁用 Skills：{{ account.disabled_skill_ids.length > 0 ? account.disabled_skill_ids.join('、') : '无' }}
                <span v-if="account.capital_seal_enabled" class="mx-1.5">·</span>
                <span v-if="account.capital_seal_enabled">资金封印 ¥{{ formatAmount(account.capital_seal_amount) }}</span>
              </p>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <button type="button" class="btn-secondary" :disabled="account.archived" @click="openEdit(account)">编辑</button>
            <button type="button" class="btn-secondary" :disabled="account.archived" @click="openSkills(account)">Skills</button>
            <button type="button" class="btn-secondary" :disabled="account.archived" @click="testMx(account)">测试妙想</button>
            <button type="button" class="btn-secondary" :disabled="account.archived" @click="testLlm(account)">测试LLM</button>
            <template v-if="!account.archived && account.slug !== 'default'">
              <button type="button" class="btn-secondary text-warning-text" @click="archive(account)">归档</button>
            </template>
            <template v-else-if="account.archived">
              <button type="button" class="btn-secondary" @click="restore(account)">恢复</button>
            </template>
          </div>
        </div>

        <div v-if="testResults[account.id]" class="mt-3 rounded-[10px] border border-separator bg-fill px-3 py-2 text-footnote" :class="testResults[account.id].ok ? 'text-success-text' : 'text-danger-text'">
          {{ testResults[account.id].message }}
        </div>
      </div>
    </div>

    <!-- 编辑/新建抽屉 -->
    <div v-if="showEditor" class="fixed inset-0 z-50 flex justify-end bg-black/40" @click.self="closeEditor">
      <div class="h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-2xl">
        <div class="mb-5 flex items-center justify-between">
          <h2 class="text-title font-bold text-label">{{ isCreating ? '新建账户' : `编辑：${String(draft.name ?? '') || accountDraft?.name || ''}` }}</h2>
          <button type="button" class="rounded-[10px] px-2 py-1 text-caption font-medium text-label-tertiary hover:bg-hover" @click="closeEditor">✕ 关闭</button>
        </div>

        <div v-if="editorError" class="mb-4 rounded-[10px] border border-danger/25 bg-danger-soft px-3 py-2 text-footnote text-danger-text" role="alert">
          {{ editorError }}
        </div>

        <form class="space-y-4" @submit.prevent="save">
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label class="block">
              <span class="mb-1 block text-footnote font-medium text-label-secondary">账户名称 *</span>
              <input :value="String(draft.name ?? '')" @input="draft.name = ($event.target as HTMLInputElement).value" type="text" required class="input" placeholder="例如：趋势账户" />
            </label>
            <label v-if="isCreating" class="block">
              <span class="mb-1 block text-footnote font-medium text-label-secondary">Slug（唯一标识，可留空自动生成）</span>
              <input :value="String(draft.slug ?? '')" @input="draft.slug = ($event.target as HTMLInputElement).value" type="text" class="input" placeholder="trend" />
            </label>
          </div>

          <section class="rounded-[14px] border border-separator p-4">
            <h3 class="mb-2 text-callout font-semibold text-label">妙想 Key</h3>
            <p class="mb-2 text-footnote text-label-tertiary">该账户所有行情、持仓、订单与模拟交易均使用此 Key。</p>
            <input v-model="draft.mx_api_key" type="password" class="input" autocomplete="off" placeholder="留空保持不变；输入空字符串可清除" />
          </section>

          <section class="rounded-[14px] border border-separator p-4">
            <div class="mb-3 flex items-center justify-between">
              <h3 class="text-callout font-semibold text-label">独立大模型（可选）</h3>
              <label class="flex items-center gap-2 text-footnote font-medium text-label-secondary">
                <input v-model="draft.account_llm_enabled" type="checkbox" class="size-4 accent-accent" />
                启用账户级大模型
              </label>
            </div>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">Base URL</span>
                <input v-model="draft.llm_base_url" type="text" class="input" placeholder="https://api.example.com/v1" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">API Key</span>
                <input v-model="draft.llm_api_key" type="password" class="input" autocomplete="off" placeholder="sk-…" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">模型</span>
                <input v-model="draft.llm_model" type="text" class="input" placeholder="gpt-4o-mini" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">推理强度</span>
                <input v-model="draft.llm_reasoning_effort" type="text" class="input" placeholder="low / medium / high" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">最大重试次数</span>
                <input v-model.number="draft.llm_max_retries" type="number" min="0" max="10" class="input" />
              </label>
            </div>
            <p v-if="draft.account_llm_enabled" class="mt-2 text-footnote text-warning-text">
              账户配置必须 Base URL、Key、模型三者齐全才生效；不完整时整体回退全局大模型。
            </p>
          </section>

          <section class="rounded-[14px] border border-separator p-4">
            <h3 class="mb-3 text-callout font-semibold text-label">交易策略</h3>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label class="block sm:col-span-2">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">系统提示词</span>
                <textarea :value="String(draft.system_prompt ?? '')" rows="4" class="input font-mono text-footnote" @input="draft.system_prompt = ($event.target as HTMLTextAreaElement).value" />
              </label>
              <label class="block sm:col-span-2">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">分析师提示词</span>
                <textarea :value="String(draft.analyst_prompt ?? '')" rows="2" class="input" @input="draft.analyst_prompt = ($event.target as HTMLTextAreaElement).value" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">市场查询</span>
                <input v-model="draft.market_query" type="text" class="input" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">资讯查询</span>
                <input v-model="draft.news_query" type="text" class="input" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">选股查询</span>
                <input v-model="draft.screener_query" type="text" class="input" />
              </label>
              <label class="block">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">最大动作数</span>
                <input v-model.number="draft.max_actions" type="number" min="1" max="20" class="input" />
              </label>
            </div>
            <div class="mt-3 flex flex-wrap items-center gap-4">
              <span class="text-footnote font-medium text-label-secondary">允许市场：</span>
              <label v-for="market in marketOptions" :key="market.key" class="flex items-center gap-1.5 text-footnote text-label-secondary">
                <input v-model="draft.allowed_markets" type="checkbox" :value="market.key" class="size-4 accent-accent" />
                {{ market.label }}
              </label>
              <label class="ml-auto flex items-center gap-2 text-footnote text-label-secondary">
                <input v-model="draft.trade_enabled" type="checkbox" class="size-4 accent-accent" />
                允许交易
              </label>
            </div>
          </section>

          <section class="rounded-[14px] border border-separator p-4">
            <h3 class="mb-3 text-callout font-semibold text-label">风控与通知</h3>
            <div class="space-y-3">
              <label class="flex items-center gap-2 text-footnote font-medium text-label-secondary">
                <input v-model="draft.capital_seal_enabled" type="checkbox" class="size-4 accent-accent" />
                启用资金封印
              </label>
              <label v-if="draft.capital_seal_enabled" class="block max-w-xs">
                <span class="mb-1 block text-footnote font-medium text-label-secondary">封印金额（元）</span>
                <input v-model.number="draft.capital_seal_amount" type="number" min="0" step="1000" class="input" />
              </label>
              <label class="flex items-center gap-2 text-footnote font-medium text-label-secondary">
                <input v-model="draft.tg_notify_trade_enabled" type="checkbox" class="size-4 accent-accent" />
                交易后发送 Telegram 通知
              </label>
            </div>
          </section>

          <div class="flex justify-end gap-2 pt-1">
            <button type="button" class="btn-secondary" @click="closeEditor">取消</button>
            <button type="submit" class="btn-primary" :disabled="saving">
              {{ saving ? (isCreating ? '创建中…' : '保存中…') : (isCreating ? '创建账户' : '保存修改') }}
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- Skills 抽屉 -->
    <div v-if="showSkills" class="fixed inset-0 z-50 flex justify-end bg-black/40" @click.self="closeSkills">
      <div class="h-full w-full max-w-xl overflow-y-auto bg-white p-6 shadow-2xl">
        <div class="mb-5 flex items-center justify-between">
          <h2 class="text-title font-bold text-label">Skills：{{ skillsAccount?.name }}</h2>
          <button type="button" class="rounded-[10px] px-2 py-1 text-caption font-medium text-label-tertiary hover:bg-hover" @click="closeSkills">✕ 关闭</button>
        </div>
        <p class="mb-4 text-footnote text-label-tertiary">
          账户 Skill 启用状态与全局目录独立；全局硬禁用的技能无法在账户层重新启用。
        </p>
        <div v-if="skillsLoading" class="py-8 text-center text-caption text-label-tertiary">加载中…</div>
        <div v-else class="space-y-2">
          <div
            v-for="skill in skillsList?.global_available ?? []"
            :key="skill.id"
            class="flex items-center justify-between rounded-[10px] border border-separator px-3 py-2.5"
          >
            <div class="min-w-0">
              <p class="text-callout font-medium text-label">{{ skill.name }}</p>
              <p class="mt-0.5 text-footnote text-label-tertiary">
                {{ skill.id }}
                <span v-if="skill.always_enabled" class="ml-1 text-success-text">（系统运行时，始终启用）</span>
                <span v-else-if="skill.global_disabled" class="ml-1 text-danger-text">（全局硬禁用）</span>
              </p>
            </div>
            <input
              type="checkbox"
              class="size-4 accent-accent"
              :disabled="skill.always_enabled || skill.global_disabled"
              :checked="skill.effective_enabled"
              @change="toggleSkill(skill.id)"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { api } from '@/services/api'
import type {
  AccountLlmTestResult,
  AccountMxTestResult,
  AccountSkillList,
  TradingAccount,
  TradingAccountPayload,
} from '@/types'
import { useTradingAccountsStore } from '@/stores/tradingAccounts'

const store = useTradingAccountsStore()
const { accounts } = storeToRefs(store)

const creating = ref(false)
const showEditor = ref(false)
const isCreating = ref(false)
const editingId = ref<number | null>(null)
const saving = ref(false)
const error = ref('')
const editorError = ref('')
const accountDraft = ref<TradingAccount | null>(null)
const testResults = reactive<Record<number, AccountMxTestResult | AccountLlmTestResult>>({})

const showSkills = ref(false)
const skillsAccount = ref<TradingAccount | null>(null)
const skillsList = ref<AccountSkillList | null>(null)
const skillsLoading = ref(false)

const marketOptions = [
  { key: 'sh_main', label: '上证' },
  { key: 'sz_main', label: '深证' },
  { key: 'chinext', label: '创业板' },
  { key: 'star', label: '科创板' },
  { key: 'bse', label: '北交所' },
] as const

const draft = reactive<Record<string, unknown>>({})

onMounted(async () => {
  if (!store.accountsLoaded) {
    try {
      await store.loadAccounts()
    } catch (exception) {
      error.value = (exception as Error).message
    }
  }
})

function llmSourceText(account: TradingAccount): string {
  if (account.resolved_llm_source === 'account') {
    return `账户模型（${account.llm_model ?? '—'}）`
  }
  if (account.resolved_llm_source === 'global') {
    return '全局模型'
  }
  return '未配置'
}

function formatAmount(value: number): string {
  return Number(value ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

function resetDraft(account: TradingAccount | null) {
  accountDraft.value = account
  const defaults: Record<string, unknown> = account
    ? { ...account }
    : {
        name: '',
        slug: '',
        enabled: true,
        account_llm_enabled: false,
        llm_provider_name: '',
        llm_base_url: '',
        llm_api_key: '',
        llm_model: '',
        llm_reasoning_effort: '',
        llm_max_retries: 3,
        llm_enable_reasoning_content_echo: false,
        system_prompt: '你是专业的 A 股交易分析师。',
        analyst_prompt: '请结合市场数据、资讯、候选股票、持仓和资金情况做判断。当信号不明确时返回HOLD。',
        market_query: '上证指数今天走势和市场概况',
        news_query: '今天A股市场热点新闻',
        screener_query: 'A股今天值得关注的强势股',
        max_actions: 2,
        trade_enabled: true,
        allowed_markets: ['sh_main', 'sz_main'] as string[],
        tg_notify_trade_enabled: false,
        capital_seal_enabled: false,
        capital_seal_amount: 0,
        mx_api_key: '',
      }
  for (const key of Object.keys(defaults)) {
    draft[key] = defaults[key]
  }
}

function startCreate() {
  creating.value = true
  try {
    isCreating.value = true
    editingId.value = null
    resetDraft(null)
    editorError.value = ''
    showEditor.value = true
  } finally {
    creating.value = false
  }
}

function openEdit(account: TradingAccount) {
  isCreating.value = false
  editingId.value = account.id
  resetDraft(account)
  editorError.value = ''
  showEditor.value = true
}

function closeEditor() {
  showEditor.value = false
  editingId.value = null
  accountDraft.value = null
  editorError.value = ''
}

function buildPayload(): TradingAccountPayload {
  return {
    name: String(draft.name ?? '').trim(),
    enabled: Boolean(draft.enabled),
    account_llm_enabled: Boolean(draft.account_llm_enabled),
    llm_provider_name: String(draft.llm_provider_name ?? '').trim() || null,
    llm_base_url: String(draft.llm_base_url ?? '').trim() || null,
    llm_api_key: String(draft.llm_api_key ?? '').trim() || null,
    llm_model: String(draft.llm_model ?? '').trim() || null,
    llm_reasoning_effort: String(draft.llm_reasoning_effort ?? '').trim() || null,
    llm_max_retries: Number(draft.llm_max_retries) || null,
    llm_enable_reasoning_content_echo: Boolean(draft.llm_enable_reasoning_content_echo),
    system_prompt: String(draft.system_prompt ?? ''),
    analyst_prompt: String(draft.analyst_prompt ?? ''),
    market_query: String(draft.market_query ?? ''),
    news_query: String(draft.news_query ?? ''),
    screener_query: String(draft.screener_query ?? ''),
    max_actions: Math.max(1, Math.min(20, Number(draft.max_actions ?? 2))),
    trade_enabled: Boolean(draft.trade_enabled),
    allowed_markets: ((draft.allowed_markets as string[]) ?? ['sh_main', 'sz_main']) as TradingAccountPayload['allowed_markets'],
    tg_notify_trade_enabled: Boolean(draft.tg_notify_trade_enabled),
    capital_seal_enabled: Boolean(draft.capital_seal_enabled),
    capital_seal_amount: Number(draft.capital_seal_amount ?? 0),
  }
}

async function save() {
  saving.value = true
  editorError.value = ''
  try {
    const payload = buildPayload()
    const rawKey = String(draft.mx_api_key ?? '')
    if (rawKey.trim() || isCreating.value) {
      payload.mx_api_key = rawKey.trim() || null
    }
    if (isCreating.value) {
      payload.slug = String(draft.slug ?? '').trim() || undefined
      const created = await api.createAccount(payload)
      await store.loadAccounts()
      store.selectAccount(created.id)
    } else if (editingId.value !== null) {
      await api.updateAccount(editingId.value, payload)
      await store.loadAccounts()
    }
    closeEditor()
  } catch (exception) {
    editorError.value = (exception as Error).message
  } finally {
    saving.value = false
  }
}

async function archive(account: TradingAccount) {
  error.value = ''
  try {
    await api.archiveAccount(account.id)
    await store.loadAccounts()
  } catch (exception) {
    error.value = (exception as Error).message
  }
}

async function restore(account: TradingAccount) {
  error.value = ''
  try {
    await api.restoreAccount(account.id)
    await store.loadAccounts()
  } catch (exception) {
    error.value = (exception as Error).message
  }
}

async function testMx(account: TradingAccount) {
  testResults[account.id] = { ok: false, message: '测试中…' }
  try {
    testResults[account.id] = await api.testAccountMx(account.id)
  } catch (exception) {
    testResults[account.id] = { ok: false, message: (exception as Error).message }
  }
}

async function testLlm(account: TradingAccount) {
  testResults[account.id] = { ok: false, message: '测试中…' }
  try {
    testResults[account.id] = await api.testAccountLlm(account.id)
  } catch (exception) {
    testResults[account.id] = { ok: false, message: (exception as Error).message }
  }
}

async function openSkills(account: TradingAccount) {
  skillsAccount.value = account
  skillsList.value = null
  showSkills.value = true
  skillsLoading.value = true
  try {
    skillsList.value = await api.getAccountSkills(account.id)
  } catch (exception) {
    error.value = (exception as Error).message
  } finally {
    skillsLoading.value = false
  }
}

async function toggleSkill(skillId: string) {
  if (!skillsAccount.value || !skillsList.value) {
    return
  }
  const current = skillsList.value
  const enabled = new Set(current.effective_enabled)
  if (enabled.has(skillId)) {
    enabled.delete(skillId)
  } else {
    enabled.add(skillId)
  }
  try {
    skillsList.value = await api.updateAccountSkills(skillsAccount.value.id, [...enabled])
  } catch (exception) {
    error.value = (exception as Error).message
  }
}

function closeSkills() {
  showSkills.value = false
  skillsAccount.value = null
  skillsList.value = null
}
</script>
