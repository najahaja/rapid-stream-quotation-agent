"""JWT authentication and authorization utilities."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)

ROLE_CUSTOMER = "customer"
ROLE_INTERNAL_REVIEWER = "internal_reviewer"

# Simple hardcoded dev users — in production replace with a real user store
DEV_USERS = {
    "customer1": {"password": "customerpass", "role": ROLE_CUSTOMER},
    "customer2": {"password": "customerpass", "role": ROLE_CUSTOMER},
    "reviewer1": {"password": "reviewerpass", "role": ROLE_INTERNAL_REVIEWER},
}

http_bearer = HTTPBearer(
    bearerFormat="JWT",
    description="Enter your JWT access token. Both raw token ('eyJ...') and prefixed ('Bearer eyJ...') are accepted.",
    auto_error=False,
)


def create_access_token(
    subject: str, role: str, expires_delta: Optional[timedelta] = None
) -> str:
    """Create a signed JWT token with subject and role embedded."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    payload = {
        "sub": subject,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def decode_token(token: str) -> dict:
    """Decode and verify a JWT. Raises HTTPException on failure."""
    cleaned_token = token.strip()
    # Strip any redundant 'Bearer ' / 'bearer ' prefixes (common in Swagger UI)
    while cleaned_token.lower().startswith("bearer "):
        cleaned_token = cleaned_token[7:].strip()

    try:
        payload = jwt.decode(
            cleaned_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError as exc:
        logger.warning("JWT decode failure: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> dict:
    """Dependency: returns decoded JWT payload for authenticated requests."""
    token_str: Optional[str] = None
    if credentials and credentials.credentials:
        token_str = credentials.credentials
    else:
        # Fallback for clients sending raw token in Authorization header without "Bearer "
        auth_header = request.headers.get("Authorization", "").strip()
        if auth_header:
            token_str = auth_header

    if not token_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(token_str)


def require_internal_reviewer(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: ensures the caller has the internal_reviewer role."""
    if user.get("role") != ROLE_INTERNAL_REVIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal reviewer role required",
        )
    return user


def require_customer_or_reviewer(user: dict = Depends(get_current_user)) -> dict:
    """Dependency: any authenticated user (customer or reviewer)."""
    role = user.get("role")
    if role not in (ROLE_CUSTOMER, ROLE_INTERNAL_REVIEWER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Valid role required",
        )
    return user
