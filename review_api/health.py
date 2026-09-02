import logging

import redis
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import get_settings
from storage.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict:
    checks = {"database": False, "redis": False}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        logger.exception("readiness check failed: database unreachable")
    try:
        client = redis.from_url(get_settings().rate_limit_redis_url, socket_connect_timeout=0.5)
        client.ping()
        checks["redis"] = True
    except Exception:
        logger.exception("readiness check failed: redis unreachable")
    if not all(checks.values()):
        response.status_code = 503
        return {"status": "unavailable", "checks": checks}
    return {"status": "ok", "checks": checks}
