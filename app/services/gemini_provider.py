"""
Google Gemini extraction provider for Rapid Stream Technology.

Uses Google's official 'google-genai' SDK to perform structured specification
extraction from natural-language customer inquiries.

IMPORTANT ARCHITECTURAL GUARDRAIL:
The LLM ONLY extracts customer requirements into an ExtractionResult schema.
The LLM NEVER determines selling prices, internal costs, inventory levels,
catalog matching, or quotation approvals. Those remain strictly deterministic.
"""

import json
import logging
import os
from typing import Optional

import pydantic
from google import genai
from google.genai import types

from app.config import settings
from app.models.schemas import ExtractionConfidence, ExtractionResult
from app.services.extraction import ExtractionProvider

logger = logging.getLogger(__name__)

# System instructions constraining the model solely to extraction
EXTRACTION_SYSTEM_INSTRUCTION = """
You are a precise hardware requirement extraction system for Rapid Stream Technology FZE, an enterprise AI server provider.
Your sole responsibility is to parse natural-language customer procurement inquiries into a structured JSON object.

CORE RULES:
1. Extract ONLY specifications explicitly mentioned or unambiguously implied by the customer.
2. DO NOT invent GPU counts, RAM sizes, server quantities, or specifications not stated.
3. NEVER calculate prices, taxes, commercial discounts, or margins.
4. NEVER determine, assume, or report warehouse stock or inventory availability.
5. NEVER approve, reject, or decide quotations.

FIELDS TO EXTRACT:
- sku: Official server SKU if explicitly named (e.g., RST-RTX8, RST-H2004, RST-B3008). Null if not stated.
- gpu_family: GPU family name (e.g., "RTX", "H200", "B300"). Null if not stated.
- gpu_model: Specific model name if mentioned (e.g., "RTX PRO 6000 Server Edition", "H200 NVL", "HGX B300").
- gpu_memory_gb: VRAM per GPU in GB as an integer (e.g., 96, 141, 288). Null if not stated.
- gpu_count_per_server: Number of GPUs in each individual server as an integer (e.g., 4 or 8). Convert number words like "eight" to 8. Null if not stated.
- num_servers: Total count of complete server systems requested as an integer (e.g., 1, 2, 5). Convert number words like "two" to 2. Null if not stated.
- ram_gb: Total system RAM in GB as an integer (e.g., 512, 2048 for 2 TB). Null if not stated.
- explicitly_stated: List of attribute names explicitly present in the customer message.
- inferred: List of attribute names inferred from context rather than stated explicitly.
- missing_fields: Critical fields missing for quotation matching. A complete request requires:
    * (gpu_family OR sku)
    * (gpu_count_per_server OR sku)
    * num_servers
  If any of these 3 requirements are unsatisfied, include the missing field name in missing_fields.
- confidence: "HIGH" if all essential specifications are present; "MEDIUM" if 2-3 fields are present; "LOW" if 0-1 fields are present.
"""

PLACEHOLDER_KEYS = {
    "paste_your_gemini_api_key_here",
    "your_gemini_api_key_here",
    "changeme",
    "none",
    "",
}


class GeminiProviderError(RuntimeError):
    """Base exception for Gemini provider errors."""


class GeminiAuthenticationError(GeminiProviderError):
    """Raised when the Gemini API key is missing, invalid, or unauthorized."""


class GeminiRateLimitError(GeminiProviderError):
    """Raised when Gemini API rate limit or quota is exhausted."""


class GeminiUnavailableError(GeminiProviderError):
    """Raised when Gemini API service is temporarily unavailable (503)."""


class GeminiTimeoutError(GeminiProviderError):
    """Raised when a call to the Gemini API exceeds the configured timeout."""


class GeminiResponseError(GeminiProviderError):
    """Raised when Gemini returns malformed, incomplete, or truncated JSON."""


