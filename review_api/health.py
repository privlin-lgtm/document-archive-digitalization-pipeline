import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from storage.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# Deliberately not rate-limited (no `Depends(enforce_rate_limit)`, unlike
# every other router) -- these are polled frequently and automatically by
# infrastructure (docker-compose/orchestrator healthchecks, load
# balancers), not by end users; rate-limiting them risks the healthcheck
# itself tripping the limit and the orchestrator wrongly cycling the
# container.
@router.get("/health")
def health() -> dict:
    """Liveness: "is the process up at all" -- deliberately cheap (no DB
    call) so it can't itself become slow/unavailable if the database is
    struggling, which is exactly the situation an orchestrator needs an
    accurate liveness signal for.
    """
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict:
    """Readiness: "can this instance actually serve a request" -- checks DB
    connectivity. A liveness-only /health returns "ok" even when Postgres
    is unreachable, which is exactly wrong for a load balancer/orchestrator
    deciding whether to route traffic here.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("readiness check failed: database unreachable")
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ok"}
