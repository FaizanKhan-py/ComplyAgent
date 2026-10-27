"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..customers.portfolio import clean_email
from ..decisions.case_analyst import CaseAnalysis
from ..decisions.decision import Recommendation
from ..decisions.red_team import RedTeamFinding

DocumentStatus = Literal["pending", "processing", "indexed", "failed"]
ComponentStatus = Literal["ok", "degraded", "error"]
RiskLevel = Literal["low", "medium", "high"]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class Document(BaseModel):
    id: str
    filename: str
    file_type: str
    size_bytes: int
    status: DocumentStatus
    error: str | None = None
    page_count: int | None = None
    char_count: int | None = None
    chunk_count: int
    embedding_model: str | None = None
    uploaded_at: str
    updated_at: str
    indexed_at: str | None = None


class DocumentList(BaseModel):
    documents: list[Document]


class DeleteResult(BaseModel):
    id: str
    deleted: bool
    chunks_removed: int


class RebuildResult(BaseModel):
    queued: int


class ActivityEvent(BaseModel):
    id: int
    document_id: str
    filename: str
    event: Literal["uploaded", "indexed", "failed", "reindex", "deleted"]
    detail: str | None = None
    created_at: str


class ActivityList(BaseModel):
    events: list[ActivityEvent]


class KnowledgeBaseStats(BaseModel):
    status: Literal["empty", "processing", "ready", "degraded", "unavailable"]
    total_documents: int
    by_status: dict[str, int]
    by_file_type: dict[str, int]
    total_size_bytes: int
    indexed_characters: int
    total_chunks: int
    searchable_chunks: int
    stale_documents: int
    last_indexed_at: str | None
    embedding_model: str
    chunk_size: int
    chunk_overlap: int


class Component(BaseModel):
    status: ComponentStatus
    detail: str


class Health(BaseModel):
    status: ComponentStatus
    components: dict[str, Component]


class PublicHealth(BaseModel):
    status: ComponentStatus


class AuthStatus(BaseModel):
    auth_required: bool
    registration_open: bool


class User(BaseModel):
    id: int
    email: str
    full_name: str
    role: Literal["user", "admin"]
    supervisor: bool = Field(
        default=False, description="A team lead: gives the second approval payment plans need."
    )
    created_at: str
    last_login_at: str | None = None


class SessionInfo(BaseModel):
    user: User
    csrf_token: str  # sent back in the X-CSRF-Token header on every state-changing request


class RegisterRequest(BaseModel):
    # Lengths here only bound the request; the real rules give friendlier messages.
    full_name: str = Field(max_length=500)
    email: str = Field(max_length=500)
    password: str = Field(max_length=1024)


class LoginRequest(BaseModel):
    email: str = Field(max_length=500)
    password: str = Field(max_length=1024)


class Ok(BaseModel):
    ok: bool


class AdminConfig(BaseModel):
    supported_extensions: list[str]
    max_upload_mb: int
    chunk_size: int
    chunk_overlap: int
    embedding_model: str
    llm_provider: str | None
    llm_model: str | None
    default_top_k: int
    default_min_score: float
    email_provider: Literal["none", "graph"] = "none"
    email_redirect_to: str | None = None
    contact_rules: str = Field(default="", description="The contact rules sends must follow.")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    top_k: int | None = Field(default=None, ge=1, le=20)
    min_score: float | None = Field(default=None, ge=0, le=1)
    document_ids: list[str] | None = Field(default=None, max_length=100)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class Source(BaseModel):
    rank: int
    document_id: str
    filename: str
    file_type: str
    page_number: int | None
    chunk_index: int
    score: float
    text: str
    cited: bool


class RetrievalInfo(BaseModel):
    top_k: int
    min_score: float
    embedding_model: str
    chunks_searched: int


class QueryResponse(BaseModel):
    answer: str
    answer_mode: Literal["generated", "retrieval_only", "no_evidence"]
    insufficient_evidence: bool
    sources: list[Source]
    model: str | None
    warning: str | None
    retrieval: RetrievalInfo


class ModelScores(BaseModel):
    customers: int
    accuracy: float
    precision: float
    recall: float
    f1: float
    roc_auc: float
    tn: int
    fp: int
    fn: int
    tp: int


class ModelMetrics(BaseModel):
    threshold: float
    validation: ModelScores
    test: ModelScores


class RiskModel(BaseModel):
    model_name: str
    trained_at: str
    features: list[str]
    metrics: ModelMetrics


class RiskGroup(BaseModel):
    risk_level: RiskLevel
    customers: int
    default_rate: float | None


class MonthGroup(BaseModel):
    month: str
    customers: int
    late_rate: float
    billed: int
    paid: int


