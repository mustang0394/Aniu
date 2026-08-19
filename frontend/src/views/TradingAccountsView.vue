<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader
      title="交易账户"
      kicker="Accounts"
      description="每个账户绑定独立妙想 Key，拥有独立的提示词、市场范围、Skills、定时任务、自动化会话与运行历史"
    >
      <UiButton variant="primary" :loading="creating" @click="startCreate">
        {{ creating ? '创建中…' : '＋ 新建账户' }}
      </UiButton>
    </UiPageHeader>

    <div
      v-if="error"
      class="rounded-[12px] border border-danger/25 bg-danger-soft px-4 py-3 text-body font-medium text-danger-text"
      role="alert"
    >
      {{ error }}
    </div>

    <div
      v-if="store.loadingAccounts && store.accounts.length === 0"
      class="py-12 text-center text-caption text-label-tertiary"
    >
      正在加载账户…
    </div>

    <UiEmpty
      v-else-if="store.accounts.length === 0"
      title="暂无交易账户"
      description="点击右上角「新建账户」创建第一个交易账户。"
    />

    <div v-else class="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <article
        v-for="account in store.accounts"
        :key="account.id"
        class="glass-card rounded-xl border border-separator p-5 shadow-sm transition-shadow hover:shadow-md"
        :class="account.archived ? 'opacity-75' : ''"
      >
        <div class="flex items-start justify-between gap-3">
          <div class="flex min-w-0 items-center gap-3">
            <div
              class="flex size-11 shrink-0 items-center justify-center rounded-[12px] text-base font-semibold"
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
                <span v-if="account.slug === 'default'" class="rounded-full bg-fill px-2 py-0.5 text-[11px] font-medium text-label-tertiary">默认</span>
              </div>
              <p class="mt-1 truncate text-footnote text-label-tertiary">
                <span class="font-mono">{{ account.slug }}</span>
                <span class="mx-1.5">·</span>
                <span :class="account.has_mx_api_key ? 'text-success-text' : 'text-warning-text'">
                  {{ account.has_mx_api_key ? '妙想 Key 已配置' : '妙想 Key 未配置' }}
                </span>
              </p>
            </div>
          </div>
        </div>

        <dl class="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div class="rounded-[10px] bg-fill p-3">
            <dt class="text-footnote text-label-tertiary">大模型</dt>
            <dd class="mt-0.5 truncate text-callout font-semibold text-label">{{ llmSourceText(account) }}</dd>
          </div>
          <div class="rounded-[10px] bg-fill p-3">
            <dt class="text-footnote text-label-tertiary">允许市场</dt>
            <dd class="mt-0.5 truncate text-callout font-semibold text-label">{{ marketLabel(account.allowed_markets) }}</dd>
          </div>
          <div class="rounded-[10px] bg-fill p-3">
            <dt class="text-footnote text-label-tertiary">交易开关</dt>
            <dd class="mt-0.5 text-callout font-semibold" :class="account.trade_enabled ? 'text-success-text' : 'text-danger-text'">
              {{ account.trade_enabled ? '启用' : '停用' }}
            </dd>
          </div>
          <div class="rounded-[10px] bg-fill p-3">
            <dt class="text-footnote text-label-tertiary">资金封印</dt>
            <dd class="mt-0.5 truncate text-callout font-semibold text-label">
              {{ account.capital_seal_enabled ? `¥${formatAmount(account.capital_seal_amount)}` : '关闭' }}
            </dd>
          </div>
        </dl>

        <div v-if="testResults[account.id]" class="mt-3 rounded-[10px] border border-separator bg-fill px-3 py-2 text-footnote" :class="testResults[account.id].ok ? 'text-success-text' : 'text-danger-text'">
          {{ testResults[account.id].message }}
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-separator pt-4">
          <UiButton variant="primary" size="sm" :disabled="account.archived" @click="openEdit(account)">
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /><path d="m15 5 4 4" /></svg>
            编辑
          </UiButton>
          <UiButton variant="ghost" size="sm" :disabled="account.archived" @click="openSkills(account)">
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" /></svg>
            Skills
          </UiButton>
          <UiButton
            variant="tinted"
            size="sm"
            :disabled="account.archived || testingMxId === account.id"
            :loading="testingMxId === account.id"
            @click="testMx(account)"
          >
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 20h.01M7 20v-4M12 20v-8M17 20V8M22 20V4" /></svg>
            测试妙想
          </UiButton>
          <UiButton
            variant="tinted"
            size="sm"
            :disabled="account.archived || testingLlmId === account.id"
            :loading="testingLlmId === account.id"
            @click="testLlm(account)"
          >
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><path d="M15 2v4M15 18v4M2 15h4M18 15h4M2 9h4M18 9h4M9 2v4M9 18v4" /></svg>
            测试 LLM
          </UiButton>
          <span class="flex-1" />
          <UiButton
            v-if="!account.archived && account.slug !== 'default'"
            variant="danger-soft"
            size="sm"
            @click="archive(account)"
          >
            归档
          </UiButton>
          <UiButton
            v-else-if="account.archived"
            variant="tinted"
            size="sm"
            @click="restore(account)"
          >
            恢复
          </UiButton>
        </div>
      </article>
    </div>

    <!-- 编辑 / 新建抽屉 -->
    <Teleport to="body">
      <Transition name="drawer">
        <div v-if="showEditor" class="fixed inset-0 z-50 flex justify-end">
          <div class="absolute inset-0 bg-black/40" @click="closeEditor" />
          <div class="relative flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl">
            <!-- 抽屉头部 -->
            <header class="flex items-center justify-between border-b border-separator px-6 py-4">
              <div>
                <h2 class="m-0 text-title-2 font-semibold tracking-tight text-label">
                  {{ isCreating ? '新建交易账户' : '编辑交易账户' }}
                </h2>
                <p class="mt-0.5 text-footnote text-label-tertiary">
                  {{ isCreating ? '配置独立妙想 Key、大模型与交易策略' : (accountDraft?.name ?? '') }}
                </p>
              </div>
              <button
                type="button"
                class="flex size-9 items-center justify-center rounded-[10px] text-label-secondary transition-colors hover:bg-hover hover:text-label"
                aria-label="关闭"
                @click="closeEditor"
              >
                ✕
              </button>
            </header>

            <!-- 抽屉内容 -->
            <div class="flex-1 overflow-y-auto px-6 py-5">
              <div v-if="editorError" class="mb-4 rounded-[10px] border border-danger/25 bg-danger-soft px-3 py-2 text-footnote text-danger-text" role="alert">
                {{ editorError }}
              </div>

              <form id="account-editor-form" class="account-form" @submit.prevent="save">
                <!-- 基本信息 -->
                <section class="account-section">
                  <header class="account-section__header">
                    <h3 class="account-section__title">基本信息</h3>
                    <p class="account-section__desc">账户名称与唯一标识</p>
                  </header>
                  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <UiField label="账户名称" help="在账户切换器与列表中展示的名称。">
                      <input
                        :value="String(draft.name ?? '')"
                        type="text"
                        required
                        class="field-input"
                        placeholder="例如：趋势账户"
                        @input="draft.name = ($event.target as HTMLInputElement).value"
                      />
                    </UiField>
                    <UiField
                      v-if="isCreating"
                      label="Slug"
                      help="唯一标识，留空自动生成；用于数据库归属，创建后不可修改。"
                    >
                      <input
                        :value="String(draft.slug ?? '')"
                        type="text"
                        class="field-input"
                        placeholder="trend"
                        @input="draft.slug = ($event.target as HTMLInputElement).value"
                      />
                    </UiField>
                  </div>
                </section>

                <!-- 妙想 Key -->
                <section class="account-section">
                  <header class="account-section__header">
                    <h3 class="account-section__title">妙想 Key</h3>
                    <p class="account-section__desc">该账户行情、持仓、订单与模拟交易统一使用的 apikey</p>
                  </header>
                  <UiField
                    :label="isCreating ? '妙想密钥' : '妙想密钥（脱敏显示）'"
                    :help="isCreating ? '访问东方财富妙想接口的 apikey。' : '留空保持不变；输入空字符串可清除当前 Key。'"
                  >
                    <input
                      v-model="draft.mx_api_key"
                      type="password"
                      autocomplete="off"
                      class="field-input max-w-xl"
                      placeholder="lz-xxxx…"
                    />
                  </UiField>
                </section>

                <!-- 独立大模型 -->
                <section class="account-section">
                  <header class="account-section__header">
                    <div>
                      <h3 class="account-section__title">独立大模型</h3>
                      <p class="account-section__desc">不启用则使用全局大模型；启用后必须整套完整才生效</p>
                    </div>
                    <UiToggle v-model="draft.account_llm_enabled" />
                  </header>

                  <div :class="draft.account_llm_enabled ? '' : 'pointer-events-none opacity-45'">
                    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <UiField label="Base URL" help="OpenAI 兼容端点。">
                        <input v-model="draft.llm_base_url" type="text" class="field-input" placeholder="https://api.example.com/v1" />
                      </UiField>
                      <UiField label="API Key" help="访问大模型 API 的密钥。">
                        <input v-model="draft.llm_api_key" type="password" autocomplete="off" class="field-input" placeholder="sk-…" />
                      </UiField>
                      <UiField label="模型名" help="例如 gpt-4o-mini、o3-mini。">
                        <input v-model="draft.llm_model" type="text" class="field-input" placeholder="gpt-4o-mini" />
                      </UiField>
                      <UiField label="思考等级" help="reasoning_effort；留空则不传。">
                        <input v-model="draft.llm_reasoning_effort" type="text" class="field-input" placeholder="low / medium / high" />
                      </UiField>
                      <UiField label="请求重试次数" help="单次调用失败后的额外重试，默认 3。">
                        <input v-model.number="draft.llm_max_retries" type="number" min="0" max="10" class="field-input" />
                      </UiField>
                    </div>

                    <div class="account-card mt-4">
                      <div class="account-card__row">
                        <div class="min-w-0">
                          <p class="account-card__title">回传思考内容</p>
                          <p class="account-card__hint">
                            将推理模型返回的 reasoning_content 在下一轮请求中回传，避免 DeepSeek-v4 系列模型报 400。
                          </p>
                        </div>
                        <UiToggle v-model="draft.llm_enable_reasoning_content_echo" />
                      </div>
                    </div>
                  </div>
                </section>

                <!-- 交易策略 -->
                <section class="account-section">
                  <header class="account-section__header">
                    <h3 class="account-section__title">交易策略</h3>
                    <p class="account-section__desc">提示词、查询口径与选股范围</p>
                  </header>

                  <div class="space-y-4">
                    <UiField label="系统提示词" help="定义 AI 角色、目标与决策风格。">
                      <textarea
                        :value="String(draft.system_prompt ?? '')"
                        rows="4"
                        class="field-input field-input--textarea"
                        @input="draft.system_prompt = ($event.target as HTMLTextAreaElement).value"
                      />
                    </UiField>

                    <UiField label="分析师提示词" help="注入系统提示词「分析师设定」段落，决定决策风格。">
                      <textarea
                        :value="String(draft.analyst_prompt ?? '')"
                        rows="2"
                        class="field-input"
                        @input="draft.analyst_prompt = ($event.target as HTMLTextAreaElement).value"
                      />
                    </UiField>

                    <div class="grid grid-cols-1 gap-4">
                      <UiField label="市场查询" help="预取阶段的市场快照查询词，会作为本轮实时行情数据快照的查询输入。">
                        <input v-model="draft.market_query" type="text" class="field-input" />
                      </UiField>
                      <UiField label="资讯查询" help="预取阶段的资讯快照查询词，会作为本轮实时资讯数据快照的查询输入。">
                        <input v-model="draft.news_query" type="text" class="field-input" />
                      </UiField>
                      <UiField label="选股查询" help="预取阶段的选股快照查询词，会作为本轮实时选股数据快照的查询输入。">
                        <input v-model="draft.screener_query" type="text" class="field-input" />
                      </UiField>
                    </div>

                    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <UiField label="最大动作数" help="每轮最多执行的买入/卖出/撤单次数。">
                        <input v-model.number="draft.max_actions" type="number" min="1" max="20" class="field-input" />
                      </UiField>
                      <UiField label="允许市场" help="选股与买入必须落在允许范围内。">
                        <div class="flex flex-wrap gap-2">
                          <label
                            v-for="market in marketOptions"
                            :key="market.key"
                            class="market-chip"
                            :class="(draft.allowed_markets as string[]).includes(market.key) ? 'market-chip--active' : ''"
                          >
                            <input
                              v-model="draft.allowed_markets"
                              type="checkbox"
                              :value="market.key"
                              class="sr-only"
                            />
                            {{ market.label }}
                          </label>
                        </div>
                      </UiField>
                    </div>

                    <div class="account-card">
                      <div class="account-card__row">
                        <div class="min-w-0">
                          <p class="account-card__title">允许交易</p>
                          <p class="account-card__hint">关闭后该账户的买入、卖出与撤单会被服务端硬拦截。</p>
                        </div>
                        <UiToggle v-model="draft.trade_enabled" />
                      </div>
                    </div>
                  </div>
                </section>

                <!-- 自动化上下文 -->
                <section class="account-section">
                  <header class="account-section__header">
                    <h3 class="account-section__title">自动化上下文</h3>
                    <p class="account-section__desc">控制该账户自动化会话的历史记忆与压缩行为</p>
                  </header>

                  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                    <UiField label="最大上下文" help="自动化会话上下文窗口大小；后端按 85% 触发压缩，默认 128000。">
                      <input
                        v-model.number="draft.automation_context_window_tokens"
                        type="number"
                        min="4096"
                        step="1024"
                        class="field-input"
                      />
                    </UiField>
                    <UiField label="最近消息条数" help="保留给大模型参考的最近消息数，超限触发压缩，默认 24。">
                      <input
                        v-model.number="draft.automation_recent_message_limit"
                        type="number"
                        min="4"
                        max="200"
                        class="field-input"
                      />
                    </UiField>
                    <UiField label="空闲摘要小时" help="会话空闲超过该小时数后触发压缩，默认 12。">
                      <input
                        v-model.number="draft.automation_idle_summary_hours"
                        type="number"
                        min="1"
                        max="168"
                        class="field-input"
                      />
                    </UiField>
                  </div>

                  <div class="account-card mt-4">
                    <div class="account-card__row">
                      <div class="min-w-0">
                        <p class="account-card__title">自动压缩</p>
                        <p class="account-card__hint">上下文超过阈值时自动生成历史策略摘要。
                        </p>
                      </div>
                      <UiToggle v-model="draft.automation_enable_auto_compaction" />
                    </div>
                  </div>
                </section>

                <!-- 风控与通知 -->
                <section class="account-section account-section--last">
                  <header class="account-section__header">
                    <h3 class="account-section__title">风控与通知</h3>
                    <p class="account-section__desc">资金封印与 Telegram 通知</p>
                  </header>

                  <div class="space-y-3">
                    <div class="account-card">
                      <div class="account-card__row">
                        <div class="min-w-0">
                          <p class="account-card__title">资金封印</p>
                          <p class="account-card__hint">封印金额从可操作资金中扣除，防止 AI 使用全部资金。</p>
                        </div>
                        <UiToggle v-model="draft.capital_seal_enabled" />
                      </div>
                      <div v-if="draft.capital_seal_enabled" class="account-card__body">
                        <UiField label="封印金额（元）">
                          <input v-model.number="draft.capital_seal_amount" type="number" min="0" step="0.01" class="field-input max-w-xs" />
                        </UiField>
                      </div>
                    </div>

                    <div class="account-card">
                      <div class="account-card__row">
                        <div class="min-w-0">
                          <p class="account-card__title">Telegram 交易通知</p>
                          <p class="account-card__hint">交易执行后通过 Telegram 推送通知，需配置 Bot Token 与 Chat ID。</p>
                        </div>
                        <UiToggle v-model="draft.tg_notify_trade_enabled" />
                      </div>
                      <div v-if="draft.tg_notify_trade_enabled" class="account-card__body space-y-4">
                        <UiField label="Bot Token" help="从 @BotFather 获取的机器人令牌，明文展示方便复制。">
                          <input v-model="draft.tg_bot_token" type="text" class="field-input font-mono text-footnote" placeholder="123456:ABC-DEF…" />
                        </UiField>
                        <UiField label="Chat ID" help="接收通知的会话 ID，支持群组或私人聊天。">
                          <input v-model="draft.tg_chat_id" type="text" class="field-input" placeholder="-1001234567890" />
                        </UiField>
                      </div>
                    </div>
                  </div>
                </section>
              </form>
            </div>

            <!-- 抽屉底部 -->
            <footer class="flex items-center justify-end gap-2 border-t border-separator px-6 py-4">
              <UiButton variant="ghost" @click="closeEditor">取消</UiButton>
              <UiButton
                variant="primary"
                type="submit"
                form="account-editor-form"
                :loading="saving"
                :disabled="saving"
              >
                {{ isCreating ? '创建账户' : '保存修改' }}
              </UiButton>
            </footer>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Skills 抽屉 -->
    <Teleport to="body">
      <Transition name="drawer">
        <div v-if="showSkills" class="fixed inset-0 z-50 flex justify-end">
          <div class="absolute inset-0 bg-black/40" @click="closeSkills" />
          <div class="relative flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
            <header class="flex items-center justify-between border-b border-separator px-6 py-4">
              <div>
                <h2 class="m-0 text-title-2 font-semibold tracking-tight text-label">账户 Skills</h2>
                <p class="mt-0.5 text-footnote text-label-tertiary">{{ skillsAccount?.name ?? '' }}</p>
              </div>
              <button
                type="button"
                class="flex size-9 items-center justify-center rounded-[10px] text-label-secondary transition-colors hover:bg-hover hover:text-label"
                aria-label="关闭"
                @click="closeSkills"
              >
                ✕
              </button>
            </header>

            <div class="flex-1 overflow-y-auto px-6 py-5">
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
                      <span class="font-mono">{{ skill.id }}</span>
                      <span v-if="skill.always_enabled" class="ml-1 text-success-text">（系统运行时，始终启用）</span>
                      <span v-else-if="skill.global_disabled" class="ml-1 text-danger-text">（全局硬禁用）</span>
                    </p>
                  </div>
                  <UiToggle
                    :model-value="skill.effective_enabled"
                    :disabled="skill.always_enabled || skill.global_disabled"
                    @update:model-value="toggleSkill(skill.id)"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
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
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiField from '@/components/ui/UiField.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiToggle from '@/components/ui/UiToggle.vue'

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
const testingMxId = ref<number | null>(null)
const testingLlmId = ref<number | null>(null)

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

interface AccountDraft {
  name: string
  slug: string
  enabled: boolean
  mx_api_key: string
  account_llm_enabled: boolean
  llm_provider_name: string
  llm_base_url: string
  llm_api_key: string
  llm_model: string
  llm_reasoning_effort: string
  llm_max_retries: number
  llm_enable_reasoning_content_echo: boolean
  system_prompt: string
  analyst_prompt: string
  market_query: string
  news_query: string
  screener_query: string
  max_actions: number
  trade_enabled: boolean
  allowed_markets: string[]
  tg_bot_token: string
  tg_chat_id: string
  tg_notify_trade_enabled: boolean
  capital_seal_enabled: boolean
  capital_seal_amount: number
  automation_context_window_tokens: number
  automation_recent_message_limit: number
  automation_enable_auto_compaction: boolean
  automation_idle_summary_hours: number
}

const draft = reactive<AccountDraft>({
  name: '',
  slug: '',
  enabled: true,
  mx_api_key: '',
  account_llm_enabled: false,
  llm_provider_name: '',
  llm_base_url: '',
  llm_api_key: '',
  llm_model: '',
  llm_reasoning_effort: '',
  llm_max_retries: 3,
  llm_enable_reasoning_content_echo: false,
  system_prompt: '',
  analyst_prompt: '',
  market_query: '',
  news_query: '',
  screener_query: '',
  max_actions: 2,
  trade_enabled: true,
  allowed_markets: ['sh_main', 'sz_main'],
  tg_bot_token: '',
  tg_chat_id: '',
  tg_notify_trade_enabled: false,
  capital_seal_enabled: false,
  capital_seal_amount: 0,
  automation_context_window_tokens: 128000,
  automation_recent_message_limit: 24,
  automation_enable_auto_compaction: true,
  automation_idle_summary_hours: 12,
})

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
    return account.llm_model ?? '账户模型'
  }
  if (account.resolved_llm_source === 'global') {
    return '全局模型'
  }
  return '未配置'
}

