export type MarketKey = 'sh_main' | 'sz_main' | 'chinext' | 'star' | 'bse'

export interface AppSettings {
  id: number
  app_display_name: string
  provider_name: string
  mx_api_key: string | null
  llm_base_url: string | null
  llm_api_key: string | null
  llm_model: string
  llm_reasoning_effort: string | null
  llm_max_retries: number
  automation_context_window_tokens: number | null
  llm_enable_reasoning_content_echo: boolean
  tg_bot_token: string | null
  tg_chat_id: string | null
  tg_notify_trade_enabled: boolean
  capital_seal_enabled: boolean
  capital_seal_amount: number
  allowed_markets: MarketKey[]
  system_prompt: string
  created_at: string
  updated_at: string
}

export interface ScheduleConfig {
  id: number
  name: string
  run_type: 'analysis' | 'trade'
  cron_expression: string
  task_prompt: string
  timeout_seconds: number
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  created_at: string
  updated_at: string
}

export interface RunSummary {
  id: number
  trigger_source: string
  run_type: string
  schedule_name: string | null
  status: string
  analysis_summary: string | null
  error_message: string | null
  api_call_count: number
  executed_trade_count: number
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  started_at: string
  finished_at: string | null
}

export interface RunDetail extends RunSummary {
  final_answer: string | null
  output_markdown: string | null
  api_details: ApiDetail[]
  raw_tool_previews: RawToolPreview[]
  trade_details: TradeDetail[]
  decision_payload: Record<string, unknown> | null
  executed_actions: Array<Record<string, unknown>> | null
  llm_request_payload: Record<string, unknown> | null
  llm_response_payload: Record<string, unknown> | null
  skill_payloads: Record<string, unknown> | null
  trade_orders: TradeOrder[]
}

export interface ApiDetail {
  tool_name: string
  name: string
  summary: string
  preview_index: number | null
  tool_call_id?: string | null
  status?: 'running' | 'done' | 'failed' | null
  ok?: boolean | null
  stream_key?: string | null
}

export interface RawToolPreview {
  preview_index: number
  tool_name: string
  display_name: string
  summary: string
  preview: string
  truncated: boolean
}

export interface RawToolPreviewDetail extends RawToolPreview {
  full_preview: string
}

export interface TradeDetail {
  action: 'buy' | 'sell'
  action_text: string
  symbol: string
  name: string
  volume: number
  price: number | null
  amount: number | null
  summary: string
  tool_name: string | null
  preview_index: number | null
  status?: 'running' | 'done' | 'failed' | null
  ok?: boolean | null
  stream_key?: string | null
}

export interface RunSummaryPage {
  items: RunSummary[]
  next_before_id: number | null
  has_more: boolean
}

export interface RuntimeSummarySection {
  analysis_count: number
  api_calls: number
  trades: number
  success_rate: number
  input_tokens: string
  output_tokens: string
  total_tokens: string
}

export interface RuntimeLastRun {
  start_time: string
  end_time: string
  status: string
  status_text: string
  duration: string
  input_tokens: string
  output_tokens: string
  total_tokens: string
}

export interface RuntimeOverview {
  last_run: RuntimeLastRun
  today: RuntimeSummarySection
  recent_3_days: RuntimeSummarySection
  recent_7_days: RuntimeSummarySection
}

export interface TradeOrder {
  id: number
  symbol: string
  action: string
  quantity: number
  price_type: string
  price: number | null
  status: string
  response_payload: Record<string, unknown> | null
  created_at: string
}

export interface PositionOverview {
  name: string
  symbol: string
  amount: number
  volume: number | null
  available_volume: number | null
  day_profit: number | null
  day_profit_ratio: number | null
  profit: number | null
  profit_ratio: number | null
  profit_text: string
  current_price: number | null
  cost_price: number | null
  position_ratio: number | null
}

export interface OrderOverview {
  order_id: string
  order_time: string | null
  name: string
  symbol: string
  side: string
  side_text: string
  status: string
  status_text: string
  order_price: number | null
  order_quantity: number | null
  filled_price: number | null
  filled_quantity: number | null
}

export interface TradeSummary {
  name: string
  symbol: string
  volume: number
  buy_amount: number
  sell_amount: number
  buy_price: number | null
  sell_price: number | null
  profit: number
  profit_ratio: number | null
  opened_at: string | null
  closed_at: string | null
}

export interface AccountOverview {
  open_date: string | null
  daily_profit_trade_date: string | null
  operating_days: number | null
  initial_capital: number | null
  total_assets: number | null
  total_market_value: number | null
  cash_balance: number | null
  total_position_ratio: number | null
  holding_profit: number | null
  total_return_ratio: number | null
  nav: number | null
  daily_profit: number | null
  daily_return_ratio: number | null
  positions: PositionOverview[]
  orders: OrderOverview[]
  trade_summaries: TradeSummary[]
  errors: string[]
}

export interface ChatToolCall {
  tool_call_id?: string | null
  tool_name: string
  status: 'running' | 'done'
  ok?: boolean
  summary?: string
  arguments?: unknown
  started_at: number
  finished_at?: number
}

export interface ChatAttachment {
  id: number
  filename: string
  mime_type: string
  size: number
  url: string
}

export interface ChatMessage {
  id?: number
  role: 'user' | 'assistant' | 'system'
  content: string
  tool_calls?: ChatToolCall[]
  attachments?: ChatAttachment[]
  created_at?: string
  pending?: boolean
}

