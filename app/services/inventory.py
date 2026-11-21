"""Inventory / stock checking service."""

import logging

from app.data.catalog import get_entry
from app.models.schemas import StockCheckResult

logger = logging.getLogger(__name__)


def check_stock(sku: str, requested: int) -> StockCheckResult:
    """
    Check if requested quantity can be fulfilled from current stock.

    Rules:
    - Zero stock: always requires sales review
    - Partial stock: fulfill from stock, route remainder to sales
    - Full stock available: normal fulfillment
    """
    entry = get_entry(sku)
    if entry is None:
        raise ValueError(f"SKU '{sku}' not found in catalog")

    available = entry.stock
    can_fulfill = available >= requested
    shortage = max(0, requested - available)
    requires_sales_review = shortage > 0 or available == 0

    if available == 0:
        message = (
            f"SKU {sku} is currently out of stock (0 available). "
            f"Your request for {requested} unit(s) has been routed to the sales team "
            f"for confirmation. Lead time: {entry.lead_time}."
        )
    elif shortage > 0:
        message = (
            f"Only {available} of {requested} requested unit(s) are currently in stock. "
            f"{shortage} unit(s) have been routed to the sales team for confirmation. "
            f"Lead time for additional units: {entry.lead_time}."
        )
    else:
        message = f"All {requested} unit(s) of {sku} are available. Lead time: {entry.lead_time}."

    return StockCheckResult(
        sku=sku,
        requested=requested,
        available=available,
        can_fulfill=can_fulfill,
        shortage=shortage,
        requires_sales_review=requires_sales_review,
        message=message,
    )