function marketLabel(markets: string[]): string {
  const labels: Record<string, string> = {
    sh_main: '上证',
    sz_main: '深证',
    chinext: '创业板',
    star: '科创板',
    bse: '北交所',
  }
  const text = (markets ?? []).map((key) => labels[key] ?? key).join('、')
  return text || '默认'
}

function formatAmount(value: number): string {
  return Number(value ?? 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })
}

function resetDraft(account: TradingAccount | null) {
  accountDraft.value = account
  const source = account
  if (source) {
    draft.name = source.name
    draft.slug = source.slug
    draft.enabled = source.enabled
    draft.mx_api_key = source.mx_api_key ?? ''
    draft.account_llm_enabled = source.account_llm_enabled
    draft.llm_provider_name = source.llm_provider_name ?? ''
    draft.llm_base_url = source.llm_base_url ?? ''
    draft.llm_api_key = source.llm_api_key ?? ''
    draft.llm_model = source.llm_model ?? ''
    draft.llm_reasoning_effort = source.llm_reasoning_effort ?? ''
    draft.llm_max_retries = source.llm_max_retries ?? 3
    draft.llm_enable_reasoning_content_echo = source.llm_enable_reasoning_content_echo
    draft.system_prompt = source.system_prompt
    draft.analyst_prompt = source.analyst_prompt
    draft.market_query = source.market_query
    draft.news_query = source.news_query
    draft.screener_query = source.screener_query
    draft.max_actions = source.max_actions
    draft.trade_enabled = source.trade_enabled
    draft.allowed_markets = [...source.allowed_markets]
    draft.tg_bot_token = source.tg_bot_token ?? ''
    draft.tg_chat_id = source.tg_chat_id ?? ''
    draft.tg_notify_trade_enabled = source.tg_notify_trade_enabled
    draft.capital_seal_enabled = source.capital_seal_enabled
    draft.capital_seal_amount = source.capital_seal_amount
    draft.automation_context_window_tokens = source.automation_context_window_tokens
    draft.automation_recent_message_limit = source.automation_recent_message_limit
    draft.automation_enable_auto_compaction = source.automation_enable_auto_compaction
    draft.automation_idle_summary_hours = source.automation_idle_summary_hours
    return
  }

  draft.name = ''
  draft.slug = ''
  draft.enabled = true
  draft.mx_api_key = ''
  draft.account_llm_enabled = false
  draft.llm_provider_name = ''
  draft.llm_base_url = ''
  draft.llm_api_key = ''
  draft.llm_model = ''
  draft.llm_reasoning_effort = ''
  draft.llm_max_retries = 3
  draft.llm_enable_reasoning_content_echo = false
  draft.system_prompt = '你是专业的 A 股交易分析师。'
  draft.analyst_prompt = '请结合市场数据、资讯、候选股票、持仓和资金情况做判断。当信号不明确时返回HOLD。'
  draft.market_query = '上证指数今天走势和市场概况'
  draft.news_query = '今天A股市场热点新闻'
  draft.screener_query = 'A股今天值得关注的强势股'
  draft.max_actions = 2
  draft.trade_enabled = true
  draft.allowed_markets = ['sh_main', 'sz_main']
  draft.tg_bot_token = ''
  draft.tg_chat_id = ''
  draft.tg_notify_trade_enabled = false
  draft.capital_seal_enabled = false
  draft.capital_seal_amount = 0
  draft.automation_context_window_tokens = 128000
  draft.automation_recent_message_limit = 24
  draft.automation_enable_auto_compaction = true
  draft.automation_idle_summary_hours = 12
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
    name: draft.name.trim(),
    enabled: draft.enabled,
    account_llm_enabled: draft.account_llm_enabled,
    llm_provider_name: draft.llm_provider_name.trim() || null,
    llm_base_url: draft.llm_base_url.trim() || null,
    llm_api_key: draft.llm_api_key.trim() || null,
    llm_model: draft.llm_model.trim() || null,
    llm_reasoning_effort: draft.llm_reasoning_effort.trim() || null,
    llm_max_retries: draft.llm_max_retries || null,
    llm_enable_reasoning_content_echo: draft.llm_enable_reasoning_content_echo,
    system_prompt: draft.system_prompt,
    analyst_prompt: draft.analyst_prompt,
    market_query: draft.market_query,
    news_query: draft.news_query,
    screener_query: draft.screener_query,
    max_actions: Math.max(1, Math.min(20, draft.max_actions)),
    trade_enabled: draft.trade_enabled,
    allowed_markets: (draft.allowed_markets ?? ['sh_main', 'sz_main']) as TradingAccountPayload['allowed_markets'],
    tg_bot_token: draft.tg_bot_token.trim() || null,
    tg_chat_id: draft.tg_chat_id.trim() || null,
    tg_notify_trade_enabled: draft.tg_notify_trade_enabled,
    capital_seal_enabled: draft.capital_seal_enabled,
    capital_seal_amount: draft.capital_seal_amount,
    automation_context_window_tokens: draft.automation_context_window_tokens,
    automation_recent_message_limit: draft.automation_recent_message_limit,
    automation_enable_auto_compaction: draft.automation_enable_auto_compaction,
    automation_idle_summary_hours: draft.automation_idle_summary_hours,
  }
}

