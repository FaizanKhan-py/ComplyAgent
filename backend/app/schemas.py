"""Request and response models for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class CustomerDetail(CustomerSummary):
    on_time_payment_rate: float | None
    avg_payment_delay: float | None
    avg_credit_utilization: float | None
    history: list[MonthlyRecord]


class MonthInput(BaseModel):
    repayment_status: int = Field(
        ge=-2, le=9, description="-2 no spending, -1 paid in full, 0 minimum paid, n months late"
    )
    bill_amt: int = Field(ge=-100_000_000, le=100_000_000)
    pay_amt: int = Field(ge=0, le=100_000_000)


class CustomerCreate(BaseModel):
    """One customer entered by hand, in the same shape as a row of the UCI dataset."""

    customer_id: int | None = Field(default=None, ge=1, le=2_000_000_000)
    limit_bal: int = Field(gt=0, le=100_000_000)
    sex: Literal["Male", "Female"]
    education_code: int = Field(ge=1, le=4)
    marriage: Literal["Married", "Single", "Other"]
    age: int = Field(ge=18, le=120)
    default_next_month: bool
    history: list[MonthInput] = Field(
        min_length=6, max_length=6, description="Six months, most recent first."
    )


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
