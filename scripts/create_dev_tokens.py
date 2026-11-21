"""
Script to generate development JWT tokens for testing.

Usage:
  python scripts/create_dev_tokens.py

Tokens are signed with the JWT_SECRET_KEY from .env or the default dev secret.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.security.authentication import (
    ROLE_CUSTOMER,
    ROLE_INTERNAL_REVIEWER,
    create_access_token,
)

print("=" * 60)
print("RST Quotation Agent — Development Tokens")
print("=" * 60)
print()

customer_token = create_access_token(subject="customer1", role=ROLE_CUSTOMER)
reviewer_token = create_access_token(subject="reviewer1", role=ROLE_INTERNAL_REVIEWER)

print("Customer Token (role: customer):")
print(f"  {customer_token}")
print()
print("Internal Reviewer Token (role: internal_reviewer):")
print(f"  {reviewer_token}")
print()
print("Usage: Add to request header:")
print("  Authorization: Bearer <token>")
print()
print("WARNING: These tokens are for local development only.")
print("Do not use the default JWT_SECRET_KEY in production.")
