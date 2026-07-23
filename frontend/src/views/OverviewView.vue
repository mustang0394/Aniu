<template>
  <div class="space-y-5 sm:space-y-6">
    <UiPageHeader title="账户总览" kicker="Overview" description="模拟账户资产、持仓与委托快照">
      <UiButton
        variant="tinted"
        size="sm"
        :loading="accountRefreshing"
        :disabled="accountRefreshing || !canManualRefreshAccount"
        :title="canManualRefreshAccount ? '手动刷新账户信息' : `${accountRefreshCooldownText}后可刷新`"
        @click="handleManualRefresh"
      >
        {{ canManualRefreshAccount ? '刷新' : '冷却中' }}
      </UiButton>
    </UiPageHeader>

    <!-- Stat cards -->
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <UiStatCard
        label="账户状态"
        tone="status"
        :value="formatDays(account?.operating_days)"
        description="运作天数"
        :rows="[
          { label: '开户日期', value: account?.open_date || '--' },
          { label: '交易成功率', value: formatPercent(tradeSuccessRate), valueClass: profitClass(tradeSuccessRate != null && tradeSuccessRate >= 0.5 ? 1 : tradeSuccessRate != null ? -1 : null) },
        ]"
      />
      <UiStatCard
        label="资金规模"
        tone="capital"
        :value="formatMoney(account?.total_assets)"
        :value-class="profitClass(getAssetDelta(account?.total_assets, account?.initial_capital))"
        description="账户总资产"
        :rows="[
          { label: '初始资金', value: formatMoney(account?.initial_capital) },
          { label: '初始净值', value: '1.000' },
        ]"
      />
      <UiStatCard
        label="仓位情况"
        tone="position"
        :value="formatMoney(account?.total_market_value)"
        description="总持仓市值"
        :rows="[
          { label: '现金余额', value: formatMoney(account?.cash_balance) },
          { label: '总仓位比例', value: formatPercent(account?.total_position_ratio) },
        ]"
      />
      <UiStatCard
        label="累计表现"
        tone="cumulative"
        :value="formatSignedMoney(getAssetDelta(account?.total_assets, account?.initial_capital))"
        :value-class="profitClass(getAssetDelta(account?.total_assets, account?.initial_capital))"
        description="总收益金额"
        :rows="[
          { label: '总收益率', value: formatPercent(account?.total_return_ratio), valueClass: profitClass(account?.total_return_ratio) },
          { label: '当前净值', value: formatNav(account?.nav), valueClass: profitClass(getNavDelta(account?.nav)) },
        ]"
      />
      <UiStatCard
        label="今日表现"
        tone="daily"
        :value="formatSignedMoney(account?.daily_profit)"
        :value-class="profitClass(account?.daily_profit)"
        :description="`当日盈亏（${account?.daily_profit_trade_date || '--'}）`"
        :rows="[
          { label: '当日收益率', value: formatPercent(account?.daily_return_ratio), valueClass: profitClass(account?.daily_return_ratio) },
          { label: '今日交易次数', value: formatTradeCount(todayTradeCount) },
        ]"
      />
    </div>

    <!-- Positions -->
    <UiPanel title="持仓情况" kicker="Positions">
      <template v-if="displayPositions.length">
        <div class="hidden overflow-x-auto md:block">
          <table class="w-full min-w-[900px] border-collapse text-left text-body">
            <thead>
              <tr class="border-b border-separator text-caption font-semibold uppercase tracking-wide text-label-tertiary">
                <th class="px-3 py-3 font-semibold">名称 / 代码</th>
                <th class="px-3 py-3 font-semibold">持仓市值</th>
                <th class="px-3 py-3 font-semibold">持仓 / 可卖</th>
                <th class="px-3 py-3 font-semibold">当日盈亏</th>
                <th class="px-3 py-3 font-semibold">总盈亏</th>
                <th class="px-3 py-3 font-semibold">现价 / 成本</th>
                <th class="px-3 py-3 font-semibold">仓位占比</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="pos in displayPositions"
                :key="pos.symbol"
                class="border-b border-separator/70 transition-colors hover:bg-hover"
              >
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <strong class="font-semibold text-label">{{ pos.name }}</strong>
                    <span class="text-caption text-label-tertiary">{{ pos.symbol }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5 tabular-nums font-medium text-label">{{ formatMoney(pos.amount) }}</td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-medium text-label">{{ formatVolume(pos.volume) }}</span>
                    <span class="text-caption text-label-tertiary">可卖 {{ formatVolume(pos.available_volume) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-semibold" :class="profitClass(pos.day_profit)">{{ formatSignedMoney(pos.day_profit) }}</span>
                    <span class="text-caption" :class="profitClass(pos.day_profit_ratio)">{{ formatPercent(pos.day_profit_ratio) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-semibold" :class="profitClass(pos.profit)">{{ formatSignedMoney(pos.profit) }}</span>
                    <span class="text-caption" :class="profitClass(pos.profit_ratio)">{{ formatPercent(pos.profit_ratio) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-medium text-label">{{ formatMoney(pos.current_price) }}</span>
                    <span class="text-caption text-label-tertiary">{{ formatMoney(pos.cost_price) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5 tabular-nums font-medium text-label">{{ getPositionRatioText(pos.position_ratio) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="flex flex-col gap-3 md:hidden">
          <article
            v-for="pos in displayPositions"
            :key="`m-${pos.symbol}`"
            class="rounded-[14px] border border-separator bg-card-solid p-4 shadow-sm"
          >
            <header class="mb-3 flex flex-wrap items-baseline gap-2 border-b border-separator pb-2.5">
              <strong class="text-callout font-semibold text-label">{{ pos.name }}</strong>
              <span class="text-caption text-label-tertiary">{{ pos.symbol }}</span>
            </header>
            <dl class="grid grid-cols-2 gap-x-4 gap-y-2.5">
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">持仓市值</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(pos.amount) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">仓位占比</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ getPositionRatioText(pos.position_ratio) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">持仓股数</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatVolume(pos.volume) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">可卖股数</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatVolume(pos.available_volume) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">当日盈亏</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums" :class="profitClass(pos.day_profit)">{{ formatSignedMoney(pos.day_profit) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">当日收益率</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums" :class="profitClass(pos.day_profit_ratio)">{{ formatPercent(pos.day_profit_ratio) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">总盈亏</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums" :class="profitClass(pos.profit)">{{ formatSignedMoney(pos.profit) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">总收益率</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums" :class="profitClass(pos.profit_ratio)">{{ formatPercent(pos.profit_ratio) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">当前价</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(pos.current_price) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">成本价</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(pos.cost_price) }}</dd></div>
            </dl>
          </article>
        </div>
      </template>
      <UiEmpty
        v-else
        title="暂无持仓"
        :description="errorMessage || '账户接口暂未返回真实持仓数据。请检查后端账户接口或先执行一次任务刷新账户快照。'"
      />
    </UiPanel>

    <!-- Trades -->
    <UiPanel title="交易信息" kicker="Trades">
      <template v-if="displayTradeSummaries.length">
        <div class="hidden overflow-x-auto md:block">
          <table class="w-full min-w-[900px] border-collapse text-left text-body">
            <thead>
              <tr class="border-b border-separator text-caption font-semibold uppercase tracking-wide text-label-tertiary">
                <th class="px-3 py-3 font-semibold">名称 / 代码</th>
                <th class="px-3 py-3 font-semibold">买入时间</th>
                <th class="px-3 py-3 font-semibold">卖出时间</th>
                <th class="px-3 py-3 font-semibold">成交股数</th>
                <th class="px-3 py-3 font-semibold">卖出 / 买入均价</th>
                <th class="px-3 py-3 font-semibold">卖出 / 买入金额</th>
                <th class="px-3 py-3 font-semibold">收益 / 收益率</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="trade in displayTradeSummaries"
                :key="`${trade.symbol}-${trade.closed_at || '--'}`"
                class="border-b border-separator/70 transition-colors hover:bg-hover"
              >
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <strong class="font-semibold text-label">{{ trade.name }}</strong>
                    <span class="text-caption text-label-tertiary">{{ trade.symbol }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5 text-label-secondary">{{ formatMinuteTime(trade.opened_at) }}</td>
                <td class="px-3 py-3.5 text-label-secondary">{{ formatMinuteTime(trade.closed_at) }}</td>
                <td class="px-3 py-3.5 tabular-nums font-medium text-label">{{ formatVolume(trade.volume) }}</td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-medium text-label">{{ formatMoney(trade.sell_price) }}</span>
                    <span class="text-caption text-label-tertiary">{{ formatMoney(trade.buy_price) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-medium text-label">{{ formatMoney(trade.sell_amount) }}</span>
                    <span class="text-caption text-label-tertiary">{{ formatMoney(trade.buy_amount) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-semibold" :class="profitClass(trade.profit)">{{ formatSignedMoney(trade.profit) }}</span>
                    <span class="text-caption" :class="profitClass(trade.profit_ratio)">{{ formatPercent(trade.profit_ratio) }}</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="flex flex-col gap-3 md:hidden">
          <article
            v-for="trade in displayTradeSummaries"
            :key="`m-${trade.symbol}-${trade.closed_at || '--'}`"
            class="rounded-[14px] border border-separator bg-card-solid p-4 shadow-sm"
          >
            <header class="mb-3 flex flex-wrap items-baseline gap-2 border-b border-separator pb-2.5">
              <strong class="text-callout font-semibold text-label">{{ trade.name }}</strong>
              <span class="text-caption text-label-tertiary">{{ trade.symbol }}</span>
            </header>
            <dl class="grid grid-cols-2 gap-x-4 gap-y-2.5">
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">买入时间</dt><dd class="m-0 mt-0.5 font-medium text-label">{{ formatMinuteTime(trade.opened_at) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">卖出时间</dt><dd class="m-0 mt-0.5 font-medium text-label">{{ formatMinuteTime(trade.closed_at) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">成交股数</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatVolume(trade.volume) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">收益</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums" :class="profitClass(trade.profit)">{{ formatSignedMoney(trade.profit) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">收益率</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums" :class="profitClass(trade.profit_ratio)">{{ formatPercent(trade.profit_ratio) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">卖出均价</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(trade.sell_price) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">买入均价</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(trade.buy_price) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">卖出金额</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(trade.sell_amount) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">买入金额</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(trade.buy_amount) }}</dd></div>
            </dl>
          </article>
        </div>
      </template>
      <UiEmpty v-else title="暂无闭环交易" description="买入后全部卖出的股票会展示在这里。" />
    </UiPanel>

    <!-- Orders -->
    <UiPanel title="委托信息" kicker="Orders">
      <template v-if="displayOrders.length">
        <div class="hidden overflow-x-auto md:block">
          <table class="w-full min-w-[800px] border-collapse text-left text-body">
            <thead>
              <tr class="border-b border-separator text-caption font-semibold uppercase tracking-wide text-label-tertiary">
                <th class="px-3 py-3 font-semibold">名称 / 代码</th>
                <th class="px-3 py-3 font-semibold">委托时间</th>
                <th class="px-3 py-3 font-semibold">方向</th>
                <th class="px-3 py-3 font-semibold">委托价 / 量</th>
                <th class="px-3 py-3 font-semibold">成交价 / 量</th>
                <th class="px-3 py-3 font-semibold">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="order in displayOrders"
                :key="order.order_id"
                class="border-b border-separator/70 transition-colors hover:bg-hover"
              >
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <strong class="font-semibold text-label">{{ order.name }}</strong>
                    <span class="text-caption text-label-tertiary">{{ order.symbol }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5 text-label-secondary">{{ order.order_time || '--' }}</td>
                <td class="px-3 py-3.5">
                  <span
                    class="inline-flex rounded-pill px-2.5 py-0.5 text-[11px] font-semibold"
                    :class="order.side === 'buy' ? 'bg-profit-up-soft text-profit-up-text' : 'bg-profit-down-soft text-profit-down-text'"
                  >
                    {{ order.side_text }}
                  </span>
                </td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-medium text-label">{{ formatMoney(order.order_price) }}</span>
                    <span class="text-caption text-label-tertiary">{{ formatVolume(order.order_quantity) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5">
                  <div class="flex flex-col gap-0.5">
                    <span class="tabular-nums font-medium text-label">{{ formatMoney(order.filled_price) }}</span>
                    <span class="text-caption text-label-tertiary">{{ formatVolume(order.filled_quantity) }}</span>
                  </div>
                </td>
                <td class="px-3 py-3.5">
                  <span
                    class="inline-flex rounded-pill px-2.5 py-0.5 text-[11px] font-semibold"
                    :class="order.status_text === '已成交' ? 'bg-success-soft text-success-text' : 'bg-danger-soft text-danger-text'"
                  >
                    {{ order.status_text || '--' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="flex flex-col gap-3 md:hidden">
          <article
            v-for="order in displayOrders"
            :key="`m-${order.order_id}`"
            class="rounded-[14px] border border-separator bg-card-solid p-4 shadow-sm"
          >
            <header class="mb-3 flex flex-wrap items-baseline gap-2 border-b border-separator pb-2.5">
              <strong class="text-callout font-semibold text-label">{{ order.name }}</strong>
              <span class="text-caption text-label-tertiary">{{ order.symbol }}</span>
              <span
                class="ml-auto inline-flex rounded-pill px-2.5 py-0.5 text-[11px] font-semibold"
                :class="order.side === 'buy' ? 'bg-profit-up-soft text-profit-up-text' : 'bg-profit-down-soft text-profit-down-text'"
              >
                {{ order.side_text }}
              </span>
            </header>
            <dl class="grid grid-cols-2 gap-x-4 gap-y-2.5">
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">委托时间</dt><dd class="m-0 mt-0.5 font-medium text-label">{{ order.order_time || '--' }}</dd></div>
              <div>
                <dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">委托状态</dt>
                <dd class="m-0 mt-0.5">
                  <span
                    class="inline-flex rounded-pill px-2.5 py-0.5 text-[11px] font-semibold"
                    :class="order.status_text === '已成交' ? 'bg-success-soft text-success-text' : 'bg-danger-soft text-danger-text'"
                  >{{ order.status_text || '--' }}</span>
                </dd>
              </div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">委托价格</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(order.order_price) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">委托数量</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatVolume(order.order_quantity) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">成交价格</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatMoney(order.filled_price) }}</dd></div>
              <div><dt class="text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">成交数量</dt><dd class="m-0 mt-0.5 font-semibold tabular-nums text-label">{{ formatVolume(order.filled_quantity) }}</dd></div>
            </dl>
          </article>
        </div>
      </template>
      <UiEmpty v-else title="暂无委托" description="完成一次买入、卖出或撤单后，委托信息会显示在这里。" />
    </UiPanel>

    <!-- Runtime overview -->
    <UiPanel title="运行总览" kicker="Runtime Overview">
      <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <div
          v-for="section in runtimeSections"
          :key="section.title"
          class="rounded-[16px] border border-separator bg-fill/50 p-4"
        >
          <h3 class="m-0 mb-3 text-title-3 font-semibold text-label">{{ section.title }}</h3>
          <div class="grid grid-cols-2 gap-3">
            <div v-for="stat in section.stats" :key="stat.label">
              <p class="m-0 text-[11px] font-semibold uppercase tracking-wide text-label-tertiary">{{ stat.label }}</p>
              <p
                class="m-0 mt-1 text-body font-semibold tabular-nums text-label"
                :class="stat.valueClass"
              >
                {{ stat.value }}
              </p>
            </div>
          </div>
          <div class="mt-3 flex flex-wrap gap-2 border-t border-separator pt-3">
            <span
              v-for="tok in section.tokens"
              :key="tok.label"
              class="inline-flex items-center gap-1.5 rounded-pill bg-card-solid px-2.5 py-1 text-caption"
              :class="tok.emphasis ? 'font-semibold text-accent-text ring-1 ring-accent/20' : 'text-label-secondary'"
            >
              <span class="text-label-tertiary">{{ tok.label }}</span>
              <span class="tabular-nums">{{ tok.value }}</span>
            </span>
          </div>
        </div>
      </div>
    </UiPanel>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useAppStore } from '@/stores/legacy'
import { formatMinuteTime, formatMoney, formatPercent, formatTime } from '@/utils/formatters'
import UiButton from '@/components/ui/UiButton.vue'
import UiEmpty from '@/components/ui/UiEmpty.vue'
import UiPageHeader from '@/components/ui/UiPageHeader.vue'
import UiPanel from '@/components/ui/UiPanel.vue'
import UiStatCard from '@/components/ui/UiStatCard.vue'

const store = useAppStore()
const { account, runtimeOverview, errorMessage, accountRefreshing, canManualRefreshAccount, accountRefreshCooldownText } = storeToRefs(store)

const displayPositions = computed(() => account.value.positions.filter((position) => (position.volume ?? 0) > 0))
const displayOrders = computed(() => account.value.orders)
const displayTradeSummaries = computed(() => account.value.trade_summaries)
const tradeSuccessRate = computed(() => {
  const total = displayTradeSummaries.value.length
  if (total === 0) return null
  const profitableCount = displayTradeSummaries.value.filter((trade) => trade.profit > 0).length
  return profitableCount / total
})
const todayTradeCount = computed(() => {
  const now = new Date()
  const todayPrefix = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  return displayOrders.value.filter((order) => (
    Boolean(order.order_time?.startsWith(todayPrefix))
    && (order.filled_quantity ?? 0) > 0
  )).length
})

const runtimeSections = computed(() => {
  const ro = runtimeOverview.value
  const lastStatus = ro.last_run.status
  const statusClass =
    lastStatus === 'completed'
      ? 'text-success-text'
      : lastStatus === 'failed'
        ? 'text-danger-text'
        : ''

  return [
    {
      title: '最近一次分析',
      stats: [
        { label: '启动时间', value: formatRuntimeTime(ro.last_run.start_time) },
        { label: '停止时间', value: formatRuntimeTime(ro.last_run.end_time) },
        { label: '状态', value: ro.last_run.status_text, valueClass: statusClass },
        { label: '持续时长', value: ro.last_run.duration },
      ],
      tokens: [
        { label: '输入', value: ro.last_run.input_tokens },
        { label: '输出', value: ro.last_run.output_tokens },
        { label: '总量', value: ro.last_run.total_tokens, emphasis: true },
      ],
    },
    {
      title: '今日统计',
      stats: [
        { label: 'AI分析', value: `${ro.today.analysis_count}次` },
        { label: '接口调用', value: `${ro.today.api_calls}次` },
        { label: '交易执行', value: `${ro.today.trades}次` },
        { label: '成功率', value: `${ro.today.success_rate}%` },
      ],
      tokens: [
        { label: '输入', value: ro.today.input_tokens },
        { label: '输出', value: ro.today.output_tokens },
        { label: '总量', value: ro.today.total_tokens, emphasis: true },
      ],
    },
    {
      title: '近3个交易日',
      stats: [
        { label: 'AI分析', value: `${ro.recent_3_days.analysis_count}次` },
        { label: '接口调用', value: `${ro.recent_3_days.api_calls}次` },
        { label: '交易执行', value: `${ro.recent_3_days.trades}次` },
        { label: '成功率', value: `${ro.recent_3_days.success_rate}%` },
      ],
      tokens: [
        { label: '输入', value: ro.recent_3_days.input_tokens },
        { label: '输出', value: ro.recent_3_days.output_tokens },
        { label: '总量', value: ro.recent_3_days.total_tokens, emphasis: true },
      ],
    },
    {
      title: '近7个交易日',
      stats: [
        { label: 'AI分析', value: `${ro.recent_7_days.analysis_count}次` },
        { label: '接口调用', value: `${ro.recent_7_days.api_calls}次` },
        { label: '交易执行', value: `${ro.recent_7_days.trades}次` },
        { label: '成功率', value: `${ro.recent_7_days.success_rate}%` },
      ],
      tokens: [
        { label: '输入', value: ro.recent_7_days.input_tokens },
        { label: '输出', value: ro.recent_7_days.output_tokens },
        { label: '总量', value: ro.recent_7_days.total_tokens, emphasis: true },
      ],
    },
  ]
})

let cooldownTimer: number | null = null

function formatSignedMoney(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  const formatted = formatMoney(Math.abs(value))
  if (value > 0) return `+${formatted}`
  if (value < 0) return `-${formatted}`
  return formatted
}

function profitClass(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return ''
  if (value > 0) return 'text-profit-up-text'
  if (value < 0) return 'text-profit-down-text'
  return ''
}

function getPositionRatioText(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return `${(value * 100).toFixed(1)}%`
}

function formatVolume(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return `${value.toLocaleString()} 股`
}

function formatNav(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return value.toFixed(3)
}

function formatDays(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return `${value} 天`
}

function formatTradeCount(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return '--'
  return `${value} 次`
}

function formatRuntimeTime(value: string | null | undefined) {
  const text = formatTime(value)
  if (text === '从未运行' || text === '--') return text
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}:\d{2}:\d{2})$/)
  if (!match) return text
  const [, , month, day, time] = match
  return `${month}-${day} ${time}`
}

function getAssetDelta(totalAssets: number | null | undefined, initialCapital: number | null | undefined) {
  if (
    totalAssets === null || totalAssets === undefined || Number.isNaN(totalAssets)
    || initialCapital === null || initialCapital === undefined || Number.isNaN(initialCapital)
  ) {
    return null
  }
  return totalAssets - initialCapital
}

function getNavDelta(nav: number | null | undefined) {
  if (nav === null || nav === undefined || Number.isNaN(nav)) return null
  return nav - 1
}

onMounted(async () => {
  const tasks: Promise<unknown>[] = []
  if (account.value.positions.length === 0) {
    tasks.push(store.refreshAccountData())
  }
  tasks.push(store.refreshRuntimeOverview())
  if (tasks.length > 0) {
    const results = await Promise.allSettled(tasks)
    const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
    if (failed) {
      errorMessage.value = failed.reason instanceof Error ? failed.reason.message : '总览数据加载失败'
    }
  }
  cooldownTimer = window.setInterval(() => {
    store.touchAccountRefreshTick()
  }, 60 * 1000)
})

onUnmounted(() => {
  if (cooldownTimer !== null) window.clearInterval(cooldownTimer)
})

async function handleManualRefresh() {
  if (!canManualRefreshAccount.value || accountRefreshing.value) return
  try {
    await store.refreshAccountDataWithCooldown()
  } catch {
    // Error message is already synchronized in the store.
  }
}
</script>
