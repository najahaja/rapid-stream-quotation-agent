"""
Core quotation workflow service.

This module orchestrates:
1. Extraction of requirements from natural language
2. Catalog matching
3. Stock checking
4. Price calculation (from catalog only — never LLM-generated)
5. Draft creation with correct status
"""

import json
import logging
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.data.catalog import get_entry
from app.models.database_models import Conversation, Message, QuotationDraft
from app.models.schemas import (
    CatalogMatchResult,
    ExtractionResult,
    QuotationRequest,
    QuotationResponse,
    QuotationStatus,
    StockCheckResult,
)
from app.services.catalog import match_catalog
from app.services.extraction import get_extraction_provider
from app.services.inventory import check_stock

logger = logging.getLogger(__name__)


def get_active_extraction_provider():
    """Return the active extraction provider based on current settings."""
    return get_extraction_provider(settings.llm_provider)


# Backward-compatible reference
_provider = get_extraction_provider("mock")


def _generate_clarification_question(extraction: ExtractionResult) -> str:
    """Generate a specific clarification question based on what is missing."""
    missing = extraction.missing_fields
    if "gpu_family" in missing and "sku" not in extraction.explicitly_stated:
        return (
            "Could you specify the GPU family you need? "
            "We offer RTX PRO 6000, H200 NVL, and HGX B300 GPU servers."
        )
    if "gpu_count_per_server" in missing:
        return "How many GPUs do you require per server? (e.g., 4 or 8)"
    if "num_servers" in missing:
        return "How many servers do you need?"
    return "Could you provide more details about your server requirements?"


def _merge_extractions(
    base: ExtractionResult, update: ExtractionResult
) -> ExtractionResult:
    """
    Merge a clarification extraction into the base extraction.
    Fields in 'update' that are not None take precedence.
    """
    merged_data = base.model_dump()
    update_data = update.model_dump()

    for field in [
        "gpu_family",
        "gpu_model",
        "gpu_memory_gb",
        "gpu_count_per_server",
        "num_servers",
        "ram_gb",
        "sku",
    ]:
        if update_data.get(field) is not None:
            merged_data[field] = update_data[field]

    # Merge lists
    merged_data["explicitly_stated"] = list(
        set(base.explicitly_stated) | set(update.explicitly_stated)
    )
    merged_data["inferred"] = list(set(base.inferred) | set(update.inferred))
    merged = ExtractionResult(**merged_data)

    # Recompute missing fields
    missing = []
    if not merged.gpu_family and not merged.sku:
        missing.append("gpu_family")
    if not merged.gpu_count_per_server and not merged.sku:
        missing.append("gpu_count_per_server")
    if not merged.num_servers:
        missing.append("num_servers")
    merged.missing_fields = missing
    return merged


def _calculate_prices(sku: str, quantity: int) -> tuple[float | None, float | None]:
    """
    Calculate unit and total selling prices from catalog.
    Returns (unit_price, total_price). Both are None if price is pending.
    """
    entry = get_entry(sku)
    if entry is None or entry.selling_price is None:
        return None, None
    return entry.selling_price, entry.selling_price * quantity


def _get_or_create_conversation(
    db: Session, conversation_id: str, customer_id: str | None = None
) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        try:
            conv = Conversation(id=conversation_id, customer_id=customer_id)
            db.add(conv)
            db.commit()
        except Exception:
            db.rollback()
            conv = db.get(Conversation, conversation_id)
            if conv is None:
                # Fallback if another thread hasn't committed yet
                import time

                for _ in range(5):
                    time.sleep(0.02)
                    conv = db.get(Conversation, conversation_id)
                    if conv is not None:
                        break
    elif conv.customer_id is None and customer_id is not None:
        conv.customer_id = customer_id
        db.commit()
    return conv