class GeminiExtractionProvider(ExtractionProvider):
    """
    Production-quality extraction provider powered by Google Gemini.
    Uses google-genai SDK with schema-constrained JSON outputs.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
        client: Optional[genai.Client] = None,
    ):
        raw_key = (
            api_key
            or getattr(settings, "gemini_api_key", None)
            or os.environ.get("GEMINI_API_KEY", "")
        )
        cleaned_key = raw_key.strip() if raw_key else ""

        if not cleaned_key or cleaned_key.lower() in PLACEHOLDER_KEYS:
            raise GeminiAuthenticationError(
                "Gemini provider requires a valid GEMINI_API_KEY in environment or .env. "
                "Set GEMINI_API_KEY or use LLM_PROVIDER=mock for offline development."
            )

        self.api_key = cleaned_key
        self.model = model or getattr(settings, "gemini_model", "gemini-3.1-flash-lite")
        self.timeout = timeout or getattr(settings, "gemini_timeout_seconds", 30.0)
        self.max_output_tokens = max_output_tokens or getattr(
            settings, "gemini_max_output_tokens", 1024
        )
        self.max_retries = (
            max_retries
            if max_retries is not None
            else getattr(settings, "gemini_max_retries", 2)
        )

        # Allow client injection for unit tests.
        # When no pre-built client is supplied we configure the HTTP layer with the
        # configured timeout so that slow or stalled Gemini API calls are bounded.
        if client is not None:
            self._client = client
        else:
            try:
                http_opts = types.HttpOptions(timeout=int(self.timeout * 1000))
                self._client = genai.Client(
                    api_key=self.api_key, http_options=http_opts
                )
            except Exception as exc:
                logger.error("Failed to initialize Gemini client with timeout: %s", exc)
                raise GeminiProviderError(
                    f"Failed to initialize Gemini client with timeout ({self.timeout}s): {exc}"
                ) from exc
        logger.info(
            "Initialized GeminiExtractionProvider (model=%s, timeout=%.1fs, max_tokens=%d, max_retries=%d)",
            self.model,
            self.timeout,
            self.max_output_tokens,
            self.max_retries,
        )

    def extract(self, message: str) -> ExtractionResult:
        """
        Extract hardware requirements from customer text via Gemini.
        Returns a validated ExtractionResult Pydantic model.
        """
        if not message or not message.strip():
            return ExtractionResult(
                missing_fields=["gpu_family", "gpu_count_per_server", "num_servers"],
                confidence=ExtractionConfidence.LOW,
            )

        config = types.GenerateContentConfig(
            system_instruction=EXTRACTION_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=ExtractionResult,
            temperature=0.0,
            max_output_tokens=self.max_output_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=message,
                    config=config,
                )
                break
            except Exception as exc:
                exc_str = str(exc).lower()
                # Do not retry fatal authentication or bad-request errors
                if any(
                    k in exc_str
                    for k in (
                        "api_key",
                        "unauthenticated",
                        "permission_denied",
                        "401",
                        "403",
                        "400",
                        "invalid_argument",
                    )
                ):
                    self._handle_api_exception(exc)
                if attempt < self.max_retries:
                    backoff = 0.5 * (2**attempt)
                    logger.warning(
                        "Transient Gemini API failure (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1,
                        self.max_retries,
                        backoff,
                        type(exc).__name__,
                    )
                    import time

                    time.sleep(backoff)
                else:
                    self._handle_api_exception(exc)

        # ── 1. Diagnostic metadata inspection ──
        candidates = getattr(response, "candidates", None) or []
        candidate_count = len(candidates)
        first_candidate = candidates[0] if candidates else None
        finish_reason = (
            getattr(first_candidate, "finish_reason", None) if first_candidate else None
        )

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = (
            getattr(usage, "candidates_token_count", None) if usage else None
        )

        raw_text = getattr(response, "text", "") or ""
        text_length = len(raw_text)

        logger.info(
            "Gemini response metadata: model=%s, candidates=%d, finish_reason=%s, prompt_tokens=%s, output_tokens=%s, text_length=%d",
            self.model,
            candidate_count,
            finish_reason,
            prompt_tokens,
            output_tokens,
            text_length,
        )

        # ── 2. Candidate and finish_reason validation ──
        if not candidates or first_candidate is None:
            logger.error("Gemini returned zero response candidates")
            raise GeminiResponseError("Gemini API returned zero response candidates")

        finish_reason_str = str(finish_reason).upper()
        if "MAX_TOKENS" in finish_reason_str:
            logger.error(
                "Gemini response truncated: reached MAX_TOKENS limit (finish_reason=%s, text_length=%d, output_tokens=%s)",
                finish_reason,
                text_length,
                output_tokens,
            )
            raise GeminiResponseError(
                "Upstream model response was truncated (reached MAX_TOKENS limit)"
            )

        if any(b in finish_reason_str for b in ("SAFETY", "RECITATION", "BLOCK")):
            logger.error(
                "Gemini response stopped due to policy filter: %s", finish_reason
            )
            raise GeminiResponseError(
                f"Upstream model response stopped due to {finish_reason}"
            )

        # ── 3. Content and JSON completeness verification ──
        stripped_text = raw_text.strip()
        if not stripped_text:
            logger.error("Gemini returned an empty text payload")
            raise GeminiResponseError("Gemini API returned an empty response")

        if not (stripped_text.startswith("{") and stripped_text.endswith("}")):
            logger.error(
                "Gemini returned incomplete JSON (missing matching braces, length=%d)",
                text_length,
            )
            raise GeminiResponseError(
                "Incomplete structured JSON response from Gemini (missing closing braces)"
            )

        # ── 4. Pydantic validation (never attempt unsafe JSON repair) ──
        try:
            result = ExtractionResult.model_validate_json(stripped_text)
        except (pydantic.ValidationError, json.JSONDecodeError) as val_err:
            logger.error("Failed to parse structured output from Gemini: %s", val_err)
            raise GeminiResponseError(
                f"Invalid structured JSON response from Gemini: {val_err}"
            ) from val_err

        # ── 5. Post-process for defensive validation and consistency ──
        return self._post_process_result(result)

    def _post_process_result(self, result: ExtractionResult) -> ExtractionResult:
        """
        Deterministic post-processing that OVERWRITES Gemini's missing_fields
        and confidence with application-computed values.

        WHY: The LLM may inconsistently report missing_fields (e.g. omitting
        fields it left null). We never trust Gemini's raw missing_fields —
        we recompute from actual field values using RST business rules.

        BUSINESS RULES for missing_fields (catalog matching requirements):
            1. (gpu_family OR sku) — at least one must be present
            2. (gpu_count_per_server OR sku) — at least one must be present
            3. num_servers — must be present

        OPTIONAL ENRICHMENT fields (NOT reported as missing even when null):
            - gpu_model, gpu_memory_gb, ram_gb
            These improve the quotation but are NOT required for catalog
            matching. When an SKU is provided, catalog lookup directly
            matches without needing individual GPU specs.

        CONFIDENCE is computed from 4 signal dimensions:
            - gpu_family OR sku present
            - gpu_memory_gb present
            - gpu_count_per_server OR sku present
            - num_servers present
            4 filled → HIGH, 2-3 → MEDIUM, 0-1 → LOW
        """
        # ── 1. Recompute missing_fields deterministically ──
        # We build from scratch — never trust Gemini's missing_fields list.
        missing: list[str] = []

        if not result.gpu_family and not result.sku:
            missing.append("gpu_family")
        if not result.gpu_count_per_server and not result.sku:
            missing.append("gpu_count_per_server")
        if not result.num_servers:
            missing.append("num_servers")

        result.missing_fields = missing

        # ── 2. Recompute confidence from filled signal dimensions ──
        filled_count = sum(
            [
                bool(result.gpu_family or result.sku),
                bool(result.gpu_memory_gb),
                bool(result.gpu_count_per_server or result.sku),
                bool(result.num_servers),
            ]
        )
        if filled_count >= 4:
            result.confidence = ExtractionConfidence.HIGH
        elif filled_count >= 2:
            result.confidence = ExtractionConfidence.MEDIUM
        else:
            result.confidence = ExtractionConfidence.LOW

        # ── 3. Detect extraction inconsistencies where fields listed in explicitly_stated are null ──
        inconsistent_fields = [
            f
            for f in (result.explicitly_stated or [])
            if hasattr(result, f) and getattr(result, f) is None
        ]
        if inconsistent_fields:
            logger.warning(
                "Extraction inconsistency detected: fields listed in explicitly_stated but returned as null: %s",
                inconsistent_fields,
            )
            # Reconcile explicitly_stated: null fields were not actually populated
            result.explicitly_stated = [
                f
                for f in (result.explicitly_stated or [])
                if f not in inconsistent_fields
            ]

        # ── 4. Deduplicate explicitly_stated and inferred ──
        explicit_set = set(result.explicitly_stated or [])
        inferred_set = set(result.inferred or []) - explicit_set
        result.explicitly_stated = sorted(list(explicit_set))
        result.inferred = sorted(list(inferred_set))

        return result

    def _handle_api_exception(self, exc: Exception) -> None:
        """
        Categorize API exceptions into specific GeminiProviderError subclasses.
        Ensures API keys are NEVER logged or leaked.
        """
        exc_str = str(exc).lower()

        # 1. Authentication / Permission errors
        if any(
            k in exc_str
            for k in (
                "api_key",
                "unauthenticated",
                "permission_denied",
                "api key not valid",
                "invalid api key",
                "401",
                "403",
            )
        ):
            logger.error(
                "Gemini API authentication failed: invalid or unauthorized key"
            )
            raise GeminiAuthenticationError(
                "Gemini API authentication failed. Verify that GEMINI_API_KEY is valid."
            ) from exc

        # 2. Service unavailable (503 / High demand)
        if any(
            k in exc_str
            for k in (
                "503",
                "unavailable",
                "high demand",
            )
        ):
            logger.error("Gemini API temporarily unavailable: 503 / high demand")
            raise GeminiUnavailableError(
                "Gemini API is temporarily unavailable due to high demand. Please retry later."
            ) from exc

        # 3. Rate limits & quota exhaustion (429)
        if any(
            k in exc_str
            for k in (
                "resource_exhausted",
                "429",
                "quota",
                "rate limit",
                "rate_limit",
            )
        ):
            logger.error("Gemini API rate limit or quota exceeded")
            raise GeminiRateLimitError(
                "Gemini API rate limit or quota exceeded. Please retry after a brief delay."
            ) from exc

        # 4. Timeouts (504)
        if any(
            k in exc_str
            for k in (
                "timeout",
                "timed out",
                "deadline_exceeded",
                "504",
                "408",
            )
        ) or isinstance(exc, TimeoutError):
            logger.error("Gemini API request timed out")
            raise GeminiTimeoutError(
                f"Gemini API request timed out after {self.timeout}s."
            ) from exc

        # 5. General / server failures
        logger.error("Gemini API request failed: %s", type(exc).__name__)
        raise GeminiProviderError(f"Gemini API request failed: {exc}") from exc
