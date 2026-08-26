from fastapi import FastAPI

from app.api import (
    analysis,
    catalog,
    cleansing,
    dashboard,
    documents,
    market,
    pricing,
    settings,
    submissions,
)
from app.db import models as _models
from app.middleware.submission_size import SubmissionBodyLimitMiddleware


app = FastAPI(title="Price Analyzer", version="0.1.0")
app.add_middleware(SubmissionBodyLimitMiddleware)
app.include_router(
    dashboard.router,
    prefix="/api/dashboard",
    tags=["dashboard"],
)
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
app.include_router(
    analysis.router,
    prefix="/api/analysis",
    tags=["analysis"],
)
app.include_router(
    submissions.router,
    prefix="/api/submissions",
    tags=["submissions"],
)
app.include_router(
    market.router,
    prefix="/api/market",
    tags=["market"],
)
app.include_router(
    settings.router,
    prefix="/api/settings",
    tags=["settings"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
