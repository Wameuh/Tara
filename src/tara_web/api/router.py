"""Versioned HTTP API routing."""

from fastapi import APIRouter

from .health import router as health_router
from .problem_details import problem_responses
from .public_config import router as public_config_router
from .routes.analytics import router as analytics_router
from .routes.events import router as events_router
from .routes.funding import router as funding_router
from .routes.jobs import router as jobs_router
from .routes.results import router as results_router
from .routes.sessions import router as sessions_router
from .routes.uploads import router as uploads_router

router = APIRouter(
    prefix="/api/v1",
    responses=problem_responses(400, 403, 404, 405, 409, 422, 428, 429, 500),
)
router.include_router(health_router)
router.include_router(public_config_router)
router.include_router(funding_router)
router.include_router(analytics_router)
router.include_router(uploads_router)
router.include_router(sessions_router)
router.include_router(jobs_router)
router.include_router(results_router)
router.include_router(events_router)
