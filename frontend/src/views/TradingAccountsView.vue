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

    <div v-else class="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-2">
      <article
        v-for="account in store.accounts"
        :key="account.id"
        class="account-card group relative overflow-hidden rounded-xl border border-separator bg-card-solid shadow-sm transition-all duration-200 hover:-translate-y-px hover:shadow-md"
        :class="[
          account.archived ? 'opacity-70 grayscale-[0.35]' : '',
          account.archived ? 'cursor-default' : 'cursor-pointer',
        ]"
        :tabindex="account.archived ? -1 : 0"
        :role="account.archived ? undefined : 'button'"
        @click="!account.archived && openEdit(account)"
        @keydown.enter.space.prevent="!account.archived && openEdit(account)"
      >
        <!-- 状态色竖条 -->
        <span
          class="absolute inset-y-0 left-0 w-1"
          :class="statusBarClass(account)"
          aria-hidden="true"
        />

        <div class="px-5 pt-5 pb-2 pl-6">
          <!-- 头部：头像 + 名称 + 状态 -->
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
                  <UiBadge v-if="account.archived" tone="neutral">已归档</UiBadge>
                  <UiBadge v-else-if="!account.enabled" tone="warning">已停用</UiBadge>
                  <UiBadge v-else tone="success">运行中</UiBadge>
                  <UiBadge v-if="account.slug === 'default'" tone="neutral">默认</UiBadge>
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

          <!-- 最近运行指标行 -->
          <button
            type="button"
            class="latest-run mt-3 flex w-full items-center gap-2 rounded-[10px] px-2.5 py-2 text-left text-footnote transition-colors hover:bg-hover"
            :disabled="!account.latest_run"
            :class="account.latest_run ? 'cursor-pointer' : 'cursor-default'"
            @click.stop="goToAccountTasks(account)"
          >
            <span class="text-caption text-label-tertiary">最近运行</span>
            <template v-if="account.latest_run">
              <span class="flex items-center gap-1 font-medium" :class="runStatusToneClass(account.latest_run.status)">
                <span class="size-1.5 rounded-full" :class="runStatusDotClass(account.latest_run.status)" />
                {{ runTypeLabel(account.latest_run.run_type) }}
              </span>
              <span class="text-label-secondary">{{ relativeTime(account.latest_run.started_at) }}</span>
              <span v-if="account.latest_run.executed_trade_count > 0" class="text-label-tertiary">
                · {{ account.latest_run.executed_trade_count }} 笔交易
              </span>
              <svg class="ml-auto size-3.5 shrink-0 text-label-quaternary" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
            </template>
            <span v-else class="text-label-quaternary">尚未运行</span>
          </button>

          <!-- 资产懒加载块 -->
          <div class="mt-2 rounded-[10px] border border-separator/70 bg-fill/40">
            <template v-if="assetState[account.id]?.data">
              <div class="grid grid-cols-2 gap-2 px-3 py-2.5 sm:grid-cols-4">
                <div>
                  <p class="m-0 text-[11px] text-label-tertiary">总资产</p>
                  <p class="m-0 mt-0.5 text-callout font-semibold tabular-nums text-label">{{ formatMoney(assetState[account.id]!.data!.total_assets) }}</p>
                </div>
                <div>
                  <p class="m-0 text-[11px] text-label-tertiary">当日盈亏</p>
                  <p class="m-0 mt-0.5 text-callout font-semibold tabular-nums" :class="profitClass(assetState[account.id]!.data!.daily_profit)">{{ formatSignedMoney(assetState[account.id]!.data!.daily_profit) }}</p>
                </div>
                <div>
                  <p class="m-0 text-[11px] text-label-tertiary">累计收益率</p>
                  <p class="m-0 mt-0.5 text-callout font-semibold tabular-nums" :class="profitClass(assetState[account.id]!.data!.total_return_ratio)">{{ formatPercent(assetState[account.id]!.data!.total_return_ratio) }}</p>
                </div>
                <div>
                  <p class="m-0 text-[11px] text-label-tertiary">持仓数</p>
                  <p class="m-0 mt-0.5 text-callout font-semibold tabular-nums text-label">{{ positionCountOf(assetState[account.id]!.data!) }}</p>
                </div>
              </div>
            </template>
            <template v-else-if="assetState[account.id]?.loading">
              <div class="flex items-center gap-2 px-3 py-3 text-footnote text-label-tertiary">
                <span class="size-3.5 animate-spin-slow rounded-full border-2 border-current border-t-transparent" />
                正在拉取账户资产…
              </div>
            </template>
            <template v-else-if="assetState[account.id]?.error">
              <div class="flex items-center justify-between gap-2 px-3 py-2.5 text-footnote">
                <span class="text-danger-text">资产拉取失败</span>
                <button type="button" class="font-medium text-accent-text hover:underline" @click.stop="loadAccountAssets(account)">重试</button>
              </div>
            </template>
            <button
              v-else
              type="button"
              class="flex w-full items-center gap-1.5 px-3 py-2.5 text-footnote font-medium text-accent-text transition-colors hover:bg-hover"
              :disabled="!account.has_mx_api_key || account.archived"
              @click.stop="loadAccountAssets(account)"
            >
              <svg class="size-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18" /><path d="m7 14 4-4 4 4 5-5" /></svg>
              查看账户资产
            </button>
          </div>
        </div>

        <!-- 配置指标 4 格 -->
        <dl class="mx-5 mt-1 mb-1 grid grid-cols-2 gap-2 pl-1 sm:grid-cols-4">
          <div class="rounded-[10px] bg-fill/70 p-2.5">
            <dt class="flex items-center gap-1 text-[11px] text-label-tertiary">
              <svg class="size-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /></svg>
              大模型
            </dt>
            <dd class="mt-0.5 truncate text-callout font-semibold text-label">{{ llmSourceText(account) }}</dd>
          </div>
          <div class="rounded-[10px] bg-fill/70 p-2.5">
            <dt class="flex items-center gap-1 text-[11px] text-label-tertiary">
              <svg class="size-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>
              允许市场
            </dt>
            <dd class="mt-0.5 truncate text-callout font-semibold text-label">{{ marketLabel(account.allowed_markets) }}</dd>
          </div>
          <div class="rounded-[10px] p-2.5 ring-1" :class="account.trade_enabled ? 'bg-success-soft/50 ring-success/25' : 'bg-danger-soft/40 ring-danger/20'">
            <dt class="flex items-center gap-1 text-[11px] text-label-tertiary">
              <svg class="size-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" /></svg>
              交易开关
            </dt>
            <dd class="mt-0.5 text-callout font-semibold" :class="account.trade_enabled ? 'text-success-text' : 'text-danger-text'">
              {{ account.trade_enabled ? '启用' : '停用' }}
            </dd>
          </div>
          <div class="rounded-[10px] p-2.5 ring-1" :class="account.capital_seal_enabled ? 'bg-warning-soft/50 ring-warning/25' : 'bg-fill/70 ring-transparent'">
            <dt class="flex items-center gap-1 text-[11px] text-label-tertiary">
              <svg class="size-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
              资金封印
            </dt>
            <dd class="mt-0.5 truncate text-callout font-semibold" :class="account.capital_seal_enabled ? 'text-warning-text' : 'text-label'">
              {{ account.capital_seal_enabled ? `¥${formatAmount(account.capital_seal_amount)}` : '关闭' }}
            </dd>
          </div>
        </dl>

        <!-- 测试结果（拆分后互不覆盖） -->
        <div v-if="mxTestResults[account.id] || llmTestResults[account.id]" class="mx-5 mt-2 flex flex-col gap-1 pl-1">
          <div
            v-if="mxTestResults[account.id]"
            class="flex items-center gap-1.5 rounded-[10px] border px-2.5 py-1.5 text-footnote"
            :class="mxTestResults[account.id].ok ? 'border-success/25 bg-success-soft/40 text-success-text' : 'border-danger/25 bg-danger-soft/40 text-danger-text'"
          >
            <span class="font-mono text-[11px] text-label-tertiary">妙想</span>
            <span class="truncate">{{ mxTestResults[account.id].message }}</span>
            <span v-if="mxTestResults[account.id].latency_ms != null" class="ml-auto shrink-0 tabular-nums text-[11px] text-label-tertiary">{{ mxTestResults[account.id].latency_ms }}ms</span>
          </div>
          <div
            v-if="llmTestResults[account.id]"
            class="flex items-center gap-1.5 rounded-[10px] border px-2.5 py-1.5 text-footnote"
            :class="llmTestResults[account.id].ok ? 'border-success/25 bg-success-soft/40 text-success-text' : 'border-danger/25 bg-danger-soft/40 text-danger-text'"
          >
            <span class="font-mono text-[11px] text-label-tertiary">LLM</span>
            <span class="truncate">{{ llmTestResults[account.id].message }}</span>
          </div>
        </div>

        <!-- 底部按钮行 -->
        <div class="mx-5 mt-3 mb-5 flex flex-wrap items-center gap-2 border-t border-separator pt-4 pl-1">
          <UiButton variant="ghost" size="sm" :disabled="account.archived" @click.stop="openEdit(account)">
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /><path d="m15 5 4 4" /></svg>
            编辑
          </UiButton>
          <UiButton variant="ghost" size="sm" :disabled="account.archived" @click.stop="openSkills(account)">
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" /></svg>
            Skills
          </UiButton>
          <UiButton
            variant="tinted"
            size="sm"
            :disabled="account.archived || testingMxId === account.id || !account.has_mx_api_key"
            :loading="testingMxId === account.id"
            @click.stop="testMx(account)"
          >
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 20h.01M7 20v-4M12 20v-8M17 20V8M22 20V4" /></svg>
            测试妙想
          </UiButton>
          <UiButton
            variant="tinted"
            size="sm"
            :disabled="account.archived || testingLlmId === account.id"
            :loading="testingLlmId === account.id"
            @click.stop="testLlm(account)"
          >
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><path d="M15 2v4M15 18v4M2 15h4M18 15h4M2 9h4M18 9h4M9 2v4M9 18v4" /></svg>
            测试 LLM
          </UiButton>
          <span class="flex-1" />
          <UiButton
            v-if="!account.archived && account.slug !== 'default'"
            variant="danger-soft"
            size="sm"
            @click.stop="archive(account)"
          >
            <svg class="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
            归档
          </UiButton>
          <UiButton
            v-else-if="account.archived"
            variant="tinted"
            size="sm"
            @click.stop="restore(account)"
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

            <!-- 章节导航（窄屏横向 chip 行） -->
            <nav class="flex gap-1 overflow-x-auto border-b border-separator bg-fill/40 px-3 py-2 sm:hidden">
              <a
                v-for="section in editorSections"
                :key="section.id"
                class="shrink-0 rounded-pill px-3 py-1 text-footnote font-medium transition-colors"
                :class="activeSectionId === section.id ? 'bg-accent-soft text-accent-text' : 'text-label-secondary hover:bg-hover'"
                :href="`#${section.id}`"
                @click.prevent="scrollToSection(section.id)"
              >
                {{ section.label }}
              </a>
            </nav>

            <!-- 抽屉内容：sm+ 双栏（左 nav + 右内容），窄屏单栏 -->
            <div ref="editorScrollRef" class="flex flex-1 overflow-hidden">
              <!-- 左侧 sticky 章节导航（sm+） -->
              <nav class="hidden w-40 shrink-0 flex-col gap-1 border-r border-separator bg-fill/30 p-3 sm:flex">
                <a
                  v-for="section in editorSections"
                  :key="section.id"
                  class="flex items-center gap-2 rounded-[10px] px-2.5 py-2 text-footnote font-medium transition-colors"
                  :class="activeSectionId === section.id ? 'bg-accent-soft text-accent-text' : 'text-label-secondary hover:bg-hover hover:text-label'"
                  :href="`#${section.id}`"
                  @click.prevent="scrollToSection(section.id)"
                >
                  <span class="flex size-5 items-center justify-center rounded-full text-[11px] font-semibold"
                    :class="activeSectionId === section.id ? 'bg-accent text-on-accent' : 'bg-fill-secondary text-label-tertiary'"
                  >{{ section.index }}</span>
                  {{ section.label }}
                </a>
              </nav>

              <!-- 右侧内容 -->
              <div ref="editorContentRef" class="flex-1 overflow-y-auto px-6 py-5">
                <div v-if="editorError" class="mb-4 rounded-[10px] border border-danger/25 bg-danger-soft px-3 py-2 text-footnote text-danger-text" role="alert">
                  {{ editorError }}
                </div>

                <form id="account-editor-form" class="account-form" @submit.prevent="save">
                  <!-- 基本信息 -->
                  <section :id="editorSections[0].id" :data-section-id="editorSections[0].id" class="account-section" :ref="(el) => (sectionRefs[0] = el as HTMLElement | null)">
                    <header class="account-section__header">
                      <div>
                        <h3 class="account-section__title"><span class="section-index">1</span>基本信息</h3>
                        <p class="account-section__desc">账户名称与唯一标识</p>
                      </div>
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
                  <section :id="editorSections[1].id" :data-section-id="editorSections[1].id" class="account-section" :ref="(el) => (sectionRefs[1] = el as HTMLElement | null)">
                    <header class="account-section__header">
                      <div>
                        <h3 class="account-section__title"><span class="section-index">2</span>妙想 Key</h3>
                        <p class="account-section__desc">该账户行情、持仓、订单与模拟交易统一使用的 apikey</p>
                      </div>
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
                  <section :id="editorSections[2].id" :data-section-id="editorSections[2].id" class="account-section" :ref="(el) => (sectionRefs[2] = el as HTMLElement | null)">
                    <header class="account-section__header">
                      <div>
                        <h3 class="account-section__title"><span class="section-index">3</span>独立大模型</h3>
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
                  <section :id="editorSections[3].id" :data-section-id="editorSections[3].id" class="account-section" :ref="(el) => (sectionRefs[3] = el as HTMLElement | null)">
                    <header class="account-section__header">
                      <div>
                        <h3 class="account-section__title"><span class="section-index">4</span>交易策略</h3>
                        <p class="account-section__desc">提示词、查询口径与选股范围</p>
                      </div>
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
                  <section :id="editorSections[4].id" :data-section-id="editorSections[4].id" class="account-section" :ref="(el) => (sectionRefs[4] = el as HTMLElement | null)">
                    <header class="account-section__header">
                      <div>
                        <h3 class="account-section__title"><span class="section-index">5</span>自动化上下文</h3>
                        <p class="account-section__desc">控制该账户自动化会话的历史记忆与压缩行为</p>
                      </div>
                    </header>

                    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <UiField label="最大上下文" help="自动化会话上下文窗口大小；后端按 85% 触发压缩，默认 128000。">
                        <input
                          v-model.number="draft.automation_context_window_tokens"
                          type="number"
                          min="4096"
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
                  <section :id="editorSections[5].id" :data-section-id="editorSections[5].id" class="account-section account-section--last" :ref="(el) => (sectionRefs[5] = el as HTMLElement | null)">
                    <header class="account-section__header">
                      <div>
                        <h3 class="account-section__title"><span class="section-index">6</span>风控与通知</h3>
                        <p class="account-section__desc">资金封印与 Telegram 通知</p>
                      </div>
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
              <template v-else-if="skillsList">
                <section v-for="group in skillsGroups" :key="group.role" class="mb-5">
                  <header class="mb-2 flex items-center gap-2">
                    <h3 class="m-0 text-footnote font-semibold text-label">{{ group.title }}</h3>
                    <span class="rounded-full bg-fill px-2 py-0.5 text-[11px] text-label-tertiary">{{ group.items.length }}</span>
                  </header>
                  <div class="space-y-2">
                    <div
                      v-for="skill in group.items"
                      :key="skill.id"
                      class="flex items-center justify-between rounded-[10px] border border-separator px-3 py-2.5 transition-colors"
                      :class="[
                        skill.always_enabled ? 'border-l-2 border-l-success/60' : '',
                        skill.global_disabled ? 'border-l-2 border-l-danger/60 opacity-70' : '',
                      ]"
                    >
                      <div class="min-w-0">
                        <div class="flex items-center gap-2">
                          <p class="m-0 text-callout font-medium text-label">{{ skill.name }}</p>
                          <UiBadge v-if="skill.always_enabled" tone="success">运行时</UiBadge>
                          <UiBadge v-else-if="skill.global_disabled" tone="danger">硬禁用</UiBadge>
                        </div>
                        <p class="mt-0.5 text-footnote text-label-tertiary">
                          <span class="font-mono">{{ skill.id }}</span>
                        </p>
                      </div>
                      <UiToggle
                        :model-value="skill.effective_enabled"
                        :disabled="skill.always_enabled || skill.global_disabled"
                        @update:model-value="toggleSkill(skill.id)"
                      />
                    </div>
                  </div>
                </section>
              </template>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'

