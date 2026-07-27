# Market Price Integration Implementation Plan

> **For Claude:** Execute this plan in the existing `feature/market-price-integration` worktree. Do not use subagents for this implementation.

**Goal:** For quote rows without an internal standard-price match, compare against a local market-price cache populated from DeviceMart and Mouser, perform a live lookup only when the cache is missing or expired, and preserve source links, images, and collection evidence.

**Architecture:** Keep external market observations separate from the internal standard-price catalog. A market lookup service performs cache-first, source-isolated collection and returns a comparison result without inventing a price when sources fail. The analysis page requests this result only for `NO_MATCH` rows and displays the evidence beside the quote.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, SQLite, httpx, React, TypeScript, Vitest.

---

## Task 1: Add immutable market observation storage

**Files:**
- Create: `backend/app/market/__init__.py`
- Create: `backend/app/market/models.py`
- Create: `backend/alembic/versions/0009_market_price.py`
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/market/test_market_models.py`

1. Write focused tests for source, product, collection run, observation, quantity tier, and evidence metadata.
2. Add append-only market models with unique source/product keys and SHA-256 evidence fields.
3. Add configuration for seven-day TTL, evidence directory, Mouser credentials, and DeviceMart collection controls.
4. Add and round-trip migration `0009`.

## Task 2: Implement cache-first comparison service

**Files:**
- Create: `backend/app/market/schemas.py`
- Create: `backend/app/market/repository.py`
- Create: `backend/app/market/evidence.py`
- Create: `backend/app/market/service.py`
- Test: `backend/tests/market/test_market_service.py`

1. Test fresh-cache reuse, expired-cache refresh, per-source failure isolation, quantity-tier selection, and no-price fallback.
2. Store raw JSON/HTML, downloaded image, optional screenshot, original URL, hashes, stock, MOQ, VAT/shipping notes, and collection time.
3. Compare quote unit price with the collected minimum/median/maximum and return `LOW`, `WITHIN_RANGE`, `HIGH`, or `REVIEW_REQUIRED`.
4. Never use stale or failed observations as a current assessment.

## Task 3: Add Mouser and DeviceMart collectors

**Files:**
- Create: `backend/app/market/adapters/base.py`
- Create: `backend/app/market/adapters/mouser.py`
- Create: `backend/app/market/adapters/devicemart.py`
- Test: `backend/tests/market/test_mouser_adapter.py`
- Test: `backend/tests/market/test_devicemart_adapter.py`
- Create: `backend/tests/market/fixtures/`

1. Implement Mouser official Search API parsing, including quantity price breaks and KRW/currency metadata.
2. Implement DeviceMart public search/product-page parsing with conservative request pacing.
3. Keep network tests opt-in; default tests use saved minimal fixtures.
4. Capture source URL and raw response for every accepted product. Record a screenshot when a browser capture facility is available; otherwise mark screenshot capture unavailable rather than failing price collection.

## Task 4: Expose lookup and precollection APIs

**Files:**
- Create: `backend/app/api/market.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/api/test_market_api.py`

1. Add a lookup endpoint accepting a raw quote item ID and optional force-refresh flag.
2. Add a batch precollection endpoint for reviewed item queries.
3. Add evidence retrieval endpoints that only serve files inside the configured evidence directory.
4. Return cache state, source failures, product links, image/evidence links, collection time, and assessment.

## Task 5: Integrate the quote analysis page

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/QuoteAnalysisPage.tsx`
- Modify: `frontend/src/pages/QuoteAnalysisPage.test.tsx`
- Modify: `frontend/src/styles.css`

1. Add typed market lookup requests and responses.
2. For each `NO_MATCH` row, provide `시장가 조회` and show cache/live/loading/error states.
3. Display market range and assessment beside the quote, with source badge, product image, manufacturer/model, stock/MOQ, collection time, original-product link, and evidence link.
4. Correct the existing Korean mojibake in the touched analysis page and preserve its current workflow.

## Task 6: Document and verify

**Files:**
- Modify: `docs/HANDOFF_2026-07-24.md`
- Modify: `docs/PRESENTATION_MARKET_PRICE.md`
- Modify: `.env`

1. Document Mouser key/configuration, DeviceMart pacing, precollection, cache/live behavior, and intranet handoff.
2. Run focused backend market tests and frontend analysis tests during implementation.
3. Run one final backend suite, Alembic upgrade/downgrade check, frontend tests, and production build.
4. Confirm the unrelated untracked legacy `.env` was not staged.
