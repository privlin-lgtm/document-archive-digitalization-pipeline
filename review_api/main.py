import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from review_api import documents, entities, health, review_flags, search, stats

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Document Archive Pipeline", version="0.1.0")

# Lets the web/ annotation UI (a separate-origin dev server / static host)
# call this API from a browser. The bearer token still gates every request
# beyond /health — CORS only controls which origins the browser lets through,
# not authorization.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(entities.router)
app.include_router(review_flags.router)
app.include_router(stats.router)