import { api } from '@/services/api'
import type {
  AccountLlmTestResult,
  AccountMxTestResult,
  AccountOverview,
  AccountSkillList,
  AccountSkillStatus,
  LatestRunSummary,
  TradingAccount,
  TradingAccountPayload,
} from '@/types'
import { useTradingAccountsStore } from '@/stores/tradingAccounts'
import { formatMoney, formatPercent } from '@/utils/formatters'
import UiBadge from '@/components/ui/UiBadge.vue'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiField from '@/components/ui/UiField.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiToggle from '@/components/ui/UiToggle.vue'

const router = useRouter()
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
// 测试结果拆分：妙想与 LLM 独立，互不覆盖
const mxTestResults = reactive<Record<number, AccountMxTestResult>>({})
const llmTestResults = reactive<Record<number, AccountLlmTestResult>>({})
const testingMxId = ref<number | null>(null)
const testingLlmId = ref<number | null>(null)

// 资产懒加载：每账户一份独立状态
interface AssetState {
  loading: boolean
  data: AccountOverview | null
  error: string | null
}
const assetState = reactive<Record<number, AssetState>>({})

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

// 编辑抽屉章节定义
interface EditorSection {
  id: string
  label: string
  index: number
}
const editorSections: EditorSection[] = [
  { id: 'sec-basic', label: '基本信息', index: 1 },
  { id: 'sec-mxkey', label: '妙想 Key', index: 2 },
  { id: 'sec-llm', label: '独立大模型', index: 3 },
  { id: 'sec-strategy', label: '交易策略', index: 4 },
  { id: 'sec-automation', label: '自动化上下文', index: 5 },
  { id: 'sec-risk', label: '风控与通知', index: 6 },
]
const editorContentRef = ref<HTMLElement | null>(null)
const sectionRefs = ref<Array<HTMLElement | null>>([])
const activeSectionId = ref<string>(editorSections[0].id)
let sectionObserver: IntersectionObserver | null = null

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

