"""
Regression tests for the six targeted engineering corrections.

Task 1 — Catalogue contradiction validation
Task 2 — Numeric validation for num_servers / gpu_count_per_server
Task 3 — Gemini timeout configuration reaches the HTTP client
Task 4 — Production authentication safeguards
Task 5 — Gemini model name consistency (config → provider default)

All Gemini API calls are strictly mocked. No network traffic is produced.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.main import app, lifespan
from app.models.schemas import ExtractionResult, QuotationRequest, QuotationStatus
from app.security.authentication import (
    _DEFAULT_DEV_SECRET,
    authenticate_dev_user,
    is_production,
    validate_production_configuration,
)
from app.services.catalog import match_catalog
from app.services.gemini_provider import (
    GeminiExtractionProvider,
    GeminiProviderError,
)
from app.services.quotation import (
    _merge_extractions,
    _recompute_missing_fields,
    process_quotation,
)

# ─────────────────────────────────────────────────────────────────────────────
# TASK 1 — Catalogue contradiction validation
# ─────────────────────────────────────────────────────────────────────────────


def _make_extraction(**kwargs) -> ExtractionResult:
    """Helper: build an ExtractionResult with explicit fields."""
    return ExtractionResult(**kwargs)


def test_sku_with_contradictory_ram_returns_unmatched():
    """
    RST-RTX8 has 512 GB RAM.
    If the customer explicitly states 1024 GB RAM, the match must be rejected
    rather than silently returning the wrong configuration.
    """
    extraction = _make_extraction(
        sku="RST-RTX8",
        num_servers=1,
        gpu_count_per_server=8,
        ram_gb=1024,  # contradicts catalog (512 GB)
        explicitly_stated=["sku", "num_servers", "gpu_count_per_server", "ram_gb"],
    )
    result = match_catalog(extraction)
    assert result.matched is False
    assert "RAM" in result.match_notes or "contradict" in result.match_notes.lower()


def test_sku_with_contradictory_gpu_count_returns_unmatched():
    """
    RST-RTX8 has 8 GPUs per server.
    If the customer explicitly states 4 GPUs, the match must be rejected.
    """
    extraction = _make_extraction(
        sku="RST-RTX8",
        num_servers=1,
        gpu_count_per_server=4,  # contradicts catalog (8)
        explicitly_stated=["sku", "num_servers", "gpu_count_per_server"],
    )
    result = match_catalog(extraction)
    assert result.matched is False
    assert (
        "GPU count" in result.match_notes or "contradict" in result.match_notes.lower()
    )


def test_sku_with_contradictory_gpu_model_returns_unmatched():
    """
    RST-RTX8 uses RTX PRO 6000 Server Edition.
    If the customer specifies an explicit SKU but a contradictory model,
    it must be rejected.
    """
    extraction = _make_extraction(
        sku="RST-RTX8",
        num_servers=1,
        gpu_model="H200 NVL",  # contradicts catalog
        explicitly_stated=["sku", "num_servers", "gpu_model"],
    )
    result = match_catalog(extraction)
    assert result.matched is False
    assert (
        "GPU model" in result.match_notes or "contradict" in result.match_notes.lower()
    )


@pytest.mark.parametrize(
    "sku,valid_model",
    [
        ("RST-RTX8", "RTX PRO 6000 Server Edition"),
        ("RST-RTX8", "RTX PRO 6000"),
        ("RST-RTX8", "RTX 6000 SERVER EDITION"),
        ("RST-RTX8", "RTX 6000 SERVER"),
        ("RST-RTX8", "PRO 6000"),
        ("RST-H2004", "H200 NVL"),
        ("RST-H2004", "H200"),
        ("RST-B3008", "HGX B300"),
        ("RST-B3008", "B300"),
    ],
)
def test_catalogue_gpu_model_valid_aliases_succeed(sku, valid_model):
    """Positive test: authentic catalogue models and unambiguous aliases match."""
    extraction = _make_extraction(
        sku=sku,
        num_servers=1,
        gpu_model=valid_model,
        explicitly_stated=["sku", "num_servers", "gpu_model"],
    )
    result = match_catalog(extraction)
    assert result.matched is True
    assert result.sku == sku


@pytest.mark.parametrize(
    "sku,contradictory_model",
    [
        ("RST-RTX8", "RTX 4090"),
        ("RST-RTX8", "RTX 3090"),
        # "RTX 6000" alone is ambiguous with Quadro RTX 6000 and RTX 6000 Ada workstation cards
        ("RST-RTX8", "RTX 6000"),
        ("RST-H2004", "H100 NVL"),  # contains NVL but is different generation
        ("RST-H2004", "H100"),
        ("RST-B3008", "HGX B200"),  # contains HGX but is different generation
        ("RST-B3008", "B200"),
        ("RST-B3008", "B100"),
    ],
)
def test_catalogue_gpu_model_contradictions_rejected(sku, contradictory_model):
    """Negative test: different models sharing prefixes/substrings are rejected as contradictions."""
    extraction = _make_extraction(
        sku=sku,
        num_servers=1,
        gpu_model=contradictory_model,
        explicitly_stated=["sku", "num_servers", "gpu_model"],
    )
    result = match_catalog(extraction)
    assert result.matched is False
    assert (
        "GPU model" in result.match_notes or "contradict" in result.match_notes.lower()
    )


def test_config_match_with_contradictory_ram_returns_unmatched():
    """
    Configuration matching matches RST-RTX8 by family/memory/count, but the customer
    explicitly requests 1024 GB RAM (catalog has 512 GB).
    Must return unmatched.
    """
    extraction = _make_extraction(
        gpu_family="RTX",
        gpu_memory_gb=96,
        gpu_count_per_server=8,
        num_servers=1,
        ram_gb=1024,  # contradicts RST-RTX8 (512 GB)
        explicitly_stated=[
            "gpu_family",
            "gpu_memory_gb",
            "gpu_count_per_server",
            "num_servers",
            "ram_gb",
        ],
    )
    result = match_catalog(extraction)
    assert result.matched is False
    assert "RAM" in result.match_notes or "contradict" in result.match_notes.lower()


def test_config_match_with_contradictory_gpu_model_returns_unmatched():
    """
    Configuration matching matches RST-RTX8 by family/memory/count, but customer
    explicitly specifies 'RTX 4090'.
    Must return unmatched.
    """
    extraction = _make_extraction(
        gpu_family="RTX",
        gpu_memory_gb=96,
        gpu_count_per_server=8,
        num_servers=1,
        gpu_model="RTX 4090",  # contradicts RST-RTX8
        explicitly_stated=[
            "gpu_family",
            "gpu_memory_gb",
            "gpu_count_per_server",
            "num_servers",
            "gpu_model",
        ],
    )
    result = match_catalog(extraction)
    assert result.matched is False
    assert (
        "GPU model" in result.match_notes or "contradict" in result.match_notes.lower()
    )


def test_config_match_with_matching_specs_succeeds():
    """
    Configuration matching with consistent explicit specs matches cleanly.
    """
    extraction = _make_extraction(
        gpu_family="RTX",
        gpu_memory_gb=96,
        gpu_count_per_server=8,
        num_servers=1,
        gpu_model="RTX PRO 6000 Server Edition",
        ram_gb=512,
        explicitly_stated=[
            "gpu_family",
            "gpu_memory_gb",
            "gpu_count_per_server",
            "num_servers",
            "gpu_model",
            "ram_gb",
        ],
    )
    result = match_catalog(extraction)
    assert result.matched is True
    assert result.sku == "RST-RTX8"


def test_inferred_or_omitted_specs_do_not_contradict():
    """
    An inferred or omitted spec should not be treated as a customer contradiction.
    Only explicitly stated specs are checked.
    """
    extraction = _make_extraction(
        gpu_family="RTX",
        gpu_memory_gb=96,
        gpu_count_per_server=8,
        num_servers=1,
        gpu_model="Inferred Non-catalog Label",
        inferred=["gpu_model"],
        explicitly_stated=[
            "gpu_family",
            "gpu_memory_gb",
            "gpu_count_per_server",
            "num_servers",
        ],
    )
    result = match_catalog(extraction)
    assert result.matched is True
    assert result.sku == "RST-RTX8"


def test_sku_with_valid_matching_specs_succeeds():
    """
    RST-RTX8 has 8 GPUs and 512 GB RAM.
    Explicitly stating matching values must not block the match.
    """
    extraction = _make_extraction(
        sku="RST-RTX8",
        num_servers=2,
        gpu_count_per_server=8,  # matches catalog
        ram_gb=512,  # matches catalog
        explicitly_stated=["sku", "num_servers", "gpu_count_per_server", "ram_gb"],
    )
    result = match_catalog(extraction)
    assert result.matched is True
    assert result.sku == "RST-RTX8"


def test_sku_with_uncontradicted_null_specs_succeeds():
    """
    When a customer provides only an SKU and num_servers (no other explicit specs),
    the match must succeed even though gpu_count, ram_gb etc. are null — those are
    not contradictions, merely omissions.
    """
    extraction = _make_extraction(
        sku="RST-RTX8",
        num_servers=1,
        explicitly_stated=["sku", "num_servers"],
    )
    result = match_catalog(extraction)
    assert result.matched is True
    assert result.sku == "RST-RTX8"


def test_sku_match_through_quotation_pipeline_with_contradiction(db_session):
    """
    End-to-end: a contradictory RAM value flows through process_quotation and
    results in PENDING_SALES_REVIEW (catalog unmatched) rather than a quotation
    at the wrong price.
    """
    # Build a mock provider that returns RST-RTX8 with contradictory RAM
    mock_provider = MagicMock()
    mock_provider.extract.return_value = ExtractionResult(
        sku="RST-RTX8",
        gpu_family="RTX",
        gpu_count_per_server=8,
        num_servers=1,
        ram_gb=1024,  # contradicts catalog
        missing_fields=[],
        explicitly_stated=["sku", "gpu_count_per_server", "num_servers", "ram_gb"],
    )

    with patch(
        "app.services.quotation.get_active_extraction_provider",
        return_value=mock_provider,
    ):
        req = QuotationRequest(
            request_id="req-sku-contra-001",
            conversation_id="conv-sku-contra-001",
            message="I need 1 RST-RTX8 with 1024 GB RAM.",
        )
        resp = process_quotation(
            req, db_session, current_user={"sub": "customer1", "role": "customer"}
        )

    # Contradictory spec → no match → PENDING_SALES_REVIEW, not PENDING_APPROVAL
    assert resp.status == QuotationStatus.PENDING_SALES_REVIEW
    assert resp.total_price is None


# ─────────────────────────────────────────────────────────────────────────────
# TASK 2 — Numeric validation
# ─────────────────────────────────────────────────────────────────────────────


def test_negative_num_servers_coerced_to_none():
    """Negative num_servers must be silently coerced to None by the validator."""
    extraction = ExtractionResult(num_servers=-3)
    assert extraction.num_servers is None


def test_zero_num_servers_coerced_to_none():
    """Zero num_servers must be silently coerced to None by the validator."""
    extraction = ExtractionResult(num_servers=0)
    assert extraction.num_servers is None


def test_negative_gpu_count_coerced_to_none():
    """Negative gpu_count_per_server must be coerced to None."""
    extraction = ExtractionResult(gpu_count_per_server=-1)
    assert extraction.gpu_count_per_server is None


def test_zero_gpu_count_coerced_to_none():
    """Zero gpu_count_per_server must be coerced to None."""
    extraction = ExtractionResult(gpu_count_per_server=0)
    assert extraction.gpu_count_per_server is None


def test_excessive_num_servers_coerced_to_none():
    """num_servers beyond the upper limit must be coerced to None."""
    extraction = ExtractionResult(num_servers=99999)
    assert extraction.num_servers is None


def test_excessive_gpu_count_coerced_to_none():
    """gpu_count_per_server beyond the upper limit must be coerced to None."""
    extraction = ExtractionResult(gpu_count_per_server=9999)
    assert extraction.gpu_count_per_server is None


def test_valid_positive_values_are_preserved():
    """Valid positive values must pass through the validators unchanged."""
    extraction = ExtractionResult(num_servers=5, gpu_count_per_server=8)
    assert extraction.num_servers == 5
    assert extraction.gpu_count_per_server == 8


def test_numeric_validators_reject_booleans():
    """Booleans must not be accepted as numbers in num_servers or gpu_count."""
    assert ExtractionResult(num_servers=True).num_servers is None
    assert ExtractionResult(num_servers=False).num_servers is None
    assert ExtractionResult(gpu_count_per_server=True).gpu_count_per_server is None
    assert ExtractionResult(gpu_count_per_server=False).gpu_count_per_server is None


def test_numeric_validators_reject_non_integral_floats():
    """Non-integral floating point numbers must be rejected rather than truncated."""
    assert ExtractionResult(num_servers=2.5).num_servers is None
    assert ExtractionResult(num_servers="2.5").num_servers is None
    assert ExtractionResult(num_servers=0.9).num_servers is None
    assert ExtractionResult(gpu_count_per_server=4.1).gpu_count_per_server is None
    assert ExtractionResult(gpu_count_per_server="4.1").gpu_count_per_server is None


def test_numeric_validators_accept_valid_integers_and_integral_representations():
    """Valid positive integers and clean whole-number representations are accepted."""
    assert ExtractionResult(num_servers=2).num_servers == 2
    assert ExtractionResult(num_servers=2.0).num_servers == 2
    assert ExtractionResult(num_servers="2").num_servers == 2
    assert ExtractionResult(num_servers="2.0").num_servers == 2
    assert ExtractionResult(gpu_count_per_server=8).gpu_count_per_server == 8
    assert ExtractionResult(gpu_count_per_server=8.0).gpu_count_per_server == 8
    assert ExtractionResult(gpu_count_per_server="8").gpu_count_per_server == 8
    assert ExtractionResult(gpu_count_per_server="8.0").gpu_count_per_server == 8


def test_numeric_validators_handle_decimal_types():
    """Decimal values: whole Decimals are accepted; non-integral or negative are rejected."""
    # Non-integral Decimals must be rejected
    assert ExtractionResult(num_servers=Decimal("2.5")).num_servers is None
    assert (
        ExtractionResult(gpu_count_per_server=Decimal("2.5")).gpu_count_per_server
        is None
    )
    assert ExtractionResult(num_servers=Decimal("0.5")).num_servers is None

    # Whole positive Decimals must be accepted
    assert ExtractionResult(num_servers=Decimal("2")).num_servers == 2
    assert ExtractionResult(num_servers=Decimal("2.0")).num_servers == 2
    assert ExtractionResult(gpu_count_per_server=Decimal("8")).gpu_count_per_server == 8
    assert (
        ExtractionResult(gpu_count_per_server=Decimal("8.0")).gpu_count_per_server == 8
    )

    # Negative Decimals must be rejected
    assert ExtractionResult(num_servers=Decimal("-2")).num_servers is None


def test_numeric_validators_reject_unsupported_types():
    """Unsupported data structures (lists, dicts, objects) must be rejected rather than coerced."""
    assert ExtractionResult(num_servers=[2]).num_servers is None
    assert ExtractionResult(num_servers={"servers": 2}).num_servers is None
    assert ExtractionResult(num_servers=object()).num_servers is None
    assert ExtractionResult(gpu_count_per_server=[8]).gpu_count_per_server is None
    assert (
        ExtractionResult(gpu_count_per_server={"gpus": 8}).gpu_count_per_server is None
    )
    assert ExtractionResult(gpu_count_per_server=object()).gpu_count_per_server is None


def test_invalid_quantities_do_not_reach_price_calculations(db_session):
    """
    An extracted negative num_servers must trigger NEEDS_CLARIFICATION (because
    the application sees num_servers=None → missing) rather than a negative or
    nonsensical quotation total.
    """
    mock_provider = MagicMock()
    # LLM hallucination: extracted num_servers=-1
    mock_provider.extract.return_value = ExtractionResult(
        sku="RST-RTX8",
        gpu_count_per_server=8,
        num_servers=-1,  # validator will coerce to None
        missing_fields=[],
        explicitly_stated=["sku", "gpu_count_per_server", "num_servers"],
    )

    with patch(
        "app.services.quotation.get_active_extraction_provider",
        return_value=mock_provider,
    ):
        req = QuotationRequest(
            request_id="req-neg-servers-001",
            conversation_id="conv-neg-servers-001",
            message="I need -1 RST-RTX8 servers.",
        )
        resp = process_quotation(
            req, db_session, current_user={"sub": "customer1", "role": "customer"}
        )

    # num_servers coerced to None → missing → clarification
    assert resp.status == QuotationStatus.NEEDS_CLARIFICATION
    # Must not have produced a price
    assert resp.total_price is None
    assert resp.unit_selling_price is None


# ─────────────────────────────────────────────────────────────────────────────
# TASK 3 — Gemini timeout configuration reaches the HTTP client
# ─────────────────────────────────────────────────────────────────────────────


def test_gemini_provider_stores_configured_timeout():
    """
    The timeout supplied to GeminiExtractionProvider must be stored on the
    instance so it is available for exception classification and logging.
    """
    mock_client = MagicMock()
    provider = GeminiExtractionProvider(
        api_key="valid-test-key",
        timeout=15.0,
        client=mock_client,
    )
    assert provider.timeout == 15.0


def test_gemini_provider_timeout_propagated_to_client_construction(monkeypatch):
    """
    When no pre-built client is injected, the GeminiExtractionProvider must
    attempt to pass the configured timeout to the genai.Client constructor via
    HttpOptions. We patch genai.Client and types.HttpOptions to verify the call.
    """
    captured_http_options = {}

    class FakeHttpOptions:
        def __init__(self, timeout):
            captured_http_options["timeout"] = timeout

    class FakeClient:
        def __init__(self, api_key, http_options=None):
            captured_http_options["constructed"] = True
            captured_http_options["http_options_passed"] = http_options is not None

    import app.services.gemini_provider as gp_module

    monkeypatch.setattr(gp_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gp_module.types, "HttpOptions", FakeHttpOptions)

    GeminiExtractionProvider(api_key="valid-test-key", timeout=20.0)

    assert captured_http_options.get("constructed") is True
    assert captured_http_options.get("http_options_passed") is True
    # timeout is stored in ms (20s → 20000 ms)
    assert captured_http_options.get("timeout") == 20000


def test_gemini_provider_default_timeout_from_settings():
    """
    When no explicit timeout argument is supplied, the provider must use the
    value from application settings (gemini_timeout_seconds).
    """
    mock_client = MagicMock()
    provider = GeminiExtractionProvider(
        api_key="valid-test-key",
        client=mock_client,
    )
    assert provider.timeout == settings.gemini_timeout_seconds


# ─────────────────────────────────────────────────────────────────────────────
# TASK 4 — Production authentication safeguards
# ─────────────────────────────────────────────────────────────────────────────


def test_dev_login_works_in_development(client):
    """
    The development /auth/token endpoint must continue to work normally in the
    default development environment.
    """
    resp = client.post(
        "/auth/token",
        json={"username": "customer1", "password": "customerpass"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "customer"
    assert "access_token" in data


def test_dev_login_blocked_in_production(client, monkeypatch):
    """
    The development /auth/token endpoint must return 403 when
    ENVIRONMENT=production, preventing hardcoded credentials from being used.
    """
    monkeypatch.setattr(settings, "environment", "production")
    resp = client.post(
        "/auth/token",
        json={"username": "customer1", "password": "customerpass"},
    )
    assert resp.status_code == 403
    assert "production" in resp.json()["detail"].lower()


def test_authenticate_dev_user_raises_403_in_production(monkeypatch):
    """
    authenticate_dev_user() must raise HTTPException(403) when
    ENVIRONMENT=production, regardless of credentials supplied.
    """
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(HTTPException) as exc_info:
        authenticate_dev_user("customer1", "customerpass")
    assert exc_info.value.status_code == 403


def test_authenticate_dev_user_raises_401_for_wrong_password(monkeypatch):
    """
    authenticate_dev_user() must raise HTTPException(401) for wrong credentials
    in development.
    """
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "environment", "development")
    with pytest.raises(HTTPException) as exc_info:
        authenticate_dev_user("customer1", "wrongpassword")
    assert exc_info.value.status_code == 401


def test_validate_production_configuration_raises_with_default_secret_in_production():
    """validate_production_configuration raises RuntimeError when in production with default secret."""
    with pytest.raises(RuntimeError) as exc_info:
        validate_production_configuration(
            environment="production", secret_key=_DEFAULT_DEV_SECRET
        )
    assert "JWT_SECRET_KEY is still set to the default development value" in str(
        exc_info.value
    )


def test_validate_production_configuration_passes_with_custom_secret_in_production():
    """validate_production_configuration passes when running in production with a custom secret."""
    # Should not raise any error
    validate_production_configuration(
        environment="production", secret_key="custom-secure-secret-key-12345"
    )


def test_validate_production_configuration_allows_default_secret_in_development():
    """validate_production_configuration allows the default secret in development environment."""
    # Should not raise any error
    validate_production_configuration(
        environment="development", secret_key=_DEFAULT_DEV_SECRET
    )


@pytest.mark.asyncio
async def test_application_lifespan_aborts_in_production_with_default_secret(
    monkeypatch,
):
    """The real application lifespan context manager aborts startup in production with default secret."""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", _DEFAULT_DEV_SECRET)
    with pytest.raises(RuntimeError) as exc_info:
        async with lifespan(app):
            pass
    assert "JWT_SECRET_KEY is still set to the default development value" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_application_lifespan_starts_in_production_with_custom_secret(
    monkeypatch,
):
    """The real application lifespan starts cleanly in production with a custom secret."""
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "jwt_secret_key", "strong-production-key-987654321")
    # conftest autouse sets llm_provider to mock, so database initializes without network calls
    async with lifespan(app):
        pass


@pytest.mark.asyncio
async def test_application_lifespan_starts_in_development_with_default_secret(
    monkeypatch,
):
    """The real application lifespan starts cleanly in development with default secret."""
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "jwt_secret_key", _DEFAULT_DEV_SECRET)
    async with lifespan(app):
        pass


def test_is_production_returns_false_in_development(monkeypatch):
    """is_production() helper returns False in development environment."""
    monkeypatch.setattr(settings, "environment", "development")
    assert is_production() is False


def test_is_production_returns_true_in_production(monkeypatch):
    """is_production() helper returns True in production environment."""
    monkeypatch.setattr(settings, "environment", "production")
    assert is_production() is True


# ─────────────────────────────────────────────────────────────────────────────
# TASK 5 — Gemini model name consistency
# ─────────────────────────────────────────────────────────────────────────────


def test_config_default_model_is_gemini_3_1_flash_lite():
    """settings.gemini_model must default to gemini-3.1-flash-lite."""
    # Reset to default by checking what pydantic gives us without env override
    assert settings.gemini_model == "gemini-3.1-flash-lite"


def test_gemini_provider_uses_settings_model_as_default():
    """
    GeminiExtractionProvider must pick up the model from settings when
    no explicit model argument is passed.
    """
    mock_client = MagicMock()
    provider = GeminiExtractionProvider(
        api_key="valid-test-key",
        client=mock_client,
    )
    assert provider.model == settings.gemini_model


def test_gemini_provider_explicit_model_overrides_settings():
    """
    When an explicit model is passed to GeminiExtractionProvider it must take
    precedence over settings — GEMINI_MODEL env var is not ignored.
    """
    mock_client = MagicMock()
    provider = GeminiExtractionProvider(
        api_key="valid-test-key",
        model="gemini-1.5-pro",
        client=mock_client,
    )
    assert provider.model == "gemini-1.5-pro"


# ─────────────────────────────────────────────────────────────────────────────
# TASK 6 — Timeout reliability & missing-field recomputation regression tests
# ─────────────────────────────────────────────────────────────────────────────


def test_gemini_client_init_failure_raises_provider_error(monkeypatch):
    """
    If genai.Client initialization with HttpOptions fails, GeminiProviderError
    must be raised immediately rather than falling back silently.
    """
    import app.services.gemini_provider as gp_module

    def bad_client(*args, **kwargs):
        raise RuntimeError("Network configuration error")

    monkeypatch.setattr(gp_module.genai, "Client", bad_client)

    with pytest.raises(GeminiProviderError) as exc_info:
        GeminiExtractionProvider(api_key="valid-test-key", timeout=10.0)

    assert "Failed to initialize Gemini client with timeout" in str(exc_info.value)


def test_gemini_provider_real_sdk_sets_timeout():
    """
    Verify with the installed google-genai 3.6.1 SDK that HttpOptions timeout
    is actually passed and configured on the internal client transport.
    """
    provider = GeminiExtractionProvider(api_key="valid-test-key", timeout=12.5)
    # google-genai stores http_options on the internal api_client
    http_opts = getattr(provider._client._api_client, "_http_options", None)
    assert http_opts is not None
    assert http_opts.timeout == 12500  # 12.5s in milliseconds


def test_recompute_missing_fields_preserves_legitimate_clarification_needs():
    """
    _recompute_missing_fields must preserve legitimate clarification needs
    present in missing_fields if the corresponding attribute is None.
    """
    extraction = ExtractionResult(
        num_servers=2,
        gpu_family="RTX",
        gpu_count_per_server=8,
        gpu_model=None,
        missing_fields=["gpu_model"],
    )
    _recompute_missing_fields(extraction)
    assert "gpu_model" in extraction.missing_fields
    assert "num_servers" not in extraction.missing_fields


def test_recompute_missing_fields_removes_satisfied_fields():
    """
    If a field was in missing_fields but is now populated on extraction,
    it must be removed.
    """
    extraction = ExtractionResult(
        num_servers=2,
        gpu_family="RTX",
        gpu_count_per_server=8,
        ram_gb=512,
        missing_fields=["ram_gb", "gpu_count_per_server"],
    )
    _recompute_missing_fields(extraction)
    assert extraction.missing_fields == []


def test_recompute_missing_fields_enforces_required_fields():
    """
    Missing required fields (num_servers, sku/gpu_family, sku/gpu_count) must
    always be added even if missing_fields was empty.
    """
    extraction = ExtractionResult(
        sku="RST-RTX8",
        num_servers=None,
        missing_fields=[],
    )
    _recompute_missing_fields(extraction)
    assert extraction.missing_fields == ["num_servers"]


def test_recompute_missing_fields_discards_unknown_or_invalid_fields():
    """
    Unknown, arbitrary, or internal field names in missing_fields must be discarded
    by the whitelist check in _recompute_missing_fields.
    """
    extraction = ExtractionResult(
        sku="RST-RTX8",
        num_servers=2,
        missing_fields=[
            "arbitrary_field",
            "unknown_foo",
            "internal_discount",
            "margin",
            "gpu_model",  # valid extraction field, currently None
        ],
    )
    _recompute_missing_fields(extraction)
    # Only valid extraction fields whose value is None are preserved
    assert "gpu_model" in extraction.missing_fields
    assert "arbitrary_field" not in extraction.missing_fields
    assert "unknown_foo" not in extraction.missing_fields
    assert "internal_discount" not in extraction.missing_fields
    assert "margin" not in extraction.missing_fields


def test_recompute_missing_fields_multi_turn_clarification():
    """
    Multi-turn test: Turn 1 creates a base extraction with missing gpu_count_per_server.
    Turn 2 supplies gpu_count_per_server=8. _merge_extractions must clear
    gpu_count_per_server from missing_fields upon satisfaction.
    """
    base = ExtractionResult(
        gpu_family="RTX",
        num_servers=2,
        gpu_memory_gb=96,
        missing_fields=["gpu_count_per_server"],
        explicitly_stated=["gpu_family", "num_servers", "gpu_memory_gb"],
    )
    update = ExtractionResult(
        gpu_count_per_server=8,
        missing_fields=[],
        explicitly_stated=["gpu_count_per_server"],
    )
    merged = _merge_extractions(base, update)
    assert merged.gpu_count_per_server == 8
    assert merged.missing_fields == []
    assert "gpu_count_per_server" in merged.explicitly_stated
