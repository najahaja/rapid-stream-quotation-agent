"""Shared FastAPI dependencies."""

from app.database import get_db
from app.security.authentication import (
    get_current_user,
    require_customer_or_reviewer,
    require_internal_reviewer,
)

__all__ = [
    "get_db",
    "get_current_user",
    "require_customer_or_reviewer",
    "require_internal_reviewer",
]
