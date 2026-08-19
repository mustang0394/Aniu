import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'

import { api } from '@/services/api'
import type {
  AccountOverview,
  GlobalOverview,
  RunDetail,
  RuntimeOverview,
  ScheduleConfig,
  TradingAccount,
} from '@/types'

function defaultOverview(): AccountOverview {
  return {
    open_date: null,
    daily_profit_trade_date: null,
    operating_days: null,
    initial_capital: null,
    total_assets: null,
    total_market_value: null,
    cash_balance: null,
    total_position_ratio: null,
    holding_profit: null,
    total_return_ratio: null,
    nav: null,
    daily_profit: null,
    daily_return_ratio: null,
    positions: [],
    orders: [],
    trade_summaries: [],
    errors: [],
  }
}

interface AccountData {
  overview: AccountOverview | null
  runtimeOverview: RuntimeOverview | null
  schedules: ScheduleConfig[]
  schedulesLoading: boolean
  runs: RunDetail[] | null
  loading: boolean
  error: string
}

function emptyAccountData(): AccountData {
  return {
    overview: null,
    runtimeOverview: null,
    schedules: [],
    schedulesLoading: false,
    runs: null,
    loading: false,
    error: '',
  }
}

export const useTradingAccountsStore = defineStore('tradingAccounts', () => {
  const accounts = ref<TradingAccount[]>([])
  const selectedAccountId = ref<number | null>(null)
  const accountsLoaded = ref(false)
  const loadingAccounts = ref(false)
  const globalOverview = ref<GlobalOverview | null>(null)
  const runDetailsMap = reactive<Record<number, Record<number, RunDetail>>>({})

  const accountData = reactive<Record<number, AccountData>>({})

  const selectedAccount = computed<TradingAccount | null>(() => {
    if (selectedAccountId.value === null) {
      return null
    }
    return accounts.value.find((account) => account.id === selectedAccountId.value) ?? null
  })
  const activeAccounts = computed(() => accounts.value.filter((account) => !account.archived))
  const hasMultipleAccounts = computed(() => activeAccounts.value.length > 1)

  function account(id: number): AccountData {
    if (!accountData[id]) {
      accountData[id] = emptyAccountData()
    }
    return accountData[id]
  }

  async function loadAccounts(options?: { preferSelection?: boolean }) {
    loadingAccounts.value = true
    try {
      const payload = await api.listAccounts()
      accounts.value = payload
      accountsLoaded.value = true
      if (accounts.value.length > 0) {
        const current = selectedAccountId.value
        if (
          current === null ||
          !accounts.value.some((item) => item.id === current && !item.archived)
        ) {
          selectedAccountId.value = accounts.value[0].id
        }
      } else {
        selectedAccountId.value = null
      }
      return payload
    } finally {
      loadingAccounts.value = false
    }
  }

  function selectAccount(accountId: number | null) {
    selectedAccountId.value = accountId
  }

  async function refreshAccountOverview(accountId: number, forceRefresh = false) {
    const data = account(accountId)
    data.loading = true
    data.error = ''
    try {
      data.overview = await api.getAccountOverview(accountId, forceRefresh)
      return data.overview
    } catch (error) {
      data.error = (error as Error).message
      throw error
    } finally {
      data.loading = false
    }
  }

  async function refreshSelectedOverview(forceRefresh = false) {
    if (selectedAccountId.value === null) {
      return null
    }
    return refreshAccountOverview(selectedAccountId.value, forceRefresh)
  }

  async function refreshRuntimeOverview(accountId: number) {
    const data = account(accountId)
    try {
      data.runtimeOverview = await api.getAccountRuntimeOverview(accountId)
    } catch {
      data.runtimeOverview = null
    }
    return data.runtimeOverview
  }

  async function loadSchedules(accountId: number) {
    const data = account(accountId)
    data.schedulesLoading = true
    try {
      data.schedules = await api.getAccountSchedule(accountId)
      return data.schedules
    } catch (error) {
      data.error = (error as Error).message
      throw error
    } finally {
      data.schedulesLoading = false
    }
  }

  async function saveSchedules(accountId: number, payload: Array<Partial<ScheduleConfig>>) {
    const result = await api.updateAccountSchedule(accountId, payload)
    account(accountId).schedules = result
    return result
  }

  async function loadRuns(accountId: number) {
    const data = account(accountId)
    data.loading = true
    try {
      const runs = await api.listAccountRuns(accountId, { limit: 50 })
      data.runs = runs as RunDetail[]
      return data.runs
    } catch (error) {
      data.error = (error as Error).message
      throw error
    } finally {
      data.loading = false
    }
  }

  async function loadRunDetail(accountId: number, runId: number, options?: { force?: boolean }) {
    const map = runDetailsMap[accountId] ?? (runDetailsMap[accountId] = {})
    if (!options?.force && map[runId]) {
      return map[runId]
    }
    const detail = await api.getAccountRun(accountId, runId)
    map[runId] = detail
    return detail
  }

  function clearAccountCache(accountId: number) {
    delete accountData[accountId]
    delete runDetailsMap[accountId]
  }

  /** 删除运行记录后调用：清除该 run 的详情缓存，防止 rowid 复用时命中旧数据。 */
  function evictRunDetail(accountId: number, runId: number) {
    const map = runDetailsMap[accountId]
    if (map) {
      delete map[runId]
    }
  }

  async function refreshGlobalOverview(forceRefresh = false) {
    globalOverview.value = await api.getGlobalOverview(forceRefresh)
    return globalOverview.value
  }

  function reset() {
    accounts.value = []
    selectedAccountId.value = null
    accountsLoaded.value = false
    globalOverview.value = null
    for (const key of Object.keys(accountData)) {
      delete accountData[Number(key)]
    }
    for (const key of Object.keys(runDetailsMap)) {
      delete runDetailsMap[Number(key)]
    }
  }

  return {
    accounts,
    selectedAccountId,
    selectedAccount,
    activeAccounts,
    hasMultipleAccounts,
    accountsLoaded,
    loadingAccounts,
    globalOverview,
    runDetailsMap,
    accountData,
    account,
    loadAccounts,
    selectAccount,
    refreshAccountOverview,
    refreshSelectedOverview,
    refreshRuntimeOverview,
    loadSchedules,
    saveSchedules,
    loadRuns,
    loadRunDetail,
    clearAccountCache,
    evictRunDetail,
    refreshGlobalOverview,
    reset,
  }
})