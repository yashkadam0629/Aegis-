export interface Dataset {
  dataset_id: string
  name: string
  kind: string
  dataset_version: string
  n_accounts: number
  n_transactions: number
  min_ts: string | null
  max_ts: string | null
  has_ground_truth: boolean
  provenance: string
  created_at: string
}

export interface Txn {
  transaction_id: string
  source_account_id: string
  destination_account_id: string
  transaction_timestamp: string
  amount: number
  currency?: string
  transaction_type: string
  channel?: string | null
  model_risk?: number | null
  anomaly?: number | null
}

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface AlertRow {
  alert_id: string
  entity_id: string
  entity_type: string
  alert_type: string
  risk_score: number
  risk_band: string
  n_transactions: number
  n_accounts: number
  gross_value: number
  exposed_value: number
  alert_timestamp: string
  investigation_status: string
  assigned_analyst?: string | null
  evidence_summary: string
  methods: string[]
  model_version: string
  feature_version: string
}

export interface AlertDetail extends AlertRow {
  detail: {
    evidence_summary: string
    detection_methods: string[]
    detections: { method: string; strength: number; summary: string; evidence: Record<string, unknown>; path: string[]; start: string; end: string }[]
    paths: string[][]
    accounts: string[]
    evidence_timestamps: { first: string; last: string }
    risk_breakdown: { score: number; signals: Record<string, number>; contributions: Record<string, number>; missing_families: string[]; method: string }
    alternative_scores: { rules_only: number | null; model_only: number | null }
    exposure: { gross_value: number; exposed_value: number; incremental_unique_value: number; method: string; definition: string }
    explanation: { method: string; factors: { feature: string; contribution: number; value: number | null; direction: string }[] } | null
    explained_transaction: string | null
    recommended_steps: string[]
    limitations: string[]
  }
  transactions: Txn[]
  investigations: { investigation_id: string; status: string; priority: string; assigned_analyst: string | null }[]
  disclaimer: string
}

export interface GraphNode {
  id: string
  hop: number
  account_type: string
  max_risk: number
  community: number | null
  alerts: { alert_id: string; alert_type: string; risk_band: string; risk_score: number }[]
}
export interface GraphEdge {
  source: string
  target: string
  count: number
  total: number
  txn_ids: string[]
  first: string
  last: string
  max: number
}
export interface Subgraph {
  center: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  truncated: boolean
  alerts_in_view: { alert_id: string; alert_type: string; risk_band: string; risk_score: number }[]
  query_ms: number
  note: string
}

export interface Investigation {
  investigation_id: string
  dataset_id: string
  title: string
  assigned_analyst: string | null
  status: string
  priority: string
  created_at: string
  updated_at: string
  closed_at: string | null
  disposition: string | null
  findings: string | null
  age_days: number
  alert_ids: string[]
  notes?: { note_id: number; author: string; created_at: string; body: string }[]
  transaction_ids?: string[]
  history?: { audit_id: number; actor: string; at: string; action: string; field: string | null; old: string | null; new: string | null }[]
  allowed_transitions?: string[]
  disposition_notice: string
  created_by: string
}

export interface Overview {
  dataset: { dataset_id: string; name: string; kind: string; min_ts: string; max_ts: string; dataset_version: string }
  kpis: Record<string, number | null>
  definitions: Record<string, string>
  alerts_by_band: Record<string, number>
  alerts_by_type: { alert_type: string; count: number; exposed_value: number }[]
  model: { model_run_id: string; version: string; feature_version: string } | null
  model_performance: { model: string; pr_auc_test: number; recall_at_1pct: number; evaluation: string; rules_baseline_pr_auc: number } | null
  freshness: { last_pipeline_run: string | null; window_end: string | null; data_max_ts: string | null }
  data_quality: { rows_received: number | null; rows_accepted: number | null; rows_rejected: number | null; duplicates: Record<string, number> | null; rejected_by_reason: Record<string, number> | null; report_id: string | null }
}

export interface Job {
  job_id: string
  kind: string
  status: string
  log: string[]
  result: unknown
  error: string | null
}