// ── 展示辅助 ──────────────────────────────────────────────────────────────

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

function formatSignedMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  const formatted = formatMoney(Math.abs(value))
  if (value > 0) return `+${formatted}`
  if (value < 0) return `-${formatted}`
  return formatted
}

function profitClass(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return ''
  if (value > 0) return 'text-profit-up-text'
  if (value < 0) return 'text-profit-down-text'
  return ''
}

function positionCountOf(overview: AccountOverview): number {
  return (overview.positions ?? []).filter((p) => (p.volume ?? 0) > 0).length
}

function statusBarClass(account: TradingAccount): string {
  if (account.archived) return 'bg-label-quaternary'
  if (!account.enabled) return 'bg-warning'
  return 'bg-success'
}

function runTypeLabel(runType: string): string {
  if (runType === 'trade') return '交易'
  if (runType === 'chat') return '对话'
  return '分析'
}

function runStatusToneClass(status: string): string {
  if (status === 'completed' || status === 'success') return 'text-success-text'
  if (status === 'failed' || status === 'error') return 'text-danger-text'
  if (status === 'running' || status === 'pending') return 'text-warning-text'
  return 'text-label-secondary'
}

function runStatusDotClass(status: string): string {
  if (status === 'completed' || status === 'success') return 'bg-success'
  if (status === 'failed' || status === 'error') return 'bg-danger'
  if (status === 'running' || status === 'pending') return 'bg-warning animate-pulse'
  return 'bg-label-quaternary'
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '从未运行'
  // 后端 datetime 可能无时区后缀，按 UTC 解析
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`
  const then = new Date(normalized).getTime()
  if (!Number.isFinite(then)) return '--'
  const diff = Date.now() - then
  if (diff < 0) return '刚刚'
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < hour) return `${Math.max(1, Math.floor(diff / minute))} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`
  const d = new Date(then)
  return `${d.getMonth() + 1}-${d.getDate()}`
}

// ── 跳转 ────────────────────────────────────────────────────────────────────

function goToAccountTasks(account: TradingAccount) {
  if (!account.latest_run) return
  store.selectAccount(account.id)
  router.push('/tasks')
}

// ── 资产懒加载 ──────────────────────────────────────────────────────────────

async function loadAccountAssets(account: TradingAccount) {
  const id = account.id
  assetState[id] = { loading: true, data: null, error: null }
  try {
    const overview = await api.getAccountOverview(account.id)
    assetState[id] = { loading: false, data: overview, error: null }
  } catch (exception) {
    assetState[id] = { loading: false, data: null, error: (exception as Error).message }
  }
}

// ── Skills 分组 ───────────────────────────────────────────────────────────────

const skillsGroups = computed(() => {
  const list = skillsList.value?.global_available ?? []
  const runtime = list.filter((s: AccountSkillStatus) => s.role === 'runtime')
  const standard = list.filter((s: AccountSkillStatus) => s.role === 'standard')
  return [
    { role: 'runtime' as const, title: '运行时技能', items: runtime },
    { role: 'standard' as const, title: '业务技能', items: standard },
  ]
})

// ── 抽屉章节导航 ───────────────────────────────────────────────────────────────

function scrollToSection(sectionId: string) {
  const idx = editorSections.findIndex((s) => s.id === sectionId)
  const el = idx >= 0 ? sectionRefs.value[idx] : null
  if (el && editorContentRef.value) {
    editorContentRef.value.scrollTo({
      top: el.offsetTop - 8,
      behavior: 'smooth',
    })
    activeSectionId.value = sectionId
  }
}

function setupSectionObserver() {
  teardownSectionObserver()
  const root = editorContentRef.value
  if (!root) return
  sectionObserver = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((e) => e.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)
      if (visible[0]) {
        const id = (visible[0].target as HTMLElement).dataset.sectionId
        if (id) activeSectionId.value = id
      }
    },
    { root, rootMargin: '-15% 0px -70% 0px', threshold: [0, 0.25, 0.5, 1] },
  )
  sectionRefs.value.forEach((el) => {
    if (el) sectionObserver?.observe(el)
  })
}

function teardownSectionObserver() {
  sectionObserver?.disconnect()
  sectionObserver = null
}

watch(showEditor, async (visible) => {
  if (visible) {
    await nextTick()
    setupSectionObserver()
  } else {
    teardownSectionObserver()
  }
})

onBeforeUnmount(() => teardownSectionObserver())

// ── 草稿与表单 ──────────────────────────────────────────────────────────────────

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
  mxTestResults[account.id] = { ok: false, message: '测试中…' }
  testingMxId.value = account.id
  try {
    mxTestResults[account.id] = await api.testAccountMx(account.id)
  } catch (exception) {
    mxTestResults[account.id] = { ok: false, message: (exception as Error).message }
  } finally {
    testingMxId.value = null
  }
}

async function testLlm(account: TradingAccount) {
  llmTestResults[account.id] = { ok: false, message: '测试中…', source: 'none' }
  testingLlmId.value = account.id
  try {
    llmTestResults[account.id] = await api.testAccountLlm(account.id)
  } catch (exception) {
    llmTestResults[account.id] = { ok: false, message: (exception as Error).message, source: 'none' }
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
  @apply m-0 flex items-center gap-2 text-title-3 font-semibold tracking-tight text-label;
}

.section-index {
  @apply flex size-6 items-center justify-center rounded-full bg-accent-soft text-footnote font-semibold text-accent-text;
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
