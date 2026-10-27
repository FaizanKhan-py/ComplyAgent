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
  /** graph: emails really go out through Microsoft 365, and replies come in. */
  email_provider?: 'none' | 'graph'
  /** Outside production, every email goes to this address instead of the customer. */
  email_redirect_to?: string | null
  /** The contact rules every send must follow. */
  contact_rules?: string
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
  /** A team lead: gives the second approval that payment-plan emails need. */
  supervisor?: boolean
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
  /** Where collections emails go; null when none is on record. */
  email: string | null
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
  /** overdue: late in the latest month (Sep 2005); current: not late. */
  payment?: 'overdue' | 'current'
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

export interface DoNotContact {
  reason: string
  /** An officer, or the Communication Agent. */
  set_by: string
  set_at: string
}

export interface CustomerDetail extends CustomerSummary {
  /** The customer asked not to be contacted: no email is written or sent. */
  do_not_contact?: DoNotContact | null
  on_time_payment_rate: number | null
  avg_payment_delay: number | null
  avg_credit_utilization: number | null
  history: MonthlyRecord[]
}

/** One month as the "Add customer" form sends it. */
export interface MonthInput {
  repayment_status: number
  bill_amt: number
  pay_amt: number
}

/** A customer entered by hand, in the shape of a row of the UCI dataset. */
export interface CustomerCreate {
  /** Leave out to use the next free number. */
  customer_id?: number
  email?: string
  limit_bal: number
  sex: 'Male' | 'Female'
  education_code: number
  marriage: 'Married' | 'Single' | 'Other'
  age: number
  default_next_month: boolean
  /** Six months, most recent first. */
  history: MonthInput[]
}

