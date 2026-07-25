from fastapi import FastAPI

from app.api import cleansing, documents
from app.db import models as _models


app = FastAPI(title="Price Analyzer", version="0.1.0")
app.include_router(
    documents.router,
    prefix="/api/documents",
    tags=["documents"],
)
app.include_router(
    cleansing.router,
    prefix="/api/cleansing",
    tags=["cleansing"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
