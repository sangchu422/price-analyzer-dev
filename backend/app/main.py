from fastapi import FastAPI

from app.api import catalog, cleansing, documents, pricing
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
app.include_router(
    catalog.router,
    prefix="/api/catalog",
    tags=["catalog"],
)
app.include_router(
    pricing.router,
    prefix="/api/pricing",
    tags=["pricing"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
