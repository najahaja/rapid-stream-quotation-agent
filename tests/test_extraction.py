"""Tests for the mock extraction provider."""

from app.models.schemas import ExtractionConfidence
from app.services.extraction import MockExtractionProvider

provider = MockExtractionProvider()


def test_rtx_family_extracted():
    result = provider.extract("I need two RTX servers with 96 GB GPU memory.")
    assert result.gpu_family == "RTX"
    assert result.gpu_memory_gb == 96
    assert result.num_servers == 2


def test_missing_gpu_count_flagged():
    result = provider.extract("I need two RTX servers with 96 GB cards.")
    assert "gpu_count_per_server" in result.missing_fields


def test_gpu_count_per_server_extracted():
    result = provider.extract("Eight GPUs per server.")
    assert result.gpu_count_per_server == 8
    assert "gpu_count_per_server" not in result.missing_fields


def test_sku_extracted_directly():
    result = provider.extract("I want to order RST-RTX8.")
    assert result.sku == "RST-RTX8"
    assert "sku" in result.explicitly_stated


def test_h200_family():
    result = provider.extract("I need 4 H200 servers.")
    assert result.gpu_family == "H200"


def test_b300_family():
    result = provider.extract("Quote me on HGX B300 servers.")
    assert result.gpu_family == "B300"


def test_confidence_high_when_complete():
    result = provider.extract(
        "I need 2 RTX servers with 8 GPUs per server and 96 GB GPU memory."
    )
    assert result.confidence == ExtractionConfidence.HIGH


def test_confidence_low_when_sparse():
    result = provider.extract("I need a server.")
    assert result.confidence == ExtractionConfidence.LOW


def test_no_invention():
    """The extractor must not invent GPU counts when none are stated."""
    result = provider.extract("I need two RTX servers.")
    assert result.gpu_count_per_server is None


def test_mock_works_offline():
    """Mock provider must work without any API key (pure function call)."""
    # This test passes as long as extraction completes without raising
    result = provider.extract("I need an RTX server.")
    assert result is not None