export interface ChatRequest {
  messages: ChatMessage[]
}

export interface ChatResponse {
  message: ChatMessage
  context: Record<string, boolean>
}

export interface ChatSession {
  id: number
  title: string
  kind?: string
  slug?: string | null
  created_at: string
  updated_at: string
  last_message_at: string | null
  message_count: number
}

export interface ChatSessionMessagesPayload {
  session: ChatSession
  messages: ChatMessage[]
  next_before_id: number | null
  has_more: boolean
}

export interface PersistentSession {
  id: number
  title: string
  kind: string
  slug: string | null
  created_at: string
  updated_at: string
  last_message_at: string | null
  message_count: number
  archived_summary: string | null
  summary_revision: number
  last_compacted_message_id: number | null
  last_compacted_run_id: number | null
}

export interface PersistentSessionMessagesPayload {
  session: PersistentSession
  messages: ChatMessage[]
  next_before_id: number | null
  has_more: boolean
}

export interface ChatStreamRequest {
  session_id: number
  content: string
  attachment_ids?: number[]
}

export interface LoginRequest {
  password: string
}

export interface LoginResponse {
  authenticated: boolean
  token: string | null
}

export type SkillCompatibilityLevel = 'native' | 'prompt_only' | 'needs_attention'
export type SkillRole = 'runtime' | 'standard'

export interface SkillListItem {
  id: string
  name: string
  description: string
  source: 'builtin' | 'workspace'
  role: SkillRole
  enabled: boolean
  can_disable: boolean
  can_delete: boolean
  always_enabled: boolean
}

export interface SkillInfo {
  id: string
  name: string
  description: string
  location: string
  source: 'builtin' | 'workspace'
  role: SkillRole
  enabled: boolean
  can_disable: boolean
  can_delete: boolean
  always_enabled: boolean
  has_handler: boolean
  tool_names: string[]
  run_types: string[]
  category: string | null
  compatibility_level: SkillCompatibilityLevel
  compatibility_summary: string
  issues: string[]
  support_files: string[]
  clawhub_slug: string | null
  clawhub_version: string | null
  clawhub_url: string | null
  published_at: string | null
}

// ── UZI 深度报告（文档 §15.5 / §9.1 / §10）────────────────────────

export type UziReportStatus =
  | 'queued'
  | 'stage1_running'
  | 'llm_review'
  | 'stage2_running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export const UZI_TERMINAL_STATUSES: readonly UziReportStatus[] = [
  'completed',
  'failed',
  'cancelled',
]

export interface UziStatus {
  enabled: boolean
  worker_available: boolean
  worker_version: string | null
  active_jobs: number
  queued_jobs: number
  max_queued: number
  reason: string | null
}

export interface UziSourceStatus {
  repository: string
  current_commit: string
  current_version: string
  latest_commit: string | null
  latest_version: string | null
  update_available: boolean
  updated: boolean
  checked_at: string
  can_update: boolean
  active_jobs: number
  reason: string | null
  error: string | null
  message: string | null
}

/** 标准化摘要（文档 §9.1）：字段允许为空但不允许随意改名。 */
export interface UziValuationSummary {
  rating: string
  target_price: number
  upside_pct: number
  methods: string[]
}

export interface UziPanelSummary {
  bullish: number
  neutral: number
  bearish: number
  key_disagreements: string[]
}

export interface UziDataGapsSummary {
  coverage_pct: number
  unresolved: number
  items: string[]
}

export interface UziSummaryPayload {
  schema_version: number
  ticker: string
  company_name: string
  overall_score: number
  verdict: string
  one_liner: string
  valuation: UziValuationSummary
  risks: string[]
  catalysts: string[]
  panel: UziPanelSummary
  qualitative: Record<string, unknown>
  data_gaps: UziDataGapsSummary
  sources: string[]
  data_as_of: string
  generated_at: string
  disclaimer: string
}

export interface UziReportSummary {
  id: number
  ticker_input: string
  ticker_normalized: string | null
  company_name: string | null
  status: UziReportStatus
  progress: number
  overall_score: number | null
  verdict: string | null
  llm_model: string | null
  created_at: string
  finished_at: string | null
  data_as_of: string | null
}

export type UziArtifactKey =
  | 'html'
  | 'share_card'
  | 'war_report'
  | 'meta'
  | 'one_liner'
  | 'synthesis'

export interface UziReportArtifact {
  key: UziArtifactKey
  file: string
  size: number
  mime: string
}

export interface UziReportDetail extends UziReportSummary {
  phase: string | null
  progress_message: string | null
  error_code: string | null
  error_message: string | null
  uzi_commit: string | null
  llm_reasoning_effort: string | null
  summary: UziSummaryPayload | null
  artifacts: UziReportArtifact[]
}

export interface UziReportListResponse {
  items: UziReportSummary[]
  total: number
  limit: number
  offset: number
}

export type UziReportEventType =
  | 'snapshot'
  | 'status_changed'
  | 'progress'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'heartbeat'

export interface UziReportEvent {
  type: UziReportEventType
  report_id: number
  ts?: number
  from?: string
  to?: string
  status?: string
  phase?: string | null
  progress?: number
  message?: string | null
  job?: UziReportDetail | null
}

export interface CreateUziReportRequest {
  ticker: string
}

export interface CreateUziReportResponse {
  report: UziReportSummary
  reused: boolean
}
