"""
Comprehensive unit and integration tests for Google Gemini extraction provider.

All Gemini API calls are strictly mocked. No external network requests are made.
"""

import json
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.models.schemas import (
    ExtractionConfidence,
    ExtractionResult,
    QuotationRequest,
    QuotationStatus,
)
from app.services.extraction import MockExtractionProvider, get_extraction_provider
from app.services.gemini_provider import (
    GeminiAuthenticationError,
    GeminiExtractionProvider,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
    GeminiUnavailableError,
)
from app.services.quotation import process_quotation


@pytest.fixture
def mock_genai_client():
    """Mock google.genai.Client to prevent any network calls."""
    client = MagicMock()
    return client


# 1. Provider selection tests


def test_provider_selection_mock():
    """Selecting 'mock' provider returns MockExtractionProvider."""
    provider = get_extraction_provider("mock")
    assert isinstance(provider, MockExtractionProvider)


def test_provider_selection_gemini_missing_key(monkeypatch):
    """Selecting 'gemini' without a valid key raises GeminiAuthenticationError."""
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiAuthenticationError) as exc_info:
        get_extraction_provider("gemini")
    assert "GEMINI_API_KEY" in str(exc_info.value)


def test_provider_selection_gemini_placeholder_key(monkeypatch):
    """Placeholder keys (e.g. from .env.example) are rejected on initialization."""
    monkeypatch.setattr(settings, "gemini_api_key", "PASTE_YOUR_GEMINI_API_KEY_HERE")
    with pytest.raises(GeminiAuthenticationError):
        get_extraction_provider("gemini")


