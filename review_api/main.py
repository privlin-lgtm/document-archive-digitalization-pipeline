import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from review_api import (
    auth_routes,
    documents,
    entities,
    health,
    metrics,
    review_flags,
    search,
    stats,
)
from review_api.request_id import RequestIdMiddleware

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s: %(message)s"
    if False
    else "%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_docs = None if settings.app_env == "production" else "/docs"
_openapi = None if settings.app_env == "production" else "/openapi.json"

app = FastAPI(
    title="Document Archive Pipeline",
    version="0.1.0",
    docs_url=_docs,
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=_openapi,
)

app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=True,
)

app.include_router(health.router)
app.include_router(auth_routes.router)
app.include_router(metrics.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(entities.router)
app.include_router(review_flags.router)
app.include_router(stats.router)
