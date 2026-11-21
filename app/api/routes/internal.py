"""Internal reviewer endpoints — requires internal_reviewer role."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_internal_reviewer
from app.data.catalog import get_entry
from app.models.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    InternalQuotationResponse,
)
from app.services.approval import approve_quotation
from app.services.quotation import _draft_to_response, get_draft_by_request_id

router = APIRouter(prefix="/api/v1/internal", tags=["Internal"])
logger = logging.getLogger(__name__)


@router.get(
    "/quotations/{request_id}",
    response_model=InternalQuotationResponse,
    summary="Retrieve quotation with internal cost information",
    description="Only accessible by internal reviewers. Shows internal costs and margin.",
)
def get_internal_quotation(
    request_id: str,
    db: Session = Depends(get_db),
    user: dict = Depends(require_internal_reviewer),
) -> InternalQuotationResponse:
    draft = get_draft_by_request_id(request_id, db)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No quotation found for request_id '{request_id}'",
        )

    base = _draft_to_response(draft, db)

    unit_internal_cost = None
    total_internal_cost = None
    gross_margin = None

    if draft.sku:
        entry = get_entry(draft.sku)
        if entry and entry.internal_cost is not None:
            unit_internal_cost = entry.internal_cost
            quantity = draft.quantity or 1
            total_internal_cost = entry.internal_cost * quantity
            if draft.unit_selling_price is not None:
                gross_margin = (
                    draft.unit_selling_price - entry.internal_cost
                ) * quantity

    return InternalQuotationResponse(
        **base.model_dump(),
        unit_internal_cost=unit_internal_cost,
        total_internal_cost=total_internal_cost,
        gross_margin=gross_margin,
    )


@router.post(
    "/quotations/{request_id}/approve",
    response_model=ApprovalResponse,
    summary="Approve a quotation draft",
    description=(
        "Approve a PENDING_APPROVAL quotation draft. "
        "Only internal reviewers can call this. "
        "Drafts with pending prices cannot be approved."
    ),
)
def approve(
    request_id: str,
    body: ApprovalRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_internal_reviewer),
) -> ApprovalResponse:
    reviewer_id = user["sub"]
    return approve_quotation(request_id, reviewer_id, body.notes, db)
