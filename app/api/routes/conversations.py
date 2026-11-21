"""Conversation continuation endpoint for clarification follow-ups."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_customer_or_reviewer
from app.models.database_models import Conversation
from app.models.schemas import ClarificationMessage, QuotationRequest, QuotationResponse

router = APIRouter(prefix="/api/v1/conversations", tags=["Conversations"])
logger = logging.getLogger(__name__)


@router.post(
    "/{conversation_id}/messages",
    response_model=QuotationResponse,
    summary="Send a clarification message to continue a conversation",
    description=(
        "Use this endpoint when the system returned NEEDS_CLARIFICATION. "
        "Send the customer's clarification reply; the system will merge it with "
        "the previous context and attempt to complete the quotation."
    ),
)
def send_clarification(
    conversation_id: str,
    body: ClarificationMessage,
    db: Session = Depends(get_db),
    user: dict = Depends(require_customer_or_reviewer),
) -> QuotationResponse:
    # Verify the conversation exists
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )

    # Verify conversation ownership for customers
    if (
        user.get("role") == "customer"
        and conv.customer_id
        and conv.customer_id != user.get("sub")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: conversation belongs to another customer",
        )

    # Reuse the main quotation workflow with this conversation's accumulated state
    from app.services.quotation import process_quotation

    request = QuotationRequest(
        request_id=body.request_id,
        conversation_id=conversation_id,
        message=body.message,
    )
    return process_quotation(request, db, current_user=user)