async function save() {
  saving.value = true
  editorError.value = ''
  try {
    const payload = buildPayload()
    const rawKey = draft.mx_api_key
    if (rawKey.trim() || isCreating.value) {
      payload.mx_api_key = rawKey.trim() || null
    }
    if (isCreating.value) {
      payload.slug = draft.slug.trim() || undefined
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
  testingMxId.value = account.id
  try {
    testResults[account.id] = await api.testAccountMx(account.id)
  } catch (exception) {
    testResults[account.id] = { ok: false, message: (exception as Error).message }
  } finally {
    testingMxId.value = null
  }
}

async function testLlm(account: TradingAccount) {
  testResults[account.id] = { ok: false, message: '测试中…' }
  testingLlmId.value = account.id
  try {
    testResults[account.id] = await api.testAccountLlm(account.id)
  } catch (exception) {
    testResults[account.id] = { ok: false, message: (exception as Error).message }
  } finally {
    testingLlmId.value = null
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

<style scoped>
@reference "../styles/tailwind.css";

.account-form {
  @apply flex flex-col;
}

.account-section {
  @apply border-b border-separator py-6 first:pt-0;
}

.account-section--last {
  @apply border-b-0 pb-1;
}

.account-section__header {
  @apply mb-4 flex items-start justify-between gap-3;
}

.account-section__title {
  @apply m-0 text-title-3 font-semibold tracking-tight text-label;
}

.account-section__desc {
  @apply m-0 mt-1 text-footnote text-label-secondary;
}

/* 子卡片：允许交易 / 资金封印 / Telegram 通知 */
.account-card {
  @apply overflow-hidden rounded-[16px] border border-separator bg-fill/35;
}

.account-card__row {
  @apply flex items-start justify-between gap-4 px-4 py-3.5;
}

.account-card__title {
  @apply m-0 text-footnote font-semibold text-label;
}

.account-card__hint {
  @apply m-0 mt-1 text-caption leading-snug text-label-tertiary;
}

.account-card__body {
  @apply border-t border-separator/80 px-4 py-3.5;
}

/* 市场多选 chip */
.market-chip {
  @apply inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-pill px-3 text-footnote font-medium ring-1 ring-separator-strong;
  @apply text-label-secondary transition-colors;
}

.market-chip--active {
  @apply bg-accent-soft text-accent-text ring-accent/30;
}

.market-chip:hover {
  @apply bg-hover;
}

.market-chip--active:hover {
  @apply bg-accent-soft;
}

/* 抽屉动画 */
.drawer-enter-active,
.drawer-leave-active {
  transition: opacity 0.2s ease;
}

.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}

.drawer-enter-active > div:last-child,
.drawer-leave-active > div:last-child {
  transition: transform 0.22s ease;
}

.drawer-enter-from > div:last-child,
.drawer-leave-to > div:last-child {
  transform: translateX(100%);
}

/* 复用 SettingsView 的输入样式 */
.field-input {
  @apply h-11 w-full rounded-[12px] border border-separator-strong bg-card-solid/80 px-3.5 text-body text-label outline-none transition-colors;
  @apply placeholder:text-label-quaternary;
  @apply focus:border-accent focus:bg-accent-soft/30 focus:ring-2 focus:ring-accent-ring;
  @apply disabled:cursor-not-allowed disabled:opacity-50;
}

.field-input--textarea {
  @apply h-auto min-h-[120px] py-3 font-mono text-[13px] leading-relaxed;
}

textarea.field-input {
  @apply h-auto;
}
</style>