def process_quotation(
    request: QuotationRequest, db: Session, current_user: dict | None = None
) -> QuotationResponse:
    """
    Main quotation processing workflow.

    Idempotency: If request_id already exists in DB, return the stored result.
    Data isolation: Enforces customer ownership on request_id and conversation_id.
    """
    user_id = current_user.get("sub") if current_user else None
    user_role = current_user.get("role") if current_user else None

    # --- IDEMPOTENCY CHECK ---
    existing = (
        db.query(QuotationDraft)
        .filter(QuotationDraft.request_id == request.request_id)
        .first()
    )
    if existing is not None:
        if (
            user_role == "customer"
            and existing.customer_id
            and existing.customer_id != user_id
        ):
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: quotation belongs to another customer",
            )
        logger.info("Idempotent return for request_id=%s", request.request_id)
        return _draft_to_response(existing, db)

    # --- Ensure conversation exists and check isolation ---
    conv = _get_or_create_conversation(db, request.conversation_id, customer_id=user_id)
    if user_role == "customer" and conv.customer_id and conv.customer_id != user_id:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: conversation belongs to another customer",
        )

    # --- Load accumulated state from previous turns ---
    accumulated_state = json.loads(conv.accumulated_state or "{}")
    base_extraction = (
        ExtractionResult(**accumulated_state) if accumulated_state else None
    )

    # --- Extract from current message ---
    provider = get_active_extraction_provider()
    current_extraction = provider.extract(request.message)
    logger.info("Extracted: %s", current_extraction)

    # --- Merge with accumulated state ---
    if base_extraction:
        extraction = _merge_extractions(base_extraction, current_extraction)
    else:
        extraction = current_extraction

    # --- Store customer message ---
    db.add(
        Message(
            conversation_id=conv.id,
            role="customer",
            content=request.message,
        )
    )

    # --- Determine if clarification needed ---
    catalog_match: CatalogMatchResult | None = None
    stock_check: StockCheckResult | None = None
    unit_price: float | None = None
    total_price: float | None = None
    status: QuotationStatus
    clarification_question: str | None = None
    agent_message = ""

    if extraction.missing_fields:
        clarification_question = _generate_clarification_question(extraction)
        status = QuotationStatus.NEEDS_CLARIFICATION
        agent_message = clarification_question

        # Store accumulated state for next turn
        # Save only non-None fields to avoid overwriting good data
        state_to_save = {
            k: v
            for k, v in extraction.model_dump().items()
            if v is not None and v != [] and v != {}
        }
        conv.accumulated_state = json.dumps(state_to_save)

        db.add(
            Message(
                conversation_id=conv.id, role="agent", content=clarification_question
            )
        )
    else:
        # --- Catalog matching ---
        catalog_match = match_catalog(extraction)

        if not catalog_match.matched:
            status = QuotationStatus.PENDING_SALES_REVIEW
            agent_message = f"Could not match catalog: {catalog_match.match_notes}"
        else:
            sku = catalog_match.sku
            quantity = extraction.num_servers or 1

            # --- Stock check ---
            stock_check = check_stock(sku, quantity)

            # --- Price calculation (catalog-only) ---
            unit_price, total_price = _calculate_prices(sku, quantity)
            price_pending = catalog_match.price_pending

            # --- Determine status ---
            if price_pending:
                status = QuotationStatus.PENDING_SALES_REVIEW
                agent_message = (
                    f"The price for {sku} is pending supplier quote. "
                    "Your request has been routed to the sales team."
                )
            elif stock_check.requires_sales_review:
                status = QuotationStatus.PENDING_SALES_REVIEW
                agent_message = stock_check.message
            else:
                status = QuotationStatus.PENDING_APPROVAL
                agent_message = (
                    f"Quotation draft created for {quantity}× {sku} "
                    f"at ${unit_price:,.0f} each. Total: ${total_price:,.0f}."
                )

            # Clear accumulated state once matched
            conv.accumulated_state = "{}"
            db.add(
                Message(conversation_id=conv.id, role="agent", content=agent_message)
            )

    # --- Create quotation draft ---
    draft_id = str(uuid.uuid4())
    draft = QuotationDraft(
        id=draft_id,
        request_id=request.request_id,
        conversation_id=conv.id,
        customer_id=user_id,
        sku=catalog_match.sku if catalog_match and catalog_match.matched else None,
        configuration=json.dumps(catalog_match.model_dump()) if catalog_match else None,
        extraction_data=json.dumps(extraction.model_dump()) if extraction else None,
        stock_check_data=json.dumps(stock_check.model_dump()) if stock_check else None,
        quantity=extraction.num_servers,
        unit_selling_price=unit_price,
        total_price=total_price,
        stock_available=catalog_match.stock_available if catalog_match else None,
        stock_requested=extraction.num_servers,
        lead_time=catalog_match.lead_time if catalog_match else None,
        status=status,
        clarification_question=clarification_question,
        notes=agent_message,
    )

    try:
        db.add(draft)
        db.commit()
    except Exception:
        db.rollback()
        db.expunge_all()
        import time

        existing = None
        for _ in range(5):
            existing = (
                db.query(QuotationDraft)
                .filter(QuotationDraft.request_id == request.request_id)
                .first()
            )
            if existing:
                break
            time.sleep(0.02)
        if existing:
            return _draft_to_response(existing, db)
        raise

    return QuotationResponse(
        request_id=request.request_id,
        conversation_id=conv.id,
        status=status,
        extraction=extraction,
        catalog_match=catalog_match,
        clarification_question=clarification_question,
        stock_check=stock_check,
        unit_selling_price=unit_price,
        total_price=total_price,
        price_pending=catalog_match.price_pending if catalog_match else False,
        draft_id=draft_id,
        message=agent_message,
    )


def _draft_to_response(draft: QuotationDraft, db: Session) -> QuotationResponse:
    """Reconstruct a QuotationResponse from a stored draft (for idempotent returns)."""
    configuration = json.loads(draft.configuration) if draft.configuration else None
    catalog_match = CatalogMatchResult(**configuration) if configuration else None

    extraction = (
        ExtractionResult(**json.loads(draft.extraction_data))
        if getattr(draft, "extraction_data", None)
        else ExtractionResult()
    )
    stock_check = (
        StockCheckResult(**json.loads(draft.stock_check_data))
        if getattr(draft, "stock_check_data", None)
        else None
    )

    return QuotationResponse(
        request_id=draft.request_id,
        conversation_id=draft.conversation_id,
        status=QuotationStatus(draft.status),
        extraction=extraction,
        catalog_match=catalog_match,
        clarification_question=draft.clarification_question,
        stock_check=stock_check,
        unit_selling_price=draft.unit_selling_price,
        total_price=draft.total_price,
        price_pending=(draft.unit_selling_price is None and draft.sku is not None),
        draft_id=draft.id,
        message=draft.notes or "",
    )


def get_draft_by_request_id(request_id: str, db: Session) -> QuotationDraft | None:
    return (
        db.query(QuotationDraft).filter(QuotationDraft.request_id == request_id).first()
    )
