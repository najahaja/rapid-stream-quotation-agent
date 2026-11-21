"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.routes import conversations, health, internal, quotations
from app.config import settings
from app.database import init_db
from app.models.schemas import LoginRequest, TokenResponse
from app.security.authentication import DEV_USERS, create_access_token
from app.services.gemini_provider import (
    GeminiAuthenticationError,
    GeminiProviderError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
    GeminiUnavailableError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def get_swagger_description() -> str:
    """Generate dynamic API description reflecting the active extraction provider."""
    provider = settings.llm_provider
    if provider == "gemini":
        provider_info = (
            f"**Active LLM Provider**: Google Gemini (`{settings.gemini_model}`)\n\n"
            "Hardware requirement extraction is powered by Google Gemini via schema-constrained JSON. "
            "Catalog matching, inventory checking, pricing, and approvals remain strictly deterministic."
        )
    else:
        provider_info = (
            "**Active LLM Provider**: Mock (Deterministic rule-based extraction)\n\n"
            "Running in offline development mode without external API keys. "
            "To switch to Google Gemini, set `LLM_PROVIDER=gemini` and configure `GEMINI_API_KEY`."
        )

    return (
        "AI-powered server quotation API for Rapid Stream Technology FZE. "
        "Accepts natural-language customer requests, matches catalog entries, "
        "checks stock, and creates quotation drafts for internal approval.\n\n"
        f"{provider_info}\n\n"
        "---\n\n"
        "### Swagger UI Authentication Quickstart\n"
        "1. Open **POST `/auth/token`** below and click **Try it out**.\n"
        "2. Submit development credentials:\n"
        '   - **Customer**: `{"username": "customer1", "password": "customerpass"}` (role: `customer`)\n'
        '   - **Reviewer**: `{"username": "reviewer1", "password": "reviewerpass"}` (role: `internal_reviewer`)\n'
        "3. Copy the returned `access_token`.\n"
        "4. Click the green **Authorize** button at the top right of this page.\n"
        "5. Paste the token into the **Value** box (both raw token `eyJ...` and `Bearer eyJ...` are accepted).\n"
        "6. Click **Authorize**, then **Close**. All protected endpoints are now authorized."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting RST Quotation Agent (provider=%s)", settings.llm_provider)
    if settings.llm_provider == "gemini":
        from app.services.gemini_provider import PLACEHOLDER_KEYS

        raw_key = (
            getattr(settings, "gemini_api_key", None)
            or os.environ.get("GEMINI_API_KEY", "")
            or ""
        ).strip()
        if not raw_key or raw_key.lower() in PLACEHOLDER_KEYS:
            raise RuntimeError(
                "Application startup failed: LLM_PROVIDER is set to 'gemini' but GEMINI_API_KEY "
                "is not configured in environment or .env. Please provide a valid GEMINI_API_KEY "
                "or switch to LLM_PROVIDER=mock for offline development."
            )
        logger.info("Gemini provider verified (model=%s)", settings.gemini_model)
    init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(
    title="RST AI Server Quotation Agent",
    description=get_swagger_description(),
    version="1.0.0",
    lifespan=lifespan,
)


def custom_openapi():
    """Dynamically generate OpenAPI schema to ensure description reflects active provider."""
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=get_swagger_description(),
        routes=app.routes,
    )
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi


# ── Exception handlers for controlled upstream LLM provider errors ──
@app.exception_handler(GeminiAuthenticationError)
async def gemini_auth_error_handler(request: Request, exc: GeminiAuthenticationError):
    logger.error("Gemini authentication failure: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "LLM provider authentication failed. Check server configuration."
        },
    )


@app.exception_handler(GeminiTimeoutError)
async def gemini_timeout_error_handler(request: Request, exc: GeminiTimeoutError):
    logger.error("Gemini timeout: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content={
            "detail": "Upstream LLM provider request timed out. Please try again."
        },
    )


@app.exception_handler(GeminiUnavailableError)
async def gemini_unavailable_error_handler(
    request: Request, exc: GeminiUnavailableError
):
    logger.error("Gemini service unavailable: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "Upstream LLM provider is temporarily unavailable due to high demand. Please try again later."
        },
    )


@app.exception_handler(GeminiRateLimitError)
async def gemini_rate_limit_error_handler(request: Request, exc: GeminiRateLimitError):
    logger.error("Gemini rate limit: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "Upstream LLM provider rate limit exceeded. Please retry after a brief delay."
        },
    )


@app.exception_handler(GeminiResponseError)
async def gemini_response_error_handler(request: Request, exc: GeminiResponseError):
    logger.error("Gemini malformed response: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={
            "detail": f"Upstream LLM provider returned an invalid response: {exc}"
        },
    )


@app.exception_handler(GeminiProviderError)
async def gemini_provider_error_handler(request: Request, exc: GeminiProviderError):
    logger.error("Gemini provider error: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": f"Upstream LLM provider error: {exc}"},
    )


# Include routers
app.include_router(health.router)
app.include_router(quotations.router)
app.include_router(conversations.router)
app.include_router(internal.router)

# Auth router
auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


@app.get("/")
def home():
    return {"message": "Rapid Stream Assessment test"}


@auth_router.post(
    "/token", response_model=TokenResponse, summary="Obtain a JWT access token"
)
def login(body: LoginRequest) -> TokenResponse:
    """
    Development login endpoint.
    Returns a signed JWT for the given username.

    Dev credentials:
    - customer1 / customerpass  → role: customer
    - reviewer1 / reviewerpass  → role: internal_reviewer
    """
    user = DEV_USERS.get(body.username)
    if not user or user["password"] != body.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    token = create_access_token(subject=body.username, role=user["role"])
    return TokenResponse(access_token=token, role=user["role"])


app.include_router(auth_router)