class EducationGroup(BaseModel):
    education: str
    customers: int
    default_rate: float


class PortfolioSummary(BaseModel):
    loaded: bool
    imported_at: str | None
    customers: int
    total_credit_limit: int
    latest_billed: int
    latest_late_rate: float | None
    actual_defaults: int
    model: RiskModel | None
    by_risk: list[RiskGroup]
    by_month: list[MonthGroup]
    by_education: list[EducationGroup]


class CustomerSummary(BaseModel):
    customer_id: int
    email: str | None = Field(description="Where collections emails go. Never sent to an LLM.")
    limit_bal: int
    age: int
    sex: str
    education: str
    marriage: str
    latest_status: int | None
    latest_bill: int | None
    default_probability: float | None
    predicted_default: bool | None
    risk_level: RiskLevel | None
    actual_default: bool
    data_split: Literal["train", "validation", "test"] | None


class CustomerList(BaseModel):
    customers: list[CustomerSummary]
    total: int
    page: int
    page_size: int


class MonthlyRecord(BaseModel):
    month_no: int
    month: str
    repayment_status: int
    bill_amt: int
    pay_amt: int


class DoNotContact(BaseModel):
    reason: str
    set_by: str = Field(description="An officer, or the Communication Agent.")
    set_at: str


class CustomerDetail(CustomerSummary):
    on_time_payment_rate: float | None
    avg_payment_delay: float | None
    avg_credit_utilization: float | None
    history: list[MonthlyRecord]
    do_not_contact: DoNotContact | None = Field(
        default=None, description="The customer asked not to be contacted: no email is written."
    )


class MonthInput(BaseModel):
    repayment_status: int = Field(
        ge=-2, le=9, description="-2 no spending, -1 paid in full, 0 minimum paid, n months late"
    )
    bill_amt: int = Field(ge=-100_000_000, le=100_000_000)
    pay_amt: int = Field(ge=0, le=100_000_000)


class CustomerCreate(BaseModel):
    """One customer entered by hand, in the same shape as a row of the UCI dataset."""

    customer_id: int | None = Field(default=None, ge=1, le=2_000_000_000)
    email: str | None = Field(default=None, max_length=254)
    limit_bal: int = Field(gt=0, le=100_000_000)
    sex: Literal["Male", "Female"]
    education_code: int = Field(ge=1, le=4)
    marriage: Literal["Married", "Single", "Other"]
    age: int = Field(ge=18, le=120)
    default_next_month: bool
    history: list[MonthInput] = Field(
        min_length=6, max_length=6, description="Six months, most recent first."
    )

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        return clean_email(value)


class CustomerCreated(BaseModel):
    customer: CustomerDetail
    scored: bool


class GuidanceSection(QueryResponse):
    key: Literal["actions", "escalation", "hardship", "contact"]
    title: str
    question: str


class CustomerGuidance(BaseModel):
    customer_id: int
    months_late: int
    days_overdue_estimate: int
    latest_bill: int | None
    default_probability: float | None
    risk_level: RiskLevel | None
    sections: list[GuidanceSection]


class CaseFact(BaseModel):
    id: str
    text: str


class PolicyPassage(BaseModel):
    number: int
    document_id: str
    filename: str
    page_number: int | None
    score: float
    text: str


class CustomerRecommendation(BaseModel):
    customer_id: int
    decided_by: Literal["rule", "model"]
    model: str | None
    recommendation: Recommendation
    facts: list[CaseFact]
    sources: list[PolicyPassage]
    corrected: list[str] = Field(
        description="Rule breaks in the model's first reply that it fixed when asked again."
    )


class PredictionModelInfo(BaseModel):
    available: bool
    message: str | None
    model_name: str | None = None
    features: list[str] = []
    threshold: float | None = None
    test_metrics: dict[str, float] | None = None


class DatasetScores(BaseModel):
    accuracy: float
    precision: float
    recall: float
    f1: float
    tn: int
    fp: int
    fn: int
    tp: int


class RowPrediction(BaseModel):
    row: int
    id: int | None
    limit_bal: int
    age: int
    default_probability: float
    predicted_default: int
    risk_level: RiskLevel
    actual_default: int | None


class DatasetPrediction(BaseModel):
    filename: str
    model_name: str
    threshold: float
    rows: int
    predicted_defaults: int
    by_risk: dict[RiskLevel, int]
    scores: DatasetScores | None
    predictions: list[RowPrediction]
    csv: str


class AssistantRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(
        default=None,
        max_length=64,
        description="Continue a conversation. Omit it to start a new one.",
    )

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class AssistantStep(BaseModel):
    tool: str
    input: str = Field(description="The tool's arguments, as JSON.")


