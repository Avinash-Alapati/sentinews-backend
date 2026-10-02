# SentiNews Backend — High-Throughput Financial Intelligence Engine

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15%20%7C%2016-336791?style=flat-square&logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![Celery](https://img.shields.io/badge/Celery-5.6-37814A?style=flat-square&logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docker.com)
[![Tests](https://img.shields.io/badge/Tests-418%2F422%20Passing%20(99%25)-brightgreen?style=flat-square&logo=pytest&logoColor=white)](./tests)
[![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](./LICENSE)

SentiNews is a specialized, asynchronous financial intelligence and market analytics backend built for Indian retail investors. Engineered with **Hexagonal Architecture (Ports and Adapters)**, it delivers real-time market pulse aggregations, SEBI-aware financial news intelligence, exact `Decimal` FIFO portfolio accounting, Newton-Raphson XIRR performance solvers, and automated pre/post-market reports.

---

## Architecture Overview

```text
                                  +-----------------------+
                                  |   Web & Mobile Clients|
                                  +-----------+-----------+
                                              | HTTPS / WSS
                                              v
                              +---------------+---------------+
                              |    Caddy 2 Edge Reverse Proxy |
                              |  (Auto TLS + Rate Limit Edge) |
                              +---------------+---------------+
                                              |
                                              v
                       +----------------------+----------------------+
                       |     Pure ASGI RateLimit & APM Middleware    |
                       +----------------------+----------------------+
                                              |
                                              v
                                   FastAPI V1 Router (32 Endpoints)
         +---------------+---------------+----------------+---------------+---------------+
         |               |               |                |               |               |
         v               v               v                v               v               v
   +-----------+   +-----------+   +-----------+    +-----------+   +-----------+   +-----------+
   |   Auth    |   |  Market   |   |   News    |    | Watchlist |   | Portfolio |   |  Market   |
   |  & OAuth  |   |   Pulse   |   |Intelligence|   |  Module   |   | FIFO/XIRR |   |  Reports  |
   +-----+-----+   +-----+-----+   +-----+-----+    +-----+-----+   +-----+-----+   +-----+-----+
         |               |               |                |               |               |
         +---------------+---------------+----------------+---------------+---------------+
                                         |
            +----------------------------+----------------------------+
            |                            |                            |
            v                            v                            v
  +-------------------+        +--------------------+       +-------------------+
  |   PostgreSQL 15   |        |  Dual-Tier Cache   |       |   Celery Worker   |
  |  (Async SQLAlchemy|        | (Redis + LRU Mutex |       | (5 Recurring Jobs |
  |   + asyncpg Pool) |        |  Soft/Hard Envelope|       |  via Redis Broker)|
  +-------------------+        +--------------------+       +-------------------+
```

---

## Key Features

### 1. High-Performance Market Pulse & Dual-Tier Caching
- **Dual-Tier Cache Envelope:** In-process LRU cache coupled with distributed Redis 7, using a `{data, fetched_at, soft_ttl, hard_ttl}` envelope model with $\pm15\%$ jitter.
- **Single-Flight Stampede Mutex:** Eliminates cache dogpiling during traffic bursts using `asyncio.Lock` per cache key.
- **Latency Hedging:** Hedged fallback orchestration (`ResilientMarketProvider`) that queries Upstox sandbox if primary market feeds exceed a 2.0s threshold.
- **Sub-Millisecond Search:** In-memory symbol master indexing 50+ major Indian equities and indices.

### 2. SEBI-Aware News Intelligence Engine
- **Allowlisted RSS Ingestion:** Ingests public RSS feeds strictly from verified publications (Moneycontrol, LiveMint, Economic Times, Business Standard).
- **Zero Public Ingestion Routes:** News content enters exclusively through background workers.
- **Statutory Disclaimers:** All response payloads automatically inherit mandatory SEBI statutory disclaimer fields.
- **Dynamic Retention:** 24-hour default TTL, automatically extending to 72 hours with sticky ranking for stories exceeding 50 engagement clicks.

### 3. Precision Portfolio & Money Math
- **Exact Decimal Arithmetic:** Pure `decimal.Decimal` financial ledger preventing IEEE-754 binary floating-point rounding errors.
- **FIFO Cost Basis Engine:** Double-ended queue (`collections.deque[Lot]`) calculating realized/unrealized P&L across BUY, SELL, and SIP lots in $O(1)$ pop time.
- **Newton-Raphson XIRR Solver:** Numerical solver with analytical first-derivative computation to calculate annualized returns across non-periodic cash flows.

### 4. WebSocket Event Gateway with Ticket Authentication
- **60-Second Cryptographic Tickets:** Single-use ticket handshake (`POST /api/v1/ws/ticket`) preventing sensitive JWT tokens from leaking into query parameters or proxy logs.
- **Atomic Single-Use Invalidation:** Evaluated and deleted at connection upgrade time via Redis `GETDEL` Lua scripts.

### 5. Asynchronous Background Pipeline
- **Decoupled Architecture:** APScheduler acts strictly as a lightweight cron trigger, delegating all heavy I/O and NLP jobs to Celery workers backed by Redis.
- **5 Automated Workflows:** RSS news ingestion (every 10 min), expired article pruning (daily midnight), market report synthesis (07:00, 07:45, 16:00, 20:00 IST), cache warming, and portfolio sync.

---

## Tech Stack

- **Framework:** FastAPI 0.110+ (Asynchronous Python 3.11)
- **Database & ORM:** PostgreSQL 15 / 16, SQLAlchemy 2.0 (asyncpg), Alembic
- **Caching & Broker:** Redis 7 (Dual-Tier Cache + Celery Task Broker)
- **Workers & Schedulers:** Celery 5.6, APScheduler 3.11
- **Reverse Proxy & TLS:** Caddy 2 (Automated Let's Encrypt / ZeroSSL TLS)
- **Testing:** Pytest, pytest-asyncio, HTTPX, Hypothesis (Property-based IDOR fuzzing)
- **Observability:** Pure ASGI APM Middleware, Prometheus RED metrics, Grafana Loki logging
- **Infrastructure as Code:** Docker Compose, Terraform (AWS Graviton3 `t4g.small` in `ap-south-1` Mumbai)

---

## API Endpoint Inventory (32 Core Endpoints)

| Domain Module | Method | Path | Description | Auth |
| :--- | :---: | :--- | :--- | :--- |
| **Auth** | `POST` | `/api/v1/auth/register` | Register new user account with bcrypt hashing | None |
| | `POST` | `/api/v1/auth/login` | Authenticate & issue JWT access + sliding refresh token | None |
| | `POST` | `/api/v1/auth/refresh` | Rotate refresh token within concurrency grace window | Refresh |
| | `POST` | `/api/v1/auth/logout` | Revoke active user session | JWT |
| | `POST` | `/api/v1/auth/google/exchange` | SPA authorization code exchange | None |
| | `GET` | `/api/v1/auth/me` | Fetch authenticated user profile | JWT |
| **Market** | `GET` | `/api/v1/market/indices` | Major Indian benchmark indices (NIFTY, SENSEX, BANKNIFTY) | None |
| | `GET` | `/api/v1/market/overview` | Aggregated market pulse (gainers, losers, volume leaders) | None |
| | `GET` | `/api/v1/market/quote/{symbol}` | Real-time quote snapshot with negative caching | None |
| | `GET` | `/api/v1/market/quotes` | Multi-symbol batch quote lookup | None |
| | `GET` | `/api/v1/market/history/{symbol}`| Historical OHLCV candle series for charting | None |
| | `GET` | `/api/v1/market/search` | Sub-millisecond in-memory symbol search across 50+ tickers | None |
| **News** | `GET` | `/api/v1/news/latest` | Ranked chronological financial news feed | None |
| | `GET` | `/api/v1/news/trending` | Engaged stories crossing 50 clicks with 72h retention | None |
| | `GET` | `/api/v1/news/sources` | Verified allowlisted Indian financial publications | None |
| | `GET` | `/api/v1/news/{id}/full-coverage` | Semantic multi-publisher cluster of financial events | None |
| | `POST` | `/api/v1/news/{id}/click` | Atomic engagement click tracker (sole public news write) | None |
| **Watchlist** | `GET` | `/api/v1/watchlists` | Get user watchlists with eager-loaded items | JWT |
| | `POST` | `/api/v1/watchlists` | Create named watchlist | JWT |
| | `GET` | `/api/v1/watchlists/{id}` | Get specific watchlist (ownership verified) | JWT |
| | `POST` | `/api/v1/watchlists/{id}/stocks` | Add stock symbol to watchlist | JWT |
| | `DELETE` | `/api/v1/watchlists/{id}/stocks/{sym}` | Remove stock from watchlist | JWT |
| **Portfolio** | `POST` | `/api/v1/portfolio` | Create portfolio ledger | JWT |
| | `GET` | `/api/v1/portfolio/user/{user_id}` | Retrieve primary portfolio | JWT |
| | `POST` | `/api/v1/portfolio/{id}/transactions` | Record trade & trigger FIFO cost basis update | JWT |
| | `GET` | `/api/v1/portfolio/{id}/holdings` | Active holdings with live valuations & unrealized P&L | JWT |
| | `GET` | `/api/v1/portfolio/{id}/overview` | Aggregated realized/unrealized portfolio dashboard | JWT |
| | `GET` | `/api/v1/portfolio/{id}/allocation` | Sector and symbol concentration breakdown | JWT |
| | `GET` | `/api/v1/portfolio/{id}/performance` | Annualized Newton-Raphson XIRR calculation | JWT |
| | `GET` | `/api/v1/portfolio/{id}/news-feed` | Personalized news stream mapped to portfolio holdings | JWT |
| **Reports** | `GET` | `/api/v1/market-reports/pre-market/latest` | Domestic pre-market intelligence brief (08:00 IST) | JWT |
| | `GET` | `/api/v1/market-reports/post-market/latest` | Domestic post-market trading summary (16:00 IST) | JWT |
| **Gateway** | `POST` | `/api/v1/ws/ticket` | Generate 60-second single-use WebSocket ticket | JWT |
| | `WSS` | `/api/v1/ws` | Real-time price ticks and portfolio event stream | Ticket |
| **Health** | `GET` | `/readyz` | Deep readiness probe (PostgreSQL pool + Redis ping) | None |
| | `GET` | `/internal/metrics` | Prometheus RED metrics exposition | Bearer |

---

## Getting Started

### Prerequisites
- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 15+ and Redis 7 (if running natively)

### 1. Clone & Environment Configuration
```bash
git clone https://github.com/Avinash-Alapati/sentinews-backend.git
cd sentinews-backend

# Copy sample environment configuration
cp .env.example .env
```

Edit `.env` to supply application secrets (or use defaults for local development):
```ini
ENVIRONMENT=development
SECRET_KEY=your_64_character_hex_secret_key
DATABASE_URL=postgresql+asyncpg://sentinews:postgres@localhost:5432/sentinews
SYNC_DATABASE_URL=postgresql://sentinews:postgres@localhost:5432/sentinews
REDIS_URL=redis://localhost:6379/0
REDIS_BROKER_URL=redis://localhost:6379/1
```

### 2. Run with Docker Compose (Recommended)
Launch the entire 8-service containerized stack (API, Worker, Beat, Postgres, Redis Cache, Redis Broker, Migration Runner, Caddy):

```bash
docker compose up -d --build
```

Access services:
- **API Documentation (Swagger UI):** `http://localhost:8000/api/v1/docs`
- **Readiness Health Probe:** `http://localhost:8000/readyz`
- **Prometheus Metrics:** `http://localhost:8000/internal/metrics`

### 3. Local Native Development Setup
```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run database migrations
alembic upgrade head

# Start FastAPI development server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

In a separate terminal, start the background worker:
```bash
celery -A workers.celery_app worker --loglevel=info -c 2
```

---

## Testing & Quality Assurance

The codebase includes **422 automated test cases** across unit, integration, and security layers with **99.05% pass rate** and **70% overall statement coverage** (90–100% in domain and security layers).

```bash
# Run complete test suite
pytest

# Run with statement coverage report
pytest --cov=app --cov-report=term-missing

# Run property-based authorization and IDOR fuzzing
pytest tests/api/test_idor_and_authorization.py
```

### k6 Load Testing Benchmarks
Run the 100 Virtual User ramp load test (`observability/loadtest/k6/ramp.js`):
```bash
k6 run observability/loadtest/k6/ramp.js
```

**Benchmark Highlights (Baseline vs Optimized System under 100 VUs):**
- **Median Latency (p50):** 6,276 ms $\rightarrow$ **1,485 ms** (76.3% reduction)
- **Throughput:** 7.66 req/s $\rightarrow$ **17.83 req/s** (+132.8% increase)
- **Failure Rate:** 13.92% $\rightarrow$ **0.52%** (96.3% reduction)

---

## Production Deployment ($17.86/Month on AWS Mumbai)

The repository includes Terraform configurations (`terraform/environments/dev/`) and a production Docker Compose recipe optimized for an **AWS Graviton3 `t4g.small` instance** (2 vCPUs, 2 GB RAM) in `ap-south-1` Mumbai:

- **Compute & RAM:** ~995 MiB peak stack memory across 8 containers (40% RAM headroom).
- **Reverse Proxy:** Caddy 2 container handling automatic TLS termination and blocking `/internal/*` routes at the edge.
- **Secrets:** AWS SSM Parameter Store (`SecureString`, Free Tier).
- **Backups:** Automated nightly PostgreSQL database dumps streamed to Amazon S3 with 14-day lifecycle expiry.

---

## Authors & Contributors

- **Avinash Alapati** ([@Avinash-Alapati](https://github.com/Avinash-Alapati)) — Principal Architect & Author (Core Architecture, Domain Layer, Auth, News Intelligence, Portfolio Math, Caching, Observability, Deployment).
- **Yaswanth Kumar** ([@tadikalayaswanthkumar-sys](https://github.com/tadikalayaswanthkumar-sys)) — Co-contributor (Upstox & Indian Market Connectors in `app/integrations`).
- **Gorleanu** ([@gorleanu4](https://github.com/gorleanu4)) — Co-contributor (Initial Watchlist Domain & Repository).

---

## Regulatory & Legal Disclaimer

*The SentiNews backend is designed with SEBI-aware architectural guardrails (neutral language, mandatory statutory disclaimers, allowlisted RSS feeds, and refusal of actionable buy/sell advice). These features represent software engineering best practices and technical compliance design, not formal legal or financial advice. Market data adapters included in the repository are intended for development and sandbox prototyping; commercial public redistribution of Indian stock exchange data requires licensing through an exchange-authorized data vendor.*
