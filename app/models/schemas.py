"""Pydantic schemas for API request/response validation."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class QuotationStatus(str, Enum):
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    PENDING_SALES_REVIEW = "PENDING_SALES_REVIEW"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExtractionConfidence(str, Enum):
    HIGH = "HIGH"  # All key fields found
    MEDIUM = "MEDIUM"  # Some fields inferred
    LOW = "LOW"  # Many fields missing


class ExtractionResult(BaseModel):
    """Structured result from NLP extraction of customer message."""

    gpu_family: Optional[str] = None
    gpu_model: Optional[str] = None
    gpu_memory_gb: Optional[int] = None
    gpu_count_per_server: Optional[int] = None
    num_servers: Optional[int] = None
    ram_gb: Optional[int] = None
    sku: Optional[str] = None
    missing_fields: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence = ExtractionConfidence.LOW
    # Tracks which fields were explicitly stated vs inferred
    explicitly_stated: list[str] = Field(default_factory=list)
    inferred: list[str] = Field(default_factory=list)


class CatalogMatchResult(BaseModel):
    matched: bool = False
    sku: Optional[str] = None
    description: Optional[str] = None
    unit_selling_price: Optional[float] = None
    lead_time: Optional[str] = None
    stock_available: Optional[int] = None
    price_pending: bool = False
    match_notes: str = ""


class StockCheckResult(BaseModel):
    sku: str
    requested: int
    available: int
    can_fulfill: bool
    shortage: int = 0
    requires_sales_review: bool = False
    message: str = ""


# ---- Request schemas ----


class QuotationRequest(BaseModel):
    request_id: str = Field(
        ..., description="Unique client-generated request ID for idempotency"
    )
    conversation_id: str = Field(..., description="Conversation session ID")
    message: str = Field(
        ..., description="Natural language customer request", min_length=1
    )


class ClarificationMessage(BaseModel):
    message: str = Field(
        ..., description="Customer's clarification reply", min_length=1
    )
    request_id: str = Field(
        ..., description="New request ID for this follow-up submission"
    )


class ApprovalRequest(BaseModel):
    notes: Optional[str] = Field(None, description="Reviewer notes")


# ---- Response schemas ----


class QuotationResponse(BaseModel):
    request_id: str
    conversation_id: str
    status: QuotationStatus
    extraction: ExtractionResult
    catalog_match: Optional[CatalogMatchResult] = None
    clarification_question: Optional[str] = None
    stock_check: Optional[StockCheckResult] = None
    unit_selling_price: Optional[float] = None
    total_price: Optional[float] = None
    price_pending: bool = False
    draft_id: Optional[str] = None
    message: str = ""


class InternalQuotationResponse(QuotationResponse):
    """Extended response with internal cost information — only for internal reviewers."""

    unit_internal_cost: Optional[float] = None
    total_internal_cost: Optional[float] = None
    gross_margin: Optional[float] = None


class ApprovalResponse(BaseModel):
    request_id: str
    status: QuotationStatus
    reviewer_id: str
    approved_at: datetime
    notes: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    llm_provider: str
    environment: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class LoginRequest(BaseModel):
    username: str
    password: str