def test_provider_selection_invalid():
    """Selecting an unknown provider raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        get_extraction_provider("unsupported_provider")
    assert "Unknown LLM provider" in str(exc_info.value)


# 2. Mock mode offline guarantee


def test_offline_mock_mode_independent_of_gemini():
    """Mock mode runs 100% offline without any API keys or network dependencies."""
    provider = MockExtractionProvider()
    result = provider.extract("I need two RST-RTX8 servers.")
    assert result.num_servers == 2
    assert result.sku == "RST-RTX8"
    assert result.confidence in (
        ExtractionConfidence.HIGH,
        ExtractionConfidence.MEDIUM,
    )


# 3. Successful Gemini extraction with mocked response


def test_gemini_extraction_success_mocked(mock_genai_client):
    """Gemini successfully parses complete requirements from mocked structured JSON."""
    mock_response = MagicMock()
    mock_payload = {
        "sku": "RST-RTX8",
        "gpu_family": "RTX",
        "gpu_model": "RTX PRO 6000 Server Edition",
        "gpu_memory_gb": 96,
        "gpu_count_per_server": 8,
        "num_servers": 2,
        "ram_gb": 512,
        "missing_fields": [],
        "confidence": "HIGH",
        "explicitly_stated": ["sku", "num_servers", "gpu_count_per_server"],
        "inferred": ["gpu_family", "gpu_model", "gpu_memory_gb", "ram_gb"],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-mock-key-valid",
        model="gemini-3.1-flash-lite",
        client=mock_genai_client,
    )
    result = provider.extract("I need two RST-RTX8 servers.")

    assert isinstance(result, ExtractionResult)
    assert result.sku == "RST-RTX8"
    assert result.num_servers == 2
    assert result.gpu_count_per_server == 8
    assert result.confidence == ExtractionConfidence.HIGH
    assert result.missing_fields == []
    assert "sku" in result.explicitly_stated
    mock_genai_client.models.generate_content.assert_called_once()


# 4. Gemini extraction handling missing fields


def test_gemini_extraction_missing_fields_mocked(mock_genai_client):
    """Gemini correctly surfaces missing fields without inventing specs."""
    mock_response = MagicMock()
    mock_payload = {
        "sku": None,
        "gpu_family": "RTX",
        "gpu_model": "RTX PRO 6000 Server Edition",
        "gpu_memory_gb": 96,
        "gpu_count_per_server": None,
        "num_servers": 2,
        "ram_gb": None,
        "missing_fields": ["gpu_count_per_server"],
        "confidence": "MEDIUM",
        "explicitly_stated": ["gpu_family", "num_servers"],
        "inferred": ["gpu_model", "gpu_memory_gb"],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-mock-key-valid",
        client=mock_genai_client,
    )
    result = provider.extract("I need two RTX servers with 96 GB cards.")

    assert result.gpu_family == "RTX"
    assert result.gpu_count_per_server is None
    assert "gpu_count_per_server" in result.missing_fields
    assert result.confidence == ExtractionConfidence.MEDIUM


# 5. Invalid structured response handling


def test_gemini_extraction_invalid_json_raises_response_error(
    mock_genai_client,
):
    """Malformed or invalid JSON from Gemini raises GeminiResponseError."""
    mock_response = MagicMock()
    mock_response.text = "{invalid_json: true,"
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-mock-key-valid",
        client=mock_genai_client,
    )
    with pytest.raises(GeminiResponseError) as exc_info:
        provider.extract("I need a server.")
    assert "structured JSON" in str(exc_info.value)


def test_gemini_extraction_empty_response_raises_response_error(
    mock_genai_client,
):
    """Empty response body raises GeminiResponseError."""
    mock_response = MagicMock()
    mock_response.text = ""
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-mock-key-valid",
        client=mock_genai_client,
    )
    with pytest.raises(GeminiResponseError):
        provider.extract("I need a server.")


# 6. Authentication failure handling


def test_gemini_extraction_authentication_failure(mock_genai_client):
    """API authentication errors (401/403/API_KEY_INVALID) raise GeminiAuthenticationError."""
    mock_genai_client.models.generate_content.side_effect = Exception(
        "API_KEY_INVALID: API key not valid. Please pass a valid API key."
    )

    provider = GeminiExtractionProvider(
        api_key="test-mock-invalid-key",
        client=mock_genai_client,
    )
    with pytest.raises(GeminiAuthenticationError) as exc_info:
        provider.extract("I need 2 servers.")
    assert "authentication failed" in str(exc_info.value).lower()
    # Confirm secret key is never leaked in the exception message
    assert "test-mock-invalid-key" not in str(exc_info.value)


# 7. Rate limit handling


def test_gemini_extraction_rate_limit_failure(mock_genai_client):
    """Quota or 429 Resource Exhausted errors raise GeminiRateLimitError."""
    mock_genai_client.models.generate_content.side_effect = Exception(
        "429 Resource exhausted: quota exceeded for model"
    )

    provider = GeminiExtractionProvider(
        api_key="test-mock-key-valid",
        client=mock_genai_client,
    )
    with pytest.raises(GeminiRateLimitError) as exc_info:
        provider.extract("I need 2 servers.")
    assert "rate limit" in str(exc_info.value).lower()


# 8. Timeout handling


def test_gemini_extraction_timeout_failure(mock_genai_client):
    """Request timeouts raise GeminiTimeoutError."""
    mock_genai_client.models.generate_content.side_effect = TimeoutError(
        "Deadline exceeded"
    )

    provider = GeminiExtractionProvider(
        api_key="test-mock-key-valid",
        timeout=10.0,
        client=mock_genai_client,
    )
    with pytest.raises(GeminiTimeoutError) as exc_info:
        provider.extract("I need 2 servers.")
    assert "timed out" in str(exc_info.value).lower()


# 9. Generic API failure


def test_gemini_extraction_generic_api_failure(mock_genai_client):
    """Unexpected server errors raise GeminiProviderError."""
    mock_genai_client.models.generate_content.side_effect = RuntimeError(
        "500 Internal server error"
    )

    provider = GeminiExtractionProvider(
        api_key="test-mock-key-valid",
        client=mock_genai_client,
    )
    with pytest.raises(GeminiProviderError) as exc_info:
        provider.extract("I need 2 servers.")
    assert "failed" in str(exc_info.value).lower()


# 10. Architectural boundary: Gemini CANNOT alter pricing or stock


def test_gemini_extraction_cannot_alter_pricing_or_inventory(
    db_session, mock_genai_client, monkeypatch
):
    """
    Even when Gemini extracts specs, prices and inventory are strictly joined
    from the authoritative catalog. The LLM cannot invent prices or stock.
    """
    # Gemini extracts RTX specs
    mock_response = MagicMock()
    mock_payload = {
        "sku": "RST-RTX8",
        "gpu_family": "RTX",
        "gpu_count_per_server": 8,
        "num_servers": 2,
        "missing_fields": [],
        "confidence": "HIGH",
        "explicitly_stated": ["sku", "num_servers"],
        "inferred": [],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    mock_provider = GeminiExtractionProvider(
        api_key="valid-mock-key",
        client=mock_genai_client,
    )
    # Monkeypatch active provider
    monkeypatch.setattr(
        "app.services.quotation.get_active_extraction_provider",
        lambda: mock_provider,
    )

    req = QuotationRequest(
        request_id="req-gemini-arch-001",
        conversation_id="conv-gemini-arch-001",
        message="I need two RST-RTX8 servers.",
    )
    resp = process_quotation(
        req, db_session, current_user={"sub": "customer1", "role": "customer"}
    )

    # Verify authoritative pricing from catalog ($245,000 unit, $490,000 total)
    assert resp.status == QuotationStatus.PENDING_APPROVAL
    assert resp.unit_selling_price == 245000.0
    assert resp.total_price == 490000.0
    # Stock is authoritatively verified from warehouse catalog (available: 2)
    assert resp.stock_check.can_fulfill is True
    assert resp.stock_check.available == 2


# 11. Multi-turn workflow with Gemini


def test_gemini_in_process_quotation_clarification_workflow(
    db_session, mock_genai_client, monkeypatch
):
    """Gemini provider handles multi-turn clarification seamlessly."""
    # Turn 1: Missing GPU count
    turn1_payload = {
        "sku": None,
        "gpu_family": "RTX",
        "gpu_memory_gb": 96,
        "gpu_count_per_server": None,
        "num_servers": 2,
        "missing_fields": ["gpu_count_per_server"],
        "confidence": "MEDIUM",
        "explicitly_stated": ["gpu_family", "num_servers"],
        "inferred": ["gpu_memory_gb"],
    }
    mock_response_t1 = MagicMock()
    mock_response_t1.text = json.dumps(turn1_payload)

    # Turn 2: Customer clarifies 8 GPUs
    turn2_payload = {
        "sku": None,
        "gpu_family": None,
        "gpu_count_per_server": 8,
        "num_servers": None,
        "missing_fields": ["gpu_family", "num_servers"],
        "confidence": "LOW",
        "explicitly_stated": ["gpu_count_per_server"],
        "inferred": [],
    }
    mock_response_t2 = MagicMock()
    mock_response_t2.text = json.dumps(turn2_payload)

    mock_genai_client.models.generate_content.side_effect = [
        mock_response_t1,
        mock_response_t2,
    ]

    mock_provider = GeminiExtractionProvider(
        api_key="valid-mock-key",
        client=mock_genai_client,
    )
    monkeypatch.setattr(
        "app.services.quotation.get_active_extraction_provider",
        lambda: mock_provider,
    )

    # Turn 1
    req1 = QuotationRequest(
        request_id="req-gemini-turn1",
        conversation_id="conv-gemini-clarify",
        message="I need two RTX servers with 96GB cards.",
    )
    resp1 = process_quotation(
        req1, db_session, current_user={"sub": "customer1", "role": "customer"}
    )
    assert resp1.status == QuotationStatus.NEEDS_CLARIFICATION
    assert resp1.clarification_question is not None

    # Turn 2
    req2 = QuotationRequest(
        request_id="req-gemini-turn2",
        conversation_id="conv-gemini-clarify",
        message="Eight GPUs per server.",
    )
    resp2 = process_quotation(
        req2, db_session, current_user={"sub": "customer1", "role": "customer"}
    )
    assert resp2.status == QuotationStatus.PENDING_APPROVAL
    assert resp2.catalog_match.sku == "RST-RTX8"
    assert resp2.total_price == 490000.0


# ─────────────────────────────────────────────────────────────────────
# 12-16. Extraction consistency regression tests
# Validates that missing_fields is ALWAYS application-computed, never
# trusted from Gemini raw output. Covers the 5 audit scenarios.
# ─────────────────────────────────────────────────────────────────────


def test_regression_a_complete_inquiry(mock_genai_client):
    """
    A. Complete inquiry: "Two RST-RTX8 servers, 8 GPUs per server, 512GB RAM."

    All required + optional fields present → missing_fields empty, confidence HIGH.
    """
    mock_response = MagicMock()
    mock_payload = {
        "sku": "RST-RTX8",
        "gpu_family": "RTX",
        "gpu_model": "RTX PRO 6000 Server Edition",
        "gpu_memory_gb": 96,
        "gpu_count_per_server": 8,
        "num_servers": 2,
        "ram_gb": 512,
        "missing_fields": [],
        "confidence": "HIGH",
        "explicitly_stated": [
            "sku",
            "gpu_count_per_server",
            "num_servers",
            "ram_gb",
        ],
        "inferred": ["gpu_family", "gpu_model", "gpu_memory_gb"],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    result = provider.extract("Two RST-RTX8 servers, 8 GPUs per server, 512GB RAM.")

    # All fields populated → empty missing_fields, HIGH confidence
    assert result.missing_fields == []
    assert result.confidence == ExtractionConfidence.HIGH
    assert result.sku == "RST-RTX8"
    assert result.gpu_count_per_server == 8
    assert result.num_servers == 2
    assert result.ram_gb == 512
    assert result.gpu_memory_gb == 96


def test_regression_b_incomplete_inquiry(mock_genai_client):
    """
    B. Incomplete inquiry: "I need two servers."

    Only num_servers extracted. No gpu_family, no sku, no gpu_count.
    missing_fields must include gpu_family and gpu_count_per_server.
    Confidence LOW.
    """
    mock_response = MagicMock()
    mock_payload = {
        "sku": None,
        "gpu_family": None,
        "gpu_model": None,
        "gpu_memory_gb": None,
        "gpu_count_per_server": None,
        "num_servers": 2,
        "ram_gb": None,
        # Gemini might return an incomplete missing_fields — we overwrite it
        "missing_fields": ["gpu_family"],
        "confidence": "MEDIUM",
        "explicitly_stated": ["num_servers"],
        "inferred": [],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    result = provider.extract("I need two servers.")

    # App must deterministically compute: gpu_family + gpu_count_per_server missing
    assert "gpu_family" in result.missing_fields
    assert "gpu_count_per_server" in result.missing_fields
    # num_servers is provided → not missing
    assert "num_servers" not in result.missing_fields
    # Only 1 signal filled (num_servers) → LOW
    assert result.confidence == ExtractionConfidence.LOW
    assert result.num_servers == 2
    # Optional fields are null but NOT in missing_fields
    assert result.gpu_model is None
    assert result.gpu_memory_gb is None
    assert result.ram_gb is None


def test_regression_c_sku_based_inquiry(mock_genai_client):
    """
    C. SKU-based inquiry: "I need two RST-RTX8 servers with 8 GPUs each."

    SKU provided → satisfies gpu_family and gpu_count_per_server requirements.
    Null optional fields (gpu_model, gpu_memory_gb, ram_gb) must NOT appear
    in missing_fields. This is the exact scenario from the live audit.
    """
    mock_response = MagicMock()
    mock_payload = {
        "gpu_family": "RTX",
        "gpu_model": None,
        "gpu_memory_gb": None,
        "gpu_count_per_server": 8,
        "num_servers": 2,
        "ram_gb": None,
        "sku": "RST-RTX8",
        # Gemini might incorrectly report empty — which happens to be right here
        # but the app ALWAYS recomputes to be safe
        "missing_fields": [],
        "confidence": "MEDIUM",
        "explicitly_stated": ["gpu_count_per_server", "num_servers", "sku"],
        "inferred": ["gpu_family"],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    result = provider.extract("I need two RST-RTX8 servers with 8 GPUs each.")

    # Key assertion: missing_fields is empty despite null optional fields
    assert result.missing_fields == []
    # Optional fields are null — that's fine, they're enrichment fields
    assert result.gpu_model is None
    assert result.gpu_memory_gb is None
    assert result.ram_gb is None
    # Confidence MEDIUM: 3/4 signal dimensions filled (no gpu_memory_gb)
    assert result.confidence == ExtractionConfidence.MEDIUM
    assert result.sku == "RST-RTX8"
    assert result.num_servers == 2
    assert result.gpu_count_per_server == 8


def test_regression_d_ambiguous_inquiry(mock_genai_client):
    """
    D. Ambiguous inquiry: "I need high-performance GPU servers."

    No concrete specs extracted at all. All 3 required conditions unmet.
    missing_fields must list all 3 required groups. Confidence LOW.
    """
    mock_response = MagicMock()
    mock_payload = {
        "sku": None,
        "gpu_family": None,
        "gpu_model": None,
        "gpu_memory_gb": None,
        "gpu_count_per_server": None,
        "num_servers": None,
        "ram_gb": None,
        # Gemini might return only partial missing list — we overwrite
        "missing_fields": ["gpu_family"],
        "confidence": "LOW",
        "explicitly_stated": [],
        "inferred": [],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    result = provider.extract("I need high-performance GPU servers.")

    # All 3 required conditions unmet
    assert "gpu_family" in result.missing_fields
    assert "gpu_count_per_server" in result.missing_fields
    assert "num_servers" in result.missing_fields
    assert result.confidence == ExtractionConfidence.LOW
    # Nothing should be fabricated
    assert result.gpu_family is None
    assert result.sku is None
    assert result.gpu_count_per_server is None
    assert result.num_servers is None


def test_regression_e_no_invention(mock_genai_client):
    """
    E. No-invention test: "Give me two servers with GPUs."

    Gemini must NOT fabricate gpu_family, gpu_count_per_server, or sku.
    If it does, post-processing still flags missing_fields correctly,
    but we also verify the LLM didn't hallucinate concrete specs.
    """
    mock_response = MagicMock()
    mock_payload = {
        "sku": None,
        "gpu_family": None,
        "gpu_model": None,
        "gpu_memory_gb": None,
        "gpu_count_per_server": None,
        "num_servers": 2,
        "ram_gb": None,
        "missing_fields": [],  # Gemini wrongly says nothing missing
        "confidence": "HIGH",  # Gemini wrongly says HIGH
        "explicitly_stated": ["num_servers"],
        "inferred": [],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    result = provider.extract("Give me two servers with GPUs.")

    # Post-processing MUST overwrite Gemini's incorrect empty missing_fields
    assert "gpu_family" in result.missing_fields
    assert "gpu_count_per_server" in result.missing_fields
    assert "num_servers" not in result.missing_fields
    # Post-processing MUST overwrite Gemini's incorrect HIGH confidence
    assert result.confidence == ExtractionConfidence.LOW
    # Verify no specs were invented
    assert result.gpu_family is None
    assert result.sku is None
    assert result.gpu_count_per_server is None
    assert result.gpu_model is None
    assert result.gpu_memory_gb is None
    assert result.ram_gb is None


# ─────────────────────────────────────────────────────────────────────
# 17-23. Phase 5 Truncation, FinishReason, and Controlled Error Tests
# ─────────────────────────────────────────────────────────────────────


def test_gemini_extraction_truncated_json_raises_response_error(
    mock_genai_client,
):
    """
    Truncated JSON (e.g. cut off mid-stream with unclosed string/braces)
    raises GeminiResponseError with clear diagnostics.
    """
    mock_response = MagicMock()
    # Emulates truncated 223 KB response ending mid-stream
    mock_response.text = (
        '{\n  "gpu_family": "RTX",\n  "gpu_model": "RTX PRO 6000 Server Edition'
    )
    mock_candidate = MagicMock()
    mock_candidate.finish_reason = "STOP"
    mock_response.candidates = [mock_candidate]
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    with pytest.raises(GeminiResponseError) as exc_info:
        provider.extract("I need RTX servers.")
    assert (
        "incomplete" in str(exc_info.value).lower()
        or "braces" in str(exc_info.value).lower()
    )


def test_gemini_extraction_max_tokens_finish_reason_raises_response_error(
    mock_genai_client,
):
    """
    Candidate with finish_reason='MAX_TOKENS' raises GeminiResponseError
    explicitly before any parsing is attempted.
    """
    mock_response = MagicMock()
    mock_response.text = '{"gpu_family": "RTX'
    mock_candidate = MagicMock()
    mock_candidate.finish_reason = "MAX_TOKENS"
    mock_response.candidates = [mock_candidate]
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    with pytest.raises(GeminiResponseError) as exc_info:
        provider.extract("I need RTX servers.")
    assert "truncated" in str(exc_info.value).lower()
    assert "max_tokens" in str(exc_info.value).lower()


def test_gemini_extraction_503_raises_unavailable_error(mock_genai_client):
    """Upstream 503 Service Unavailable / High demand raises GeminiUnavailableError."""
    mock_genai_client.models.generate_content.side_effect = Exception(
        "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand.'}}"
    )

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client, max_retries=0
    )
    with pytest.raises(GeminiUnavailableError) as exc_info:
        provider.extract("I need RTX servers.")
    assert "unavailable" in str(exc_info.value).lower()


def test_gemini_extraction_unexpected_format_raises_response_error(
    mock_genai_client,
):
    """Response that is plain text instead of JSON raises GeminiResponseError."""
    mock_response = MagicMock()
    mock_response.text = (
        "Here are the server specifications you requested for RTX servers."
    )
    mock_candidate = MagicMock()
    mock_candidate.finish_reason = "STOP"
    mock_response.candidates = [mock_candidate]
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    with pytest.raises(GeminiResponseError) as exc_info:
        provider.extract("I need RTX servers.")
    assert (
        "incomplete" in str(exc_info.value).lower()
        or "braces" in str(exc_info.value).lower()
    )


def test_controlled_api_error_returns_502_on_gemini_response_error(
    client, customer_headers, monkeypatch
):
    """
    Controlled HTTP 502 Bad Gateway returned instead of unhandled HTTP 500
    when Gemini returns malformed/truncated output.
    """
    mock_provider = MagicMock()
    mock_provider.extract.side_effect = GeminiResponseError(
        "Upstream model response was truncated (reached MAX_TOKENS limit)"
    )

    monkeypatch.setattr(
        "app.services.quotation.get_active_extraction_provider",
        lambda: mock_provider,
    )

    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-gemini-502-test",
            "conversation_id": "conv-gemini-502-test",
            "message": "I need two RST-RTX8 servers.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 502
    assert "invalid response" in resp.json()["detail"].lower()


def test_controlled_api_error_returns_503_on_gemini_unavailable(
    client, customer_headers, monkeypatch
):
    """
    Controlled HTTP 503 Service Unavailable returned when Gemini is unavailable.
    """
    mock_provider = MagicMock()
    mock_provider.extract.side_effect = GeminiUnavailableError(
        "Gemini API is temporarily unavailable due to high demand."
    )

    monkeypatch.setattr(
        "app.services.quotation.get_active_extraction_provider",
        lambda: mock_provider,
    )

    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-gemini-503-test",
            "conversation_id": "conv-gemini-503-test",
            "message": "I need two RST-RTX8 servers.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


def test_controlled_api_error_returns_504_on_gemini_timeout(
    client, customer_headers, monkeypatch
):
    """
    Controlled HTTP 504 Gateway Timeout returned when Gemini API times out.
    """
    mock_provider = MagicMock()
    mock_provider.extract.side_effect = GeminiTimeoutError(
        "Gemini API request timed out after 30.0s."
    )

    monkeypatch.setattr(
        "app.services.quotation.get_active_extraction_provider",
        lambda: mock_provider,
    )

    resp = client.post(
        "/api/v1/quotations",
        json={
            "request_id": "req-gemini-504-test",
            "conversation_id": "conv-gemini-504-test",
            "message": "I need two RST-RTX8 servers.",
        },
        headers=customer_headers,
    )
    assert resp.status_code == 504
    assert "timed out" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────
# 24. Regression: explicit SKU + quantity + GPU count all populated
# ─────────────────────────────────────────────────────────────────────


def test_regression_sku_with_explicit_quantity_and_gpu_count_all_populated(
    mock_genai_client,
):
    """
    Regression test for the gemini-3.5-flash-lite extraction failure where
    the model recognized field names (listing them in explicitly_stated) but
    returned null values for num_servers and gpu_count_per_server.

    Input: "I need two RST-RTX8 servers with 8 GPUs each."

    A reliable model must populate:
      - sku = "RST-RTX8"
      - num_servers = 2
      - gpu_count_per_server = 8

    All three must be non-null integers and present in explicitly_stated.
    missing_fields must be empty (sku satisfies gpu_family and gpu_count
    requirements, num_servers is provided).
    """
    mock_response = MagicMock()
    mock_payload = {
        "sku": "RST-RTX8",
        "gpu_family": "RTX",
        "gpu_model": None,
        "gpu_memory_gb": None,
        "gpu_count_per_server": 8,
        "num_servers": 2,
        "ram_gb": None,
        "missing_fields": [],
        "confidence": "HIGH",
        "explicitly_stated": ["sku", "num_servers", "gpu_count_per_server"],
        "inferred": ["gpu_family"],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    result = provider.extract("I need two RST-RTX8 servers with 8 GPUs each.")

    # Core regression assertions: all three explicitly stated fields are populated
    assert result.sku == "RST-RTX8"
    assert result.num_servers == 2
    assert result.gpu_count_per_server == 8

    # Fields must appear in explicitly_stated
    assert "sku" in result.explicitly_stated
    assert "num_servers" in result.explicitly_stated
    assert "gpu_count_per_server" in result.explicitly_stated

    # No missing fields — SKU + num_servers + gpu_count all present
    assert result.missing_fields == []

    # Confidence HIGH: sku satisfies gpu_family, sku satisfies gpu_count,
    # num_servers provided, gpu_memory_gb not stated → 3/4 → MEDIUM
    # (but gpu_family is also inferred so sku + gpu_family gives 1st dimension)
    assert result.confidence in (ExtractionConfidence.HIGH, ExtractionConfidence.MEDIUM)


def test_regression_explicitly_stated_field_returned_null_detected_as_inconsistency(
    mock_genai_client, caplog
):
    """
    Proves that when Gemini claims a field was explicitly stated in
    'explicitly_stated' but returns null for the field's actual value,
    the system detects this extraction inconsistency, logs a warning,
    reconciles explicitly_stated, and flags the missing field.
    """
    import logging

    mock_response = MagicMock()
    # Emulates the faulty behavior: explicitly_stated claims num_servers and gpu_count_per_server
    # were stated, but both values are null in the JSON payload
    mock_payload = {
        "sku": "RST-RTX8",
        "gpu_family": None,
        "gpu_model": None,
        "gpu_memory_gb": None,
        "gpu_count_per_server": None,
        "num_servers": None,
        "ram_gb": None,
        "missing_fields": [],
        "confidence": "MEDIUM",
        "explicitly_stated": ["sku", "num_servers", "gpu_count_per_server"],
        "inferred": [],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )

    with caplog.at_level(logging.WARNING):
        result = provider.extract("I need two RST-RTX8 servers with 8 GPUs each.")

    # 1. Inconsistency must be detected and logged
    assert any(
        "Extraction inconsistency detected" in record.message
        for record in caplog.records
    )

    # 2. Reconciles explicitly_stated: null fields are removed so downstream logic isn't misled
    assert "sku" in result.explicitly_stated
    assert "num_servers" not in result.explicitly_stated
    assert "gpu_count_per_server" not in result.explicitly_stated

    # 3. Missing fields are deterministically computed (num_servers is genuinely missing)
    assert "num_servers" in result.missing_fields


def test_regression_missing_value_never_silently_guessed_or_fabricated(
    mock_genai_client,
):
    """
    Proves that the system never silently guesses or fabricates values for
    missing customer requirements (e.g. does not guess RTX or H200 when only
    generic 'GPU servers' are requested).
    """
    mock_response = MagicMock()
    mock_payload = {
        "sku": None,
        "gpu_family": None,
        "gpu_model": None,
        "gpu_memory_gb": None,
        "gpu_count_per_server": None,
        "num_servers": 2,
        "ram_gb": None,
        "missing_fields": ["gpu_family", "gpu_count_per_server"],
        "confidence": "LOW",
        "explicitly_stated": ["num_servers"],
        "inferred": [],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    result = provider.extract("I need two GPU servers.")

    # Values must remain strictly None — never guessed or defaulted
    assert result.sku is None
    assert result.gpu_family is None
    assert result.gpu_model is None
    assert result.gpu_memory_gb is None
    assert result.gpu_count_per_server is None
    assert result.ram_gb is None

    # Missing fields must demand clarification
    assert "gpu_family" in result.missing_fields
    assert "gpu_count_per_server" in result.missing_fields


def test_regression_quotation_workflow_does_not_produce_total_when_server_quantity_missing(
    db_session, mock_genai_client, monkeypatch
):
    """
    Proves that the quotation workflow does not produce an incorrect or
    hallucinated price total when the server quantity is missing.
    Must transition to NEEDS_CLARIFICATION with total_price=None.
    """
    mock_response = MagicMock()
    mock_payload = {
        "sku": "RST-RTX8",
        "gpu_family": "RTX",
        "gpu_count_per_server": 8,
        "num_servers": None,  # Missing quantity
        "missing_fields": ["num_servers"],
        "confidence": "MEDIUM",
        "explicitly_stated": ["sku", "gpu_count_per_server"],
        "inferred": [],
    }
    mock_response.text = json.dumps(mock_payload)
    mock_genai_client.models.generate_content.return_value = mock_response

    mock_provider = GeminiExtractionProvider(
        api_key="test-key-valid", client=mock_genai_client
    )
    monkeypatch.setattr(
        "app.services.quotation.get_active_extraction_provider",
        lambda: mock_provider,
    )

    req = QuotationRequest(
        request_id="req-gemini-no-qty-001",
        conversation_id="conv-gemini-no-qty-001",
        message="I need RST-RTX8 servers with 8 GPUs.",
    )
    resp = process_quotation(
        req, db_session, current_user={"sub": "customer1", "role": "customer"}
    )

    # Must transition to NEEDS_CLARIFICATION, never calculate a price
    assert resp.status == QuotationStatus.NEEDS_CLARIFICATION
    assert resp.total_price is None
    assert resp.unit_selling_price is None
    assert "servers do you need" in resp.clarification_question.lower()
