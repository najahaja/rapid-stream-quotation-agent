"""Pydantic schemas for API request/response validation."""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Sensible upper limits — well above any realistic single procurement request.
# These guard against LLM hallucination of absurdly large numbers that would
# produce meaningless price/stock calculations.
_MAX_SERVERS = 1000
_MAX_GPU_COUNT = 256


def _validate_positive_int(v: object, field_name: str, max_limit: int) -> Optional[int]:
    """
    Validate that an extracted quantity or count is a positive whole integer.

    - Rejects booleans (since bool is a subclass of int in Python: isinstance(True, int) is True).
    - Explicitly supports int, integral float, integral Decimal, and integral numeric string.
    - Rejects non-integral floats, Decimals (e.g. Decimal("2.5")), and decimal strings (e.g. "2.5").
    - Rejects all unsupported types (lists, dicts, custom objects, etc.) without unrestricted coercion.
    - Rejects values <= 0 or > max_limit.
    - Returns None on invalid input so deterministic post-processing flags the field as missing.
    """
    if v is None:
        return None

    if isinstance(v, bool):
        logger.warning(
            "%s cannot be a boolean (got %r). Treating as missing.", field_name, v
        )
        return None

    if isinstance(v, int):
        n = v
    elif isinstance(v, float):
        if not v.is_integer():
            logger.warning(
                "%s must be a whole integer (got non-integral float %r). Treating as missing.",
                field_name,
                v,
            )
            return None
        n = int(v)
    elif isinstance(v, Decimal):
        try:
            if v % 1 != 0:
                logger.warning(
                    "%s must be a whole integer (got non-integral Decimal %r). Treating as missing.",
                    field_name,
                    v,
                )
                return None
            n = int(v)
        except (InvalidOperation, TypeError, ValueError):
            logger.warning(
                "%s could not be evaluated as integer Decimal: %r → None",
                field_name,
                v,
            )
            return None
    elif isinstance(v, str):
        cleaned = v.strip()
        try:
            if "." in cleaned:
                f = float(cleaned)
                if not f.is_integer():
                    logger.warning(
                        "%s must be a whole integer (got non-integral string %r). Treating as missing.",
                        field_name,
                        v,
                    )
                    return None
                n = int(f)
            else:
                n = int(cleaned)
        except (TypeError, ValueError):
            logger.warning(
                "%s could not be parsed as integer: %r → None", field_name, v
            )
            return None
    else:
        # Unsupported type: reject without attempting unrestricted int(v) coercion
        logger.warning(
            "%s received unsupported type %s: %r. Treating as missing.",
            field_name,
            type(v).__name__,
            v,
        )
        return None

    if n <= 0:
        logger.warning(
            "%s must be positive (got %d). Treating as missing.", field_name, n
        )
        return None
    if n > max_limit:
        logger.warning(
            "%s %d exceeds upper limit %d. Treating as missing.",
            field_name,
            n,
            max_limit,
        )
        return None
    return n


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

    @field_validator("num_servers", mode="before")
    @classmethod
    def validate_num_servers(cls, v: object) -> Optional[int]:
        return _validate_positive_int(v, "num_servers", _MAX_SERVERS)

    @field_validator("gpu_count_per_server", mode="before")
    @classmethod
    def validate_gpu_count_per_server(cls, v: object) -> Optional[int]:
        return _validate_positive_int(v, "gpu_count_per_server", _MAX_GPU_COUNT)


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
