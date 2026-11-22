"""HTTP layer: one router per resource; the domain code lives outside `app.api` and never imports it."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.api import auth, dictionaries, forms, health, history, jobs
from app.core.config import settings


def _history_enabled() -> None:
    """HISTORY_ENABLED=0: the server keeps no uploaded file or result, so /api/history answers 404."""
    if not settings.history_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Historia jest wyłączona (HISTORY_ENABLED=0).")


router = APIRouter()
router.include_router(auth.router)
router.include_router(jobs.router)
router.include_router(forms.router)
router.include_router(history.router, dependencies=[Depends(_history_enabled)])
router.include_router(dictionaries.router)
router.include_router(health.router)
