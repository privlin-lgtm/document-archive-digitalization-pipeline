import logging

from fastapi import FastAPI

from config import get_settings
from review_api import documents, health

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Document Archive Pipeline", version="0.1.0")

app.include_router(health.router)
app.include_router(documents.router)
