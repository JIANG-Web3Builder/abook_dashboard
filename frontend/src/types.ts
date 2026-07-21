export type Tab = 'overview' | 'users' | 'risk' | 'routing'

export interface RequestModel {
  selection: { start: string; end: string }
  validation: { start: string; end: string }
  platforms: string[]
  filters: { groups: string[]; logins: number[] }
  rules: Record<string, number | string[] | boolean>
  personal_candidate_list: boolean
  news_candidate_list: boolean
}

export interface AccountRow {
  platform: string
  login: number
  account_group: string
  book: 'abook' | 'bbook'
  selection_source: string
  selection_client_net_pnl: number
  validation_client_net_pnl: number
  validation_status: string
  selection: Record<string, number | null>
  validation: Record<string, number | null>
  monthly?: Array<Record<string, any>>
  stability: { score: number; tier: string }
  selection_flags: string[]
  bbook_reason_tags?: string[]
  confidence_tier?: string
  risk_balance_prev_month?: number | null
  risk_average_open_degree?: number | null
  risk_peak_leverage_ratio?: number | null
  risk_leverage_p95_ratio?: number | null
  risk_turnover_leverage_p95_ratio?: number | null
  risk_concurrent_leverage_p95_ratio?: number | null
  risk_exposure_status?: string | null
  martingale_status?: string
  martingale_risk_level?: string | null
  martingale_detection_status?: 'none' | 'suspected' | 'confirmed'
  martingale_blocked?: boolean
  martingale_hard_block?: boolean
  confirmed_windows?: number
  confirmed_extreme_windows?: number
  expanded_windows?: number
  martingale_layer_hits?: Record<string, boolean>
  r4_pass?: boolean
  r4_passing_weeks?: number
  r4_record?: Record<string, any>
  july_new_user?: boolean
}

export interface BookAnalyticsPayload {
  user_structure?: {
    daily_book_series?: Array<Record<string, any>>
    [key: string]: any
  }
  pnl_structure?: Record<'abook' | 'bbook', Record<string, any>>
  risk_exposure?: Record<string, any>
  routing_quality?: Record<string, any>
  [key: string]: any
}

export interface SnapshotRefreshResponse {
  status: string
  selection_start: string
  selection_end: string
  snapshots: Record<string, {
    status: string
    path: string
    records: number
    selection_start: string
    selection_end: string
  }>
}

export interface AccountDetailPayload {
  symbols: Array<{
    symbol: string
    trade_count: number
    volume: number
    profit: number
    win_rate: number
    avg_holding_seconds: number
  }>
  metrics?: Record<string, number>
  concentration?: Record<string, number>
  de_extreme?: Record<string, number>
  markout?: Record<string, {
    primary_horizon_ms?: number
    primary_bps?: number | null
    mean_5s_bps?: number | null
    sample_count?: number
    curve: Array<{
      offset_ms: number
      mean_bps: number | null
      sample_count?: number
    }>
  }>
  martingale?: Record<string, any>
}

export interface AnalysisPayload {
  analysis_token?: string
  selection?: { counts: Record<string, number>; groups: Record<string, unknown> }
  validation?: { groups: Record<string, any>; diagnostics: Record<string, any> }
  coverage?: Record<string, any>
  profit_impact?: Record<string, any>
  book_performance?: Record<string, any>
  misjudge?: Record<string, any>
  rules?: Record<string, any>
  funnel?: { stages: Array<{ name: string; count: number; drop_reasons: Record<string, number> }> }
  martingale?: Record<string, any>
  accounts?: AccountRow[]
  population_accounts?: AccountRow[]
  daily_book_series?: any[]
  avg_profit?: Record<string, any>
  [key: string]: any
}
