from fastapi import FastAPI

from review_api import documents, health

app = FastAPI(title="Document Archive Pipeline", version="0.1.0")

app.include_router(health.router)
app.include_router(documents.router)