export interface CustomerCreated {
  customer: CustomerDetail
  /** False when no model has been trained yet, so the customer has no prediction. */
  scored: boolean
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

// --- Decision Agent and message drafts -------------------------------------------------------

export type DecisionAction = 'send_reminder' | 'offer_payment_plan' | 'schedule_call' | 'escalate_to_human' | 'no_contact'
export type DecisionChannel = 'sms' | 'email' | 'phone' | 'none'

export interface Recommendation {
  action: DecisionAction
  channel: DecisionChannel
  priority: 'low' | 'medium' | 'high' | 'critical'
  reasons: { text: string; fact_ids: string[]; source_ids: number[] }[]
  needs_human_review: boolean
}

export interface PolicyPassage {
  number: number
  document_id: string
  filename: string
  page_number: number | null
  score: number
  text: string
}

export interface CustomerMessage {
  customer_id: number
  /** Sets the tone of the email; never stated in it. */
  risk_level: RiskLevel | null
  status: 'draft' | 'not_written'
  /** Why no draft was written, when status is not_written. */
  reason: string | null
  decision: Recommendation | null
  subject: string | null
  body: string | null
  sources: PolicyPassage[]
  /** Guardrails the draft passed. */
  checks: string[]
  /** Rule breaks in the first draft, fixed on the second try. */
  corrected: string[]
  model: string | null
}

// --- AI Assistant (LangChain agent) ----------------------------------------------------------

export interface AssistantRequest {
  message: string
  /** Continue a conversation; null starts a new one. */
  conversation_id: string | null
}

/** One reply: shaped like a policy answer (text, [n] sources), plus the tools the agent used. */
export interface AssistantReply {
  answer: string
  answer_mode: AnswerMode
  insufficient_evidence: boolean
  sources: Source[]
  model: string | null
  warning: string | null
  conversation_id: string
  /** The tools the assistant called, in order, with their arguments as JSON. */
  steps: { tool: string; input: string }[]
}

// --- Collections conversations: every agent, the email thread, the officer's approval -------

export interface CaseFinding {
  text: string
  fact_ids: string[]
}

export interface CaseAnalysis {
  summary: string
  situation:
    | 'paid_up'
    | 'early_arrears'
    | 'persistent_arrears'
    | 'worsening'
    | 'improving'
    | 'partial_payer'
    | 'stopped_paying'
  urgency: 'low' | 'medium' | 'high' | 'critical'
  patterns: CaseFinding[]
  care_flags: CaseFinding[]
  open_questions: string[]
}

export interface CaseFile {
  analysed_by: 'rule' | 'model'
  model: string | null
  facts: { id: string; text: string }[]
  analysis: CaseAnalysis
}

export interface RedTeamFinding {
  target: 'message' | 'decision'
  severity: 'blocker' | 'warning'
  issue: string
  quote: string
  source_ids: number[]
  fix: string
}

export interface ReviewRound {
  round: number
  checked_by: 'guardrails' | 'red_team'
  passed: boolean
  issues: string[]
  findings: RedTeamFinding[]
  /** Red Team findings dropped because they quoted words that are not in the email. */
  dropped: number
  decision_revised: { from: string; to: string } | null
}

export interface DraftReview {
  rounds: ReviewRound[]
  checks: string[]
  sources: PolicyPassage[]
  decision: Recommendation
}

export type MessageStatus =
  | 'pending_approval'
  | 'awaiting_supervisor'
  | 'blocked'
  | 'approved'
  | 'rejected'
  | 'superseded'
  | 'sent'
  | 'received'

export interface ConversationMessage {
  id: number
  direction: 'outbound' | 'inbound'
  status: MessageStatus
  subject: string | null
  body: string
  /** Inbound: what the customer's email is about. */
  intent: string | null
  intent_summary: string | null
  review: DraftReview | null
  /** An officer changed the agent's text before approving it. */
  edited: boolean
  reviewed_by: string | null
  reviewed_at: string | null
  review_note: string | null
  sent_by: string | null
  sent_at: string | null
  created_at: string
  /** Four eyes: a team lead must give the second approval. */
  needs_supervisor?: boolean
  first_approved_by?: string | null
  first_approved_at?: string | null
  /** Really emailed through Microsoft 365. */
  delivered?: boolean
}

export interface Conversation {
  id: number
  customer_id: number
  status: 'open' | 'escalated' | 'closed'
  status_reason: string | null
  case_file: CaseFile
  decision: Recommendation
  created_by: string
  created_at: string
  updated_at: string
  messages: ConversationMessage[]
  /** What the Follow-up Agent planned. */
  follow_ups: FollowUp[]
}

export interface FollowUp {
  id: number
  conversation_id: number
  customer_id: number
  due_at: string
  action: 'send_reminder' | 'schedule_call' | 'escalate_to_human'
  reason: string
  status: 'scheduled' | 'done' | 'cancelled'
  outcome: string | null
  created_at: string
  done_at: string | null
}

export interface FollowUpRun {
  due: number
  done: number[]
  failed: { id: number; error: string }[]
}

// --- Audit trail ------------------------------------------------------------------------------

export interface AuditEvent {
  id: number
  created_at: string
  /** A person's email, or the agent's name. */
  actor: string
  event: string
  customer_id: number | null
  conversation_id: number | null
  message_id: number | null
  summary: string
  detail: Record<string, unknown> | null
}

export interface AuditList {
  events: AuditEvent[]
  next_before: number | null
}

export interface AuditQuery {
  customer_id?: number
  event?: string
  before?: number
  limit?: number
}

// --- Counterfactual Simulator -----------------------------------------------------------------

export interface SimulatedOutcome {
  probability: number
  risk_level: RiskLevel
  predicted_default: boolean
}

export interface Scenario extends SimulatedOutcome {
  key: string
  label: string
  kind: 'behaviour' | 'fairness'
  /** Probability minus the record's own probability. */
  change: number
}

export interface CustomScenario extends SimulatedOutcome {
  change: number
  changes: Record<string, number>
  features: Record<string, number>
  recommendation: Recommendation | null
  decision_error: string | null
}

export interface Simulation {
  customer_id: number
  model_name: string | null
  threshold: number
  base: SimulatedOutcome
  /** Personal attributes the model uses (age, sex, education). */
  personal_inputs?: string[]
  scenarios: Scenario[]
  custom: CustomScenario | null
}

export interface SimulateRequest {
  repayment_status?: number
  bill_amt?: number
  pay_amt?: number
  limit_bal?: number
  decide?: boolean
}

// --- Approvals inbox ----------------------------------------------------------------------------

export interface InboxDraft {
  id: number
  conversation_id: number
  customer_id: number
  status: 'pending_approval' | 'awaiting_supervisor' | 'blocked' | 'approved'
  subject: string | null
  body: string
  created_at: string
}

export interface Inbox {
  drafts: InboxDraft[]
  escalated: { id: number; customer_id: number; status_reason: string | null; updated_at: string }[]
  due_follow_ups: FollowUp[]
  /** Follow-ups that asked an officer to call. */
  calls: FollowUp[]
  total: number
}

// --- Collections ledger -----------------------------------------------------------------------

export interface Payment {
  id: number
  customer_id: number
  amount: number
  paid_on: string
  recorded_by: string
  note: string | null
  created_at: string
}

export interface PromiseToPay {
  id: number
  customer_id: number
  conversation_id: number | null
  /** null: any payment keeps it. */
  amount: number | null
  due_on: string
  status: 'open' | 'kept' | 'broken' | 'cancelled'
  recorded_by: string
  created_at: string
  resolved_at: string | null
  outcome: string | null
}

export interface ContactAttempt {
  id: number
  customer_id: number
  conversation_id: number | null
  channel: 'email' | 'phone' | 'sms'
  outcome: 'sent' | 'reached' | 'no_answer' | 'voicemail'
  made_by: string
  note: string | null
  made_at: string
}

export interface Ledger {
  payments: Payment[]
  promises: PromiseToPay[]
  contacts: ContactAttempt[]
  paid_total: number
  contact_rules: string
  /** Why a contact attempt is not allowed right now; empty when it is. */
  contact_now: string[]
}

export interface MailboxPoll {
  fetched: number
  matched: number[]
  unmatched: number
  failed: { conversation_id: number; error: string }[]
}