class AssistantReply(BaseModel):
    answer: str
    answer_mode: Literal["generated", "retrieval_only", "no_evidence"]
    insufficient_evidence: bool
    sources: list[Source] = Field(description="The policy passages read, numbered as cited.")
    model: str | None
    warning: str | None
    conversation_id: str
    steps: list[AssistantStep] = Field(description="The tools the assistant called, in order.")


# --- collections conversations: the agents, the email thread and the officer's approval ----


class CaseFile(BaseModel):
    analysed_by: Literal["rule", "model"]
    model: str | None
    facts: list[CaseFact]
    analysis: CaseAnalysis


class ReviewRound(BaseModel):
    round: int
    checked_by: Literal["guardrails", "red_team"]
    passed: bool
    issues: list[str] = Field(description="Guardrails the draft broke.")
    findings: list[RedTeamFinding] = Field(description="What the Red Team found.")
    dropped: int = Field(
        description="Red Team findings dropped: they quoted words not in the email."
    )
    decision_revised: dict[str, str] | None = Field(
        description="The action before and after the Red Team challenged it."
    )


class DraftReview(BaseModel):
    rounds: list[ReviewRound]
    checks: list[str] = Field(description="Guardrails the final draft passed.")
    sources: list[PolicyPassage]
    decision: Recommendation


ConversationStatus = Literal["open", "escalated", "closed"]
MessageStatus = Literal[
    "pending_approval",
    "awaiting_supervisor",
    "blocked",
    "approved",
    "rejected",
    "superseded",
    "sent",
    "received",
]


class ConversationMessage(BaseModel):
    id: int
    direction: Literal["outbound", "inbound"]
    status: MessageStatus
    subject: str | None
    body: str
    intent: str | None = Field(description="Inbound: what the customer's email is about.")
    intent_summary: str | None
    review: DraftReview | None = Field(description="Outbound: how the draft was checked.")
    edited: bool = Field(description="An officer changed the agent's text before approving it.")
    reviewed_by: str | None
    reviewed_at: str | None
    review_note: str | None
    sent_by: str | None
    sent_at: str | None
    created_at: str
    needs_supervisor: bool = Field(
        default=False, description="Four eyes: a team lead must give the second approval."
    )
    first_approved_by: str | None = None
    first_approved_at: str | None = None
    delivered: bool = Field(default=False, description="Really emailed (Microsoft Graph).")


class Conversation(BaseModel):
    id: int
    customer_id: int
    status: ConversationStatus
    status_reason: str | None
    case_file: CaseFile
    decision: Recommendation
    created_by: str
    created_at: str
    updated_at: str
    messages: list[ConversationMessage]
    follow_ups: list[FollowUp] = Field(description="What the Follow-up Agent planned.")


class InboundEmail(BaseModel):
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body must not be blank")
        return value.strip()


class RedraftRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=1000)


class ApproveRequest(BaseModel):
    subject: str | None = Field(default=None, max_length=300, description="Edited subject.")
    body: str | None = Field(default=None, max_length=5000, description="Edited body.")
    note: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    note: str = Field(min_length=1, max_length=1000, description="Why it was turned down.")


class CloseRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class FollowUp(BaseModel):
    id: int
    conversation_id: int
    customer_id: int
    due_at: str
    action: Literal["send_reminder", "schedule_call", "escalate_to_human"]
    reason: str
    status: Literal["scheduled", "running", "done", "cancelled"]
    outcome: str | None
    created_at: str
    done_at: str | None


class FollowUpError(BaseModel):
    id: int
    error: str


class FollowUpRun(BaseModel):
    due: int
    done: list[int]
    failed: list[FollowUpError]


class AuditEvent(BaseModel):
    id: int
    created_at: str
    actor: str = Field(description="A person's email, or the agent's name.")
    event: str
    customer_id: int | None
    conversation_id: int | None
    message_id: int | None
    summary: str
    detail: dict | None


class AuditList(BaseModel):
    events: list[AuditEvent]
    next_before: int | None = Field(description="Pass as `before` for the next, older page.")


class AgentGraph(BaseModel):
    mermaid: str


class SimulatedOutcome(BaseModel):
    probability: float
    risk_level: RiskLevel
    predicted_default: bool


class Scenario(SimulatedOutcome):
    key: str
    label: str
    kind: Literal["behaviour", "fairness"]
    change: float = Field(description="Probability minus the record's own probability.")


class CustomScenario(SimulatedOutcome):
    change: float
    changes: dict[str, int]
    features: dict[str, float]
    recommendation: Recommendation | None = Field(
        default=None, description="The Decision Agent's choice for the changed record."
    )
    decision_error: str | None = None


