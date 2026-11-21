"""
Extraction service: converts natural-language customer messages into structured data.

PROVIDERS:
  - mock: Deterministic keyword/pattern matching. No API key needed. Used in development and tests.
  - gemini: Real Google Gemini LLM provider using the official 'google-genai' SDK.
    Requires GEMINI_API_KEY env var.

The extraction result NEVER determines prices or stock — those come from the catalog.
"""

import logging
import re
from abc import ABC, abstractmethod

from app.models.schemas import ExtractionConfidence, ExtractionResult

logger = logging.getLogger(__name__)


class ExtractionProvider(ABC):
    """Interface that all extraction providers must implement."""

    @abstractmethod
    def extract(self, message: str) -> ExtractionResult:
        """Extract structured server requirements from a natural-language message."""
        ...


class MockExtractionProvider(ExtractionProvider):
    """
    Deterministic mock extraction provider.

    Uses keyword and regex matching. Works fully offline without any API key.
    Covers all assessment test cases.

    LIMITATION: This is NOT an AI. It uses simple pattern matching and will
    not understand complex or ambiguous phrasing. It is suitable for testing
    and demonstration only. A real LLM provider would handle natural language
    more robustly.
    """

    # GPU family keyword patterns → (family, model, memory_gb)
    GPU_PATTERNS = [
        (
            re.compile(r"\brtx\s*pro\s*6000\b", re.I),
            "RTX",
            "RTX PRO 6000 Server Edition",
            96,
        ),
        (re.compile(r"\brtx\b", re.I), "RTX", "RTX PRO 6000 Server Edition", 96),
        (re.compile(r"\bh200\s*nvl\b", re.I), "H200", "H200 NVL", 141),
        (re.compile(r"\bh200\b", re.I), "H200", "H200 NVL", 141),
        (re.compile(r"\bhgx\s*b300\b", re.I), "B300", "HGX B300", 288),
        (re.compile(r"\bb300\b", re.I), "B300", "HGX B300", 288),
    ]

    SKU_PATTERN = re.compile(r"\bRST-[A-Z0-9]+\b")
    # Match numbers followed by GPU memory indicators
    GPU_MEMORY_PATTERN = re.compile(
        r"(\d+)\s*(?:gb|g)\s*(?:gpu|vram|memory|mem|per gpu|/gpu)", re.I
    )
    GPU_COUNT_PATTERN = re.compile(
        r"(\d+)\s*(?:gpus?|graphics cards?|cards?)\s*(?:per server)?", re.I
    )
    NUM_SERVERS_PATTERN = re.compile(
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:[A-Za-z0-9\-_]+\s+)?(?:servers?|units?|machines?|systems?|nodes?)\b",
        re.I,
    )
    RAM_PATTERN = re.compile(r"(\d+)\s*(tb|gb)\s+(?:ram|memory)", re.I)

    WORD_TO_NUM = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }

    def _to_int(self, val: str) -> int:
        val = val.strip().lower()
        if val in self.WORD_TO_NUM:
            return self.WORD_TO_NUM[val]
        return int(val)

    def extract(self, message: str) -> ExtractionResult:
        result = ExtractionResult()
        explicitly_stated: list[str] = []
        inferred: list[str] = []

        # 1. Direct SKU mention
        sku_match = self.SKU_PATTERN.search(message)
        if sku_match:
            result.sku = sku_match.group(0)
            explicitly_stated.append("sku")

        # 2. GPU family/model from keywords
        for pattern, family, model, mem_gb in self.GPU_PATTERNS:
            if pattern.search(message):
                result.gpu_family = family
                result.gpu_model = model
                # Only set memory from pattern if not explicitly stated
                if not result.gpu_memory_gb:
                    result.gpu_memory_gb = mem_gb
                    inferred.append("gpu_memory_gb")
                explicitly_stated.append("gpu_family")
                explicitly_stated.append("gpu_model")
                break

        # 3. Explicit GPU memory mention overrides inferred
        mem_match = self.GPU_MEMORY_PATTERN.search(message)
        if mem_match:
            result.gpu_memory_gb = int(mem_match.group(1))
            if "gpu_memory_gb" in inferred:
                inferred.remove("gpu_memory_gb")
            if "gpu_memory_gb" not in explicitly_stated:
                explicitly_stated.append("gpu_memory_gb")

        # 4. GPU count per server
        # First try "X GPUs per server" pattern
        per_server_match = re.search(
            r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+gpus?\s+per\s+server",
            message,
            re.I,
        )
        if per_server_match:
            result.gpu_count_per_server = self._to_int(per_server_match.group(1))
            explicitly_stated.append("gpu_count_per_server")
        else:
            gpu_count_match = self.GPU_COUNT_PATTERN.search(message)
            if gpu_count_match:
                result.gpu_count_per_server = self._to_int(gpu_count_match.group(1))
                explicitly_stated.append("gpu_count_per_server")

        # 5. Number of servers
        num_match = self.NUM_SERVERS_PATTERN.search(message)
        if num_match:
            result.num_servers = self._to_int(num_match.group(1))
            explicitly_stated.append("num_servers")
        else:
            # Try "I need X" pattern at start
            need_match = re.search(
                r"\b(?:need|want|require|order|buy)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
                message,
                re.I,
            )
            if need_match:
                result.num_servers = self._to_int(need_match.group(1))
                inferred.append("num_servers")

        # 6. RAM
        ram_match = self.RAM_PATTERN.search(message)
        if ram_match:
            val = int(ram_match.group(1))
            unit = ram_match.group(2).lower()
            result.ram_gb = val * 1024 if unit == "tb" else val
            explicitly_stated.append("ram_gb")

        # 7. Determine missing fields
        missing = []
        if not result.gpu_family and not result.sku:
            missing.append("gpu_family")
        if not result.gpu_count_per_server and not result.sku:
            missing.append("gpu_count_per_server")
        if not result.num_servers:
            missing.append("num_servers")
        result.missing_fields = missing

        # 8. Confidence
        filled = sum(
            [
                bool(result.gpu_family or result.sku),
                bool(result.gpu_memory_gb),
                bool(result.gpu_count_per_server or result.sku),
                bool(result.num_servers),
            ]
        )
        if filled >= 4:
            result.confidence = ExtractionConfidence.HIGH
        elif filled >= 2:
            result.confidence = ExtractionConfidence.MEDIUM
        else:
            result.confidence = ExtractionConfidence.LOW

        result.explicitly_stated = explicitly_stated
        result.inferred = [f for f in inferred if f not in explicitly_stated]

        logger.debug("Mock extraction result: %s", result)
        return result


def get_extraction_provider(provider_name: str) -> ExtractionProvider:
    """Factory: returns the correct extraction provider based on config."""
    if provider_name == "mock":
        return MockExtractionProvider()
    elif provider_name == "gemini":
        from app.services.gemini_provider import GeminiExtractionProvider

        return GeminiExtractionProvider()
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider_name}'. Use 'mock' or 'gemini'."
        )
