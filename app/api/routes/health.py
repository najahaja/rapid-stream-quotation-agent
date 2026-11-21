"""Health check endpoint."""

from fastapi import APIRouter

from app.config import settings
from app.models.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check() -> HealthResponse:
    """Returns application health and configuration summary."""
    return HealthResponse(
        status="ok",
        llm_provider=settings.llm_provider,
        environment=settings.environment,
    )
