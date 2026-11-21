"""
Central catalog data for Rapid Stream Technology FZE.

This is the SINGLE SOURCE OF TRUTH for all catalog information.
Do not duplicate SKU, pricing, or stock data anywhere else.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CatalogEntry:
    sku: str
    gpu_family: str
    gpu_model: str
    gpu_memory_gb: int
    gpu_count: int
    ram_gb: int  # In GB. 2 TB = 2048 GB.
    stock: int
    lead_time: str
    selling_price: Optional[float]  # None means pending supplier quote
    internal_cost: Optional[float]  # None means pending
    description: str


CATALOG: dict[str, CatalogEntry] = {
    "RST-RTX8": CatalogEntry(
        sku="RST-RTX8",
        gpu_family="RTX",
        gpu_model="RTX PRO 6000 Server Edition",
        gpu_memory_gb=96,
        gpu_count=8,
        ram_gb=512,
        stock=2,
        lead_time="2 business days",
        selling_price=245_000.0,
        internal_cost=219_000.0,
        description="8× RTX PRO 6000 Server Edition, 96 GB/GPU, 512 GB RAM",
    ),
    "RST-H2004": CatalogEntry(
        sku="RST-H2004",
        gpu_family="H200",
        gpu_model="H200 NVL",
        gpu_memory_gb=141,
        gpu_count=4,
        ram_gb=512,
        stock=0,
        lead_time="4 weeks, subject to confirmation",
        selling_price=330_000.0,
        internal_cost=299_000.0,
        description="4× H200 NVL, 141 GB/GPU, 512 GB RAM",
    ),
    "RST-B3008": CatalogEntry(
        sku="RST-B3008",
        gpu_family="B300",
        gpu_model="HGX B300",
        gpu_memory_gb=288,
        gpu_count=8,
        ram_gb=2048,  # 2 TB
        stock=0,
        lead_time="To be confirmed",
        selling_price=None,  # Pending supplier quote
        internal_cost=None,  # Pending
        description="8× HGX B300, 288 GB/GPU, 2 TB RAM",
    ),
}


def get_all_skus() -> list[str]:
    return list(CATALOG.keys())


def get_entry(sku: str) -> Optional[CatalogEntry]:
    return CATALOG.get(sku)