class Simulation(BaseModel):
    customer_id: int
    model_name: str | None
    threshold: float
    base: SimulatedOutcome
    personal_inputs: list[str] = Field(
        default=[], description="Personal attributes the model uses (age, sex, education)."
    )
    scenarios: list[Scenario]
    custom: CustomScenario | None


class SimulateRequest(BaseModel):
    repayment_status: int | None = Field(default=None, ge=-2, le=9, description="Latest month.")
    bill_amt: int | None = Field(default=None, ge=-1_000_000, le=10_000_000)
    pay_amt: int | None = Field(default=None, ge=0, le=10_000_000)
    limit_bal: int | None = Field(default=None, gt=0, le=10_000_000)
    decide: bool = Field(default=False, description="Also ask the Decision Agent (uses the LLM).")


class DoNotContactRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500, description="What the customer asked.")


class LiftRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500, description="Why contact may resume.")


class InboxDraft(BaseModel):
    id: int
    conversation_id: int
    customer_id: int
    status: Literal["pending_approval", "awaiting_supervisor", "blocked", "approved"]
    subject: str | None
    body: str
    created_at: str


class InboxEscalation(BaseModel):
    id: int = Field(description="The conversation.")
    customer_id: int
    status_reason: str | None
    updated_at: str


class Inbox(BaseModel):
    drafts: list[InboxDraft] = Field(description="Drafts waiting for approval, blocked or unsent.")
    escalated: list[InboxEscalation] = Field(description="Conversations a person must take.")
    due_follow_ups: list[FollowUp]
    calls: list[FollowUp] = Field(description="Follow-ups that asked an officer to call.")
    total: int


# --- the collections ledger -----------------------------------------------------------------


class Payment(BaseModel):
    id: int
    customer_id: int
    amount: int
    paid_on: str
    recorded_by: str
    note: str | None
    created_at: str


class Promise(BaseModel):
    id: int
    customer_id: int
    conversation_id: int | None
    amount: int | None
    due_on: str
    status: Literal["open", "kept", "broken", "cancelled"]
    recorded_by: str
    created_at: str
    resolved_at: str | None
    outcome: str | None


class Contact(BaseModel):
    id: int
    customer_id: int
    conversation_id: int | None
    channel: Literal["email", "phone", "sms"]
    outcome: Literal["sent", "reached", "no_answer", "voicemail"]
    made_by: str
    note: str | None
    made_at: str


class LedgerView(BaseModel):
    payments: list[Payment]
    promises: list[Promise]
    contacts: list[Contact]
    paid_total: int
    contact_rules: str
    contact_now: list[str] = Field(description="Why a contact attempt is not allowed right now.")


class PaymentRequest(BaseModel):
    amount: int = Field(gt=0, le=10_000_000)
    paid_on: date
    note: str | None = Field(default=None, max_length=500)


class PromiseRequest(BaseModel):
    due_on: date
    amount: int | None = Field(default=None, gt=0, le=10_000_000)


class ContactRequest(BaseModel):
    channel: Literal["phone", "sms"] = "phone"
    outcome: Literal["reached", "no_answer", "voicemail"]
    note: str | None = Field(default=None, max_length=1000)


class MailboxPoll(BaseModel):
    fetched: int
    matched: list[int] = Field(description="Conversations that received a reply.")
    unmatched: int = Field(description="Emails left unread for a person.")
    failed: list[dict]


# --- the collections dashboard --------------------------------------------------------------


class DashboardWork(BaseModel):
    pending_approval: int
    awaiting_supervisor: int
    blocked: int
    approved_unsent: int
    escalated: int
    follow_ups_due: int


class DashboardConversations(BaseModel):
    open: int
    escalated: int
    closed: int
    emails_per_resolution: float | None = Field(
        description="Emails sent per closed conversation, on average."
    )
    recent_escalations: list[InboxEscalation]


class DashboardPromises(BaseModel):
    open: int
    kept: int
    broken: int
    kept_rate: float | None = Field(description="Kept / (kept + broken).")
    collected: int = Field(description="NT$ recorded in the ledger as paid.")


class DashboardCompliance(BaseModel):
    drafts_reviewed: int
    first_round_pass_rate: float | None
    average_rounds: float | None
    sends_refused: int = Field(description="Sends the contact rules refused.")
    do_not_contact: int


class DayCount(BaseModel):
    day: str
    emails: int


class DashboardActivity(BaseModel):
    emails_sent: int
    emails_delivered: int
    follow_ups_scheduled: int
    sent_by_day: list[DayCount]
    intents: dict[str, int]


class CollectionsDashboard(BaseModel):
    generated_at: str
    work: DashboardWork
    conversations: DashboardConversations
    promises: DashboardPromises
    compliance: DashboardCompliance
    activity: DashboardActivity


Conversation.model_rebuild()  # it refers to FollowUp, defined after it
