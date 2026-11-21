"""Customer-facing quotation endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_customer_or_reviewer
from app.models.schemas import QuotationRequest, QuotationResponse
from app.services.quotation import (
    _draft_to_response,
    get_draft_by_request_id,
    process_quotation,
)

router = APIRouter(prefix="/api/v1/quotations", tags=["Quotations"])


@router.post(
    "",
    response_model=QuotationResponse,
    summary="Submit a quotation request",
    description=(
        "Submit a natural-language server quotation request. "
        "The system extracts requirements, matches the catalog, checks stock, "
        "and creates a quotation draft. Idempotent on request_id."
    ),
)
def submit_quotation(
    request: QuotationRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_customer_or_reviewer),
) -> QuotationResponse:
    return process_quotation(request, db, current_user=user)


@router.get(
    "/{request_id}",
    response_model=QuotationResponse,
    summary="Retrieve a quotation by request ID",
)
def get_quotation(
    request_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_customer_or_reviewer),
) -> QuotationResponse:
    draft = get_draft_by_request_id(request_id, db)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No quotation found for request_id '{request_id}'",
        )
    if (
        user.get("role") == "customer"
        and draft.customer_id
        and draft.customer_id != user.get("sub")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: quotation belongs to another customer",
        )
    return _draft_to_response(draft, db)
