"""Catalog matching service — deterministic, never LLM-generated."""

import logging

from app.data.catalog import CATALOG, CatalogEntry, get_entry
from app.models.schemas import CatalogMatchResult, ExtractionResult

logger = logging.getLogger(__name__)


def match_catalog(extraction: ExtractionResult) -> CatalogMatchResult:
    """
    Match extracted requirements to a catalog entry.

    Matching priority:
    1. Explicit SKU mention (validated against catalog)
    2. GPU family + GPU count + GPU memory
    3. GPU family + GPU memory (count unknown → ask clarification)
    4. GPU family alone (ambiguous)
    5. No match
    """
    # 1. Direct SKU match
    if extraction.sku:
        entry = get_entry(extraction.sku)
        if entry is None:
            return CatalogMatchResult(
                matched=False,
                match_notes=f"SKU '{extraction.sku}' not found in catalog.",
            )
        return _entry_to_match(entry, f"Matched by explicit SKU: {extraction.sku}")

    candidates = list(CATALOG.values())

    # 2. Filter by GPU family
    if extraction.gpu_family:
        family_upper = extraction.gpu_family.upper()
        candidates = [c for c in candidates if family_upper in c.gpu_family.upper()]

    # 3. Filter by GPU memory
    if extraction.gpu_memory_gb:
        candidates = [
            c for c in candidates if c.gpu_memory_gb == extraction.gpu_memory_gb
        ]

    # 4. Filter by GPU count per server
    if extraction.gpu_count_per_server:
        candidates = [
            c for c in candidates if c.gpu_count == extraction.gpu_count_per_server
        ]

    if len(candidates) == 1:
        return _entry_to_match(candidates[0], "Matched by configuration requirements")

    if len(candidates) > 1:
        skus = ", ".join(c.sku for c in candidates)
        return CatalogMatchResult(
            matched=False,
            match_notes=f"Ambiguous match: multiple catalog entries match ({skus}). More details needed.",
        )

    # No candidates
    if extraction.gpu_family:
        return CatalogMatchResult(
            matched=False,
            match_notes=(
                f"No catalog entry found for GPU family '{extraction.gpu_family}' "
                f"with the specified configuration."
            ),
        )

    return CatalogMatchResult(
        matched=False,
        match_notes="Insufficient information to match a catalog entry.",
    )


def _entry_to_match(entry: CatalogEntry, notes: str) -> CatalogMatchResult:
    return CatalogMatchResult(
        matched=True,
        sku=entry.sku,
        description=entry.description,
        unit_selling_price=entry.selling_price,
        lead_time=entry.lead_time,
        stock_available=entry.stock,
        price_pending=entry.selling_price is None,
        match_notes=notes,
    )
