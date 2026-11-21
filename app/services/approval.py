"""Approval workflow service."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.database_models import QuotationDraft
from app.models.schemas import ApprovalResponse, QuotationStatus

logger = logging.getLogger(__name__)


def approve_quotation(
    request_id: str,
    reviewer_id: str,
    notes: str | None,
    db: Session,
) -> ApprovalResponse:
    """
    Approve a quotation draft.

    Rules:
    - Draft must exist and be in PENDING_APPROVAL status
    - Drafts with pending prices cannot be approved
    - Only internal reviewers can call this (enforced at API layer)
    """
    draft = (
        db.query(QuotationDraft).filter(QuotationDraft.request_id == request_id).first()
    )

    if draft is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No quotation draft found for request_id '{request_id}'",
        )

    if draft.status == QuotationStatus.APPROVED:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This quotation has already been approved.",
        )

    if draft.status != QuotationStatus.PENDING_APPROVAL:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot approve a draft in status '{draft.status}'. "
                "Only PENDING_APPROVAL drafts can be approved."
            ),
        )

    if draft.unit_selling_price is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot approve a quotation with a pending price. "
                "The sales team must confirm pricing first."
            ),
        )

    now = datetime.now(timezone.utc)
    draft.status = QuotationStatus.APPROVED
    draft.reviewer_id = reviewer_id
    draft.approved_at = now
    if notes:
        draft.notes = (draft.notes or "") + f"\nReviewer notes: {notes}"

    db.commit()
    db.refresh(draft)

    logger.info("Quotation %s approved by %s", request_id, reviewer_id)

    return ApprovalResponse(
        request_id=request_id,
        status=QuotationStatus.APPROVED,
        reviewer_id=reviewer_id,
        approved_at=now,
        notes=notes,
    )
