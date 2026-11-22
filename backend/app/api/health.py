"""`/health`: liveness for Docker and load balancers."""

from fastapi import APIRouter

from app.core.config import VERSION, settings
from app.llm.client import health as llm_health

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict:
    """Liveness (always 200 while the API runs) plus whether the models are usable: EDENAI_API_KEY set
    ("ok" | "no_api_key"; Eden is not called)."""
    return {"status": "ok", "version": VERSION, "llm": llm_health(), "llm_model": settings.llm_model,
            "transcribe_model": settings.transcribe_model}
