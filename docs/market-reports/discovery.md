# Market Reports Module — Discovery & Technical Specifications (Updated)

**Document Version:** 1.1.0  
**Status:** Approved Architecture (Phase 0 Discovery Complete)  
**Author:** Senior Backend Engineer (Antigravity)  
**Date:** September 2026  

---

## 1. Executive Summary

This document captures the architectural findings, module design patterns, infrastructure conventions, and integration strategies required to build the new **`market_reports`** module in SentiNews. The module delivers automated, scheduled **`PRE_MARKET`** and **`POST_MARKET`** reports sourced from **Finnhub API** and **Stock News API** (`stocknewsapi.com`), strictly adhering to hexagonal architecture, SEBI compliance guidelines, APM metrics instrumentation, distributed worker patterns, and operational observability.

---

## 2. Discovery Findings & Architecture Decisions

### 2.1 Hexagonal Module Layout
The `market_reports` module replicates the clean architecture layout established in `app/modules/news_intelligence`:

```
app/modules/market_reports/
├── domain/
│   ├── entities.py              # Dataclasses: MarketReport, section value objects (HeadlineItem without sentiment tags)
│   ├── enums.py                 # ReportType, ReportStatus
│   └── services/
│       ├── compliance.py        # Strict SEBI compliance filter & headline drop logic
│       └── trading_calendar.py  # NSE trading calendar & holiday calculation
├── application/
│   ├── ports.py                 # Protocol interfaces (FinnhubPort, StockNewsPort, RepositoryPort)
│   ├── use_cases/
│   │   ├── generate_pre_market.py
│   │   ├── generate_post_market.py
│   │   └── generate_report.py   # Orchestration, fail-open logic & crash-signature reporting
│   └── queries/
│       ├── get_report.py        # Single report fetching with 404 validation
│       └── list_reports.py      # Paginated report listing with filtering
└── infrastructure/
    ├── adapters/
    │   ├── finnhub_client.py    # Implements FinnhubPort via shared traced HTTP client
    │   └── stocknews_client.py  # Implements StockNewsPort via shared traced HTTP client
    ├── repositories/
    │   └── market_report_repository.py # SQLAlchemy repository implementing RepositoryPort
    ├── mappers.py               # ORM <-> Domain entity bidirectional mapping
    ├── locks.py                 # Distributed Redis lock & caching primitives
    └── scheduler.py             # APScheduler jobs (enqueue-only) & worker dispatchers
```

---

### 2.2 SEBI Regulatory Compliance & Headline Sanitization
1. **Sentiment Tags Stripped:** Third-party "Bullish"/"Bearish" labels are stripped from `HeadlineItem`. Headline items contain only factual metadata (`{headline, source, url, published_at}`).
2. **Granular Compliance Validation:**
   - **Generated Sections:** Evaluated as a whole; any detected advisory phrase ("buy now", "target price", "multibagger", "strong sell", "stop loss") causes validation failure.
   - **Third-Party Headlines:** Evaluated per headline. Prohibited actionable terms cause the individual offending headline to be **dropped** from the list, keeping the rest of the report intact without altering quoted text.
3. **Mandatory Disclaimer:** Every API schema and DB row includes:
   > *"For informational purposes only. Not investment advice. Sentinews is not a SEBI-registered investment adviser or research analyst."*
4. **Source Attribution:** Public reports expose `source_providers: ["finnhub", "stocknews"]` and `is_partial: bool`.

---

### 2.3 Hybrid NSE Trading Calendar
- **Primary Deterministic Authority:** `NSETradingCalendar` with pre-configured NSE declared holidays (2024–2027+) and weekend awareness (Saturday/Sunday = non-trading).
- **Secondary Live Verification:** Finnhub's `/stock/market-status?exchange=IN` endpoint to detect emergency exchange closures or special trading sessions (e.g. Muhurat trading).
- **Behavior:** Scheduled generation is automatically skipped on non-trading days, logging the reason to APM.

---

### 2.4 External API Clients & Upstream Observability
- All HTTP calls wrap through `create_traced_async_client(provider="finnhub"|"stocknews")` in `app/infrastructure/observability/http_tracer.py`.
- Metrics emitted automatically:
  - `upstream_request_duration_seconds{provider="finnhub"|"stocknews", operation}`
  - `upstream_requests_total{provider="finnhub"|"stocknews", outcome}`
  - `market_data_staleness_seconds{provider="finnhub"|"stocknews"}`
- Route templates registered in `ROUTE_FEATURE_MAP`:
  - `market_reports_pre_market`, `market_reports_post_market`, `market_reports_get`, `market_reports_list`, `market_reports_generate`.

---

### 2.5 APScheduler → Worker Flow & APM Alerting on Hard Failure
- **Schedules:**
  - `PRE_MARKET`: Weekdays at **07:45 IST** (`02:15 UTC`).
  - `POST_MARKET`: Weekdays at **16:00 IST** (`10:30 UTC`).
- **Fail-Open Strategy:** If one vendor fails, the report is generated with data from the other vendor and flagged `is_partial = True`.
- **Hard Failure / Crash Signatures:** If both vendors fail or a fatal exception occurs:
  - Report is persisted as `status = FAILED` with `error_details`.
  - Exception is recorded into `CrashSignatureEngine`, incrementing `crash_signature_total` and logging structured error details with `error_code="MARKET_REPORT_GENERATION_FAILED"`, alerting operators on the Grafana master dashboard.

---

### 2.6 Redis Conventions
- Raw vendor response cache: `market_reports:vendor:{provider}:{endpoint_hash}` (TTL: 300–600s).
- Idempotency & concurrency lock: `market_reports:lock:{report_type}:{date}` (TTL: 120s).
- Published report cache: `market_reports:latest:{report_type}` (TTL: 300s).
