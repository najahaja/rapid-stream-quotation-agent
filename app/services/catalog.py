"""Catalog matching service — deterministic, never LLM-generated."""

import logging
import re

from app.data.catalog import CATALOG, CatalogEntry, get_entry
from app.models.schemas import CatalogMatchResult, ExtractionResult

logger = logging.getLogger(__name__)

# Unambiguous aliases justified by the actual catalogue products:
# Maps SKU -> set of normalized valid representations for that SKU's GPU model.
CATALOG_GPU_MODEL_ALIASES: dict[str, set[str]] = {
    "RST-RTX8": {
        "RTX PRO 6000 SERVER EDITION",
        "RTX PRO 6000",
        "RTX 6000 SERVER EDITION",
        "RTX 6000 SERVER",
        "PRO 6000",
    },
    "RST-H2004": {
        "H200 NVL",
        "H200",
    },
    "RST-B3008": {
        "HGX B300",
        "B300",
        "B300 HGX",
    },
}


def _normalize_hardware_name(name: str) -> str:
    """Normalize a hardware name by stripping, uppercasing, and standardizing whitespace."""
    return re.sub(r"\s+", " ", name.strip().upper())


def _is_gpu_model_match(requested_model: str, entry: CatalogEntry) -> bool:
    """
    Check if a customer-specified GPU model unambiguously matches a catalog entry.

    Conservative comparison:
    1. Exact normalized match with entry.gpu_model.
    2. Or presence in the explicit, unambiguous alias set for that specific SKU.
    Arbitrary substring containment is explicitly avoided so different models
    (e.g. RTX 4090 vs RTX PRO 6000, or H100 vs H200) are not treated as equivalent.
    """
    norm_requested = _normalize_hardware_name(requested_model)
    norm_entry = _normalize_hardware_name(entry.gpu_model)
    if norm_requested == norm_entry:
        return True
    aliases = CATALOG_GPU_MODEL_ALIASES.get(entry.sku, set())
    return norm_requested in aliases


def _check_contradictions(entry: CatalogEntry, extraction: ExtractionResult) -> str | None:
    """
    Validate explicitly-stated customer specs against a matched catalog entry.

    Returns a human-readable conflict message if any explicitly stated field
    contradicts the catalog entry, or None if everything is consistent.

    Only fields the customer explicitly stated are checked — inferred or null
    fields are intentionally ignored so we never penalise the customer for
    providing *less* information than the catalog has.
    """
    stated = set(extraction.explicitly_stated or [])
    conflicts: list[str] = []

    if "gpu_count_per_server" in stated and extraction.gpu_count_per_server is not None:
        if extraction.gpu_count_per_server != entry.gpu_count:
            conflicts.append(
                f"GPU count per server: you requested {extraction.gpu_count_per_server} "
                f"but {entry.sku} has {entry.gpu_count}"
            )

    if "gpu_memory_gb" in stated and extraction.gpu_memory_gb is not None:
        if extraction.gpu_memory_gb != entry.gpu_memory_gb:
            conflicts.append(
                f"GPU memory: you specified {extraction.gpu_memory_gb} GB/GPU "
                f"but {entry.sku} has {entry.gpu_memory_gb} GB/GPU"
            )

    if "gpu_family" in stated and extraction.gpu_family is not None:
        if _normalize_hardware_name(extraction.gpu_family) != _normalize_hardware_name(
            entry.gpu_family
        ):
            conflicts.append(
                f"GPU family: you specified '{extraction.gpu_family}' "
                f"but {entry.sku} uses '{entry.gpu_family}'"
            )

    if "gpu_model" in stated and extraction.gpu_model is not None:
        if not _is_gpu_model_match(extraction.gpu_model, entry):
            conflicts.append(
                f"GPU model: you specified '{extraction.gpu_model}' "
                f"but {entry.sku} uses '{entry.gpu_model}'"
            )

    if "ram_gb" in stated and extraction.ram_gb is not None:
        if extraction.ram_gb != entry.ram_gb:
            conflicts.append(
                f"RAM: you specified {extraction.ram_gb} GB "
                f"but {entry.sku} has {entry.ram_gb} GB"
            )

    if conflicts:
        return (
            f"The specifications you provided contradict catalog entry {entry.sku}: "
            + "; ".join(conflicts)
            + ". Please verify your requirements or contact sales."
        )
    return None


def match_catalog(extraction: ExtractionResult) -> CatalogMatchResult:
    """
    Match extracted requirements to a catalog entry.

    Matching priority:
    1. Explicit SKU mention (validated against catalog; contradictory explicit
       specs are rejected rather than silently discarded)
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
        # Validate that any other explicitly-stated specs are consistent with the entry
        conflict_msg = _check_contradictions(entry, extraction)
        if conflict_msg:
            logger.warning(
                "Explicit SKU '%s' conflicts with stated specs: %s",
                extraction.sku,
                conflict_msg,
            )
            return CatalogMatchResult(matched=False, match_notes=conflict_msg)
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
        entry = candidates[0]
        conflict_msg = _check_contradictions(entry, extraction)
        if conflict_msg:
            logger.warning(
                "Configuration match for '%s' conflicts with stated specs: %s",
                entry.sku,
                conflict_msg,
            )
            return CatalogMatchResult(matched=False, match_notes=conflict_msg)
        return _entry_to_match(entry, "Matched by configuration requirements")

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
