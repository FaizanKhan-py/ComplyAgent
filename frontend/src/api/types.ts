// Mirrors backend/app/schemas.py. Keep the two in sync.

export type DocumentStatus = 'pending' | 'processing' | 'indexed' | 'failed'
export type ComponentStatus = 'ok' | 'degraded' | 'error'
export type KnowledgeBaseStatus = 'empty' | 'processing' | 'ready' | 'degraded' | 'unavailable'

export interface PolicyDocument {
  id: string
  filename: string
  file_type: string
  size_bytes: number
  status: DocumentStatus
  error: string | null
  page_count: number | null
  char_count: number | null
  chunk_count: number
  embedding_model: string | null
  uploaded_at: string
  updated_at: string
  indexed_at: string | null
}

export interface ActivityEvent {
  id: number
  document_id: string
  filename: string
  event: 'uploaded' | 'indexed' | 'failed' | 'reindex' | 'deleted'
  detail: string | null
  created_at: string
}

export interface KnowledgeBaseStats {
  status: KnowledgeBaseStatus
  total_documents: number
  by_status: Record<DocumentStatus, number>
  by_file_type: Record<string, number>
  total_size_bytes: number
  indexed_characters: number
  total_chunks: number
  searchable_chunks: number
  stale_documents: number
  last_indexed_at: string | null
  embedding_model: string
  chunk_size: number
  chunk_overlap: number
}

export interface HealthComponent {
  status: ComponentStatus
  detail: string
}

export interface Health {
  status: ComponentStatus
  components: Record<string, HealthComponent>
}

export interface AdminConfig {
  supported_extensions: string[]
  max_upload_mb: number
  chunk_size: number
  chunk_overlap: number
  embedding_model: string
  llm_provider: string | null
  llm_model: string | null
  default_top_k: number
  default_min_score: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface QueryRequest {
  question: string
  history?: ChatMessage[]
  top_k?: number
  min_score?: number
  document_ids?: string[]
}

export interface Source {
  rank: number
  document_id: string
  filename: string
  file_type: string
  page_number: number | null
  chunk_index: number
  score: number
  text: string
  cited: boolean
}

export type AnswerMode = 'generated' | 'retrieval_only' | 'no_evidence'

export interface QueryResponse {
  answer: string
  answer_mode: AnswerMode
  insufficient_evidence: boolean
  sources: Source[]
  model: string | null
  warning: string | null
  retrieval: {
    top_k: number
    min_score: number
    embedding_model: string
    chunks_searched: number
  }
}

export interface DeleteResult {
  id: string
  deleted: boolean
  chunks_removed: number
}

export type Role = 'user' | 'admin'

export interface User {
  id: number
  email: string
  full_name: string
  role: Role
  created_at: string
  last_login_at: string | null
}

export interface SessionInfo {
  user: User
  /** Sent back in the X-CSRF-Token header on every state-changing request. */
  csrf_token: string
}

export interface AuthStatus {
  auth_required: boolean
  registration_open: boolean
}

export interface RegisterRequest {
  full_name: string
  email: string
  password: string
}

export type RiskLevel = 'low' | 'medium' | 'high'

export interface ModelScores {
  customers: number
  accuracy: number
  precision: number
  recall: number
  f1: number
  roc_auc: number
  tn: number
  fp: number
  fn: number
  tp: number
}

export interface RiskModel {
  model_name: string
  trained_at: string
  features: string[]
  metrics: { threshold: number; validation: ModelScores; test: ModelScores }
}

export interface PortfolioSummary {
  loaded: boolean
  imported_at: string | null
  customers: number
  total_credit_limit: number
  latest_billed: number
  latest_late_rate: number | null
  actual_defaults: number
  model: RiskModel | null
  by_risk: { risk_level: RiskLevel; customers: number; default_rate: number | null }[]
  by_month: { month: string; customers: number; late_rate: number; billed: number; paid: number }[]
  by_education: { education: string; customers: number; default_rate: number }[]
}

export interface CustomerSummary {
  customer_id: number
  limit_bal: number
  age: number
  sex: string
  education: string
  marriage: string
  latest_status: number | null
  latest_bill: number | null
  default_probability: number | null
  predicted_default: boolean | null
  risk_level: RiskLevel | null
  actual_default: boolean
  data_split: 'train' | 'validation' | 'test' | null
}

export interface CustomerList {
  customers: CustomerSummary[]
  total: number
  page: number
  page_size: number
}

export type CustomerSort = 'id' | 'probability' | 'limit_bal' | 'latest_status'

export interface CustomerQuery {
  search?: string
  risk?: RiskLevel
  actual?: 'defaulted' | 'paid'
  sort?: CustomerSort
  order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}

export interface MonthlyRecord {
  month_no: number
  month: string
  repayment_status: number
  bill_amt: number
  pay_amt: number
}

export interface CustomerDetail extends CustomerSummary {
  on_time_payment_rate: number | null
  avg_payment_delay: number | null
  avg_credit_utilization: number | null
  history: MonthlyRecord[]
}

export interface GuidanceSection extends QueryResponse {
  key: 'actions' | 'escalation' | 'hardship' | 'contact'
  title: string
  question: string
}

export interface CustomerGuidance {
  customer_id: number
  months_late: number
  days_overdue_estimate: number
  latest_bill: number | null
  default_probability: number | null
  risk_level: RiskLevel | null
  sections: GuidanceSection[]
}

export interface PredictionModelInfo {
  available: boolean
  message: string | null
  model_name: string | null
  features: string[]
  threshold: number | null
  test_metrics: Record<string, number> | null
}

export interface DatasetScores {
  accuracy: number
  precision: number
  recall: number
  f1: number
  tn: number
  fp: number
  fn: number
  tp: number
}

export interface RowPrediction {
  row: number
  id: number | null
  limit_bal: number
  age: number
  default_probability: number
  predicted_default: 0 | 1
  risk_level: RiskLevel
  actual_default: 0 | 1 | null
}

export interface DatasetPrediction {
  filename: string
  model_name: string
  threshold: number
  rows: number
  predicted_defaults: number
  by_risk: Record<RiskLevel, number>
  scores: DatasetScores | null
  predictions: RowPrediction[]
  /** The uploaded file with the target column filled in, ready to download. */
  csv: string
}
