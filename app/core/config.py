import os
import tempfile
from typing import List, Optional, Union
from pydantic import AnyHttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    PROJECT_NAME: str = "SentiNews API"
    API_V1_STR: str = "/api/v1"
    
    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://localhost:3002",
    ]

    @field_validator("CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)


    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sentinews"
    SYNC_DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/sentinews"
    MIGRATION_DATABASE_URL: Optional[str] = None  # Direct unpooled URL for Alembic migrations
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 10
    DB_POOL_PRE_PING: bool = True
    DB_POOL_RECYCLE: int = 300
    DB_STATEMENT_TIMEOUT_MS: int = 5000  # 5 second PostgreSQL statement timeout
    DB_DISABLE_PREPARED_STATEMENTS: bool = False  # Set to True for PgBouncer / Neon transaction pooler
    TRUSTED_PROXY_COUNT: int = 1  # 1 for Caddy reverse proxy; 2 for Cloudflare + Caddy

    # Logging & Observability
    LOG_LEVEL: str = "INFO"
    APM_ENABLED: bool = True
    METRICS_TOKEN: str = "sentinews_metrics_secret_token"
    INTERNAL_API_SECRET: str = "sentinews_internal_api_secret_key"
    ENV: str = "development"
    SERVICE_NAME: str = "sentinews-api"
    LOKI_URL: Optional[str] = None
    SENTRY_DSN: Optional[str] = None
    APP_MEMORY_LIMIT_BYTES: int = 536870912  # 512 MB default
    PROMETHEUS_MULTIPROC_DIR: Optional[str] = os.path.join(tempfile.gettempdir(), "prometheus_multiproc")
    SHUTDOWN_MARKER_FILE: str = os.path.join(tempfile.gettempdir(), ".sentinews_clean_shutdown")
    WEB_CONCURRENCY: int = 2
    DEBUG: bool = False
    ENABLE_DOCS: bool = True  # Disabled in production by default

    # Security
    SECRET_KEY: str = "your_super_secret_jwt_key_here_change_in_production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    REFRESH_TOKEN_ROTATION_GRACE_SECONDS: int = 10  # 10s grace window for concurrent SPA tabs / network retries
    AUTH_REFRESH_MODE: str = "body"  # "body" (cross-site safe default) or "cookie" (same-domain)
    
    # WebSocket Security & Limits
    WS_MAX_CONNECTIONS_PER_IP: int = 10
    WS_MAX_CONNECTIONS_PER_USER: int = 5
    WS_TICKET_EXPIRE_SECONDS: int = 60
    
    # Google OAuth 2.0
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/google/callback"
    GOOGLE_REDIRECT_URI_ALLOWLIST: List[str] = [
        "http://localhost:8000/api/v1/auth/google/callback",
        "http://localhost:5173/auth/callback",
        "http://localhost:3000/auth/callback",
    ]
    FRONTEND_URL: str = "http://localhost:5173"

    # Market Data Providers
    MARKET_PROVIDER: str = "yahoo"
    MARKET_FALLBACK_PROVIDER: str = "upstox"
    MARKET_LATENCY_THRESHOLD_SECONDS: float = 4.0
    MARKET_ENABLE_LATENCY_FALLBACK: bool = True

    # Upstox API
    UPSTOX_API_KEY: str = ""
    UPSTOX_API_SECRET: str = ""
    UPSTOX_REDIRECT_URI: str = "http://localhost:3001/api/auth/callback/upstox"
    UPSTOX_ACCESS_TOKEN: str = ""

    # Global Market Providers
    FINNHUB_API_KEY: str = ""
    ALPHAVANTAGE_API_KEY: str = ""
    TWELVEDATA_API_KEY: str = ""

    # News APIs
    STOCKNEWS_API_KEY: str = ""
    NEWSAPI_API_KEY: str = ""
    GNEWS_API_KEY: str = ""

    # AI Models
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""

    # Cache & Envelope Settings (Soft & Hard TTLs in seconds)
    CACHE_TTL: int = 300
    QUOTE_CACHE_TTL: int = 30
    MARKET_OVERVIEW_SOFT_TTL: int = 30
    MARKET_OVERVIEW_HARD_TTL: int = 1800  # 30 minutes
    INDICES_SOFT_TTL: int = 30
    INDICES_HARD_TTL: int = 1800
    QUOTE_SOFT_TTL: int = 30
    QUOTE_HARD_TTL: int = 1800
    CANDLES_SOFT_TTL: int = 300  # 5 minutes
    CANDLES_HARD_TTL: int = 7200  # 2 hours
    SEARCH_SOFT_TTL: int = 3600  # 1 hour
    SEARCH_HARD_TTL: int = 86400  # 24 hours
    ETFS_SOFT_TTL: int = 300
    ETFS_HARD_TTL: int = 3600
    CACHE_SOFT_TTL_JITTER_PCT: float = 0.15  # 15% random jitter (+-15%)
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_BROKER_URL: Optional[str] = None  # Celery broker URL, defaults to REDIS_URL if not set
    REDIS_ENABLED: bool = True
    REDIS_TIMEOUT: float = 1.5
    REDIS_TLS_ENABLED: bool = False
    REDIS_SSL_CERT_REQS: Optional[str] = "none"  # "none", "optional", "required"
    REDIS_HEALTH_CHECK_INTERVAL: int = 30
    REDIS_SOCKET_KEEPALIVE: bool = True
    REDIS_RETRY_ON_TIMEOUT: bool = True
    REDIS_MAX_CONNECTIONS: int = 50

    # Fetcher & Background Scheduling
    FETCHER_MODE: str = "inprocess"  # "inprocess" (FastAPI lifespan leader) | "worker" (Celery/APScheduler)
    MARKET_TIMEZONE: str = "Asia/Kolkata"
    MARKET_HOURS_START_TIME: str = "09:15"
    MARKET_HOURS_END_TIME: str = "15:30"
    MARKET_REFRESH_INTERVAL_IN_HOURS: float = 30.0  # seconds
    MARKET_REFRESH_INTERVAL_OFF_HOURS: float = 300.0  # 5 minutes
    MARKET_REFRESH_INTERVAL_WEEKEND: float = 900.0  # 15 minutes
    MAX_QUOTE_UNIVERSE_SIZE: int = 300
    QUOTE_BATCH_SIZE: int = 50

    # Circuit Breaker Settings
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
    CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS: float = 60.0
    CIRCUIT_BREAKER_HALF_OPEN_SUCCESS_THRESHOLD: int = 2

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_AUTH_PER_MINUTE: int = 5  # Per specific Account+IP pair
    RATE_LIMIT_AUTH_ACCOUNT_AGGREGATE_PER_MINUTE: int = 15  # Per Account across all IPs (distributed brute force defense)
    RATE_LIMIT_AUTH_IP_AGGREGATE_PER_MINUTE: int = 30  # Per IP across all Accounts (credential stuffing defense)
    RATE_LIMIT_AUTH_FALLBACK_PER_MINUTE: int = 5
    RATE_LIMIT_REFRESH_PER_MINUTE: int = 30
    RATE_LIMIT_OAUTH_PER_MINUTE: int = 10
    RATE_LIMIT_NEWS_CLICK_PER_MINUTE: int = 60
    RATE_LIMIT_HEAVY_COMPUTE_PER_MINUTE: int = 20
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 120

    # Internal Secrets & Security
    INTERNAL_API_SECRET: str = "sentinews_internal_api_secret_change_in_production"
    INTERNAL_METRICS_TOKEN: str = "sentinews_metrics_secret_token_change_in_production"

    # Refresh Token Rotation Policy
    REFRESH_TOKEN_ROTATION_GRACE_SECONDS: int = 10  # 10s grace window for legitimate near-simultaneous multi-tab refreshes

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.ENVIRONMENT.lower() == "production":
            # 1. SECRET_KEY validation
            if not self.SECRET_KEY or self.SECRET_KEY.startswith("your_super_secret") or self.SECRET_KEY in ("secret", "changeme") or len(self.SECRET_KEY) < 32:
                raise ValueError("In production, SECRET_KEY must be a cryptographically strong secret with length >= 32.")

            # 2. METRICS_TOKEN and INTERNAL_API_SECRET validation
            metrics_token = self.METRICS_TOKEN
            if self.INTERNAL_METRICS_TOKEN and self.INTERNAL_METRICS_TOKEN != "sentinews_metrics_secret_token_change_in_production":
                metrics_token = self.INTERNAL_METRICS_TOKEN

            if not metrics_token or "change_in_production" in metrics_token or len(metrics_token) < 32:
                raise ValueError("In production, INTERNAL_METRICS_TOKEN must be a cryptographically secure secret with length >= 32.")

            if not self.INTERNAL_API_SECRET or "change_in_production" in self.INTERNAL_API_SECRET or len(self.INTERNAL_API_SECRET) < 32:
                raise ValueError("In production, INTERNAL_API_SECRET must be a cryptographically secure secret with length >= 32.")

            if self.INTERNAL_API_SECRET == metrics_token:
                raise ValueError("In production, INTERNAL_API_SECRET and INTERNAL_METRICS_TOKEN must be distinct separate secrets.")

            # 3. CORS_ORIGINS validation (no wildcard, no localhost)
            if "*" in self.CORS_ORIGINS:
                raise ValueError("Wildcard '*' is forbidden in CORS_ORIGINS in production.")
            for origin in self.CORS_ORIGINS:
                if "localhost" in origin or "127.0.0.1" in origin or "0.0.0.0" in origin:  # nosec B104
                    raise ValueError(f"Localhost/loopback origin '{origin}' is forbidden in CORS_ORIGINS in production.")

            # 4. DATABASE_URL validation (no default credentials or localhost)
            if "postgres:postgres@localhost" in self.DATABASE_URL or "postgres:postgres@localhost" in self.SYNC_DATABASE_URL:
                raise ValueError("Default local database credentials (postgres:postgres@localhost) are forbidden in production DATABASE_URL.")

            # 5. REDIS_URL validation
            if self.REDIS_ENABLED and "localhost:6379" in self.REDIS_URL:
                raise ValueError("Localhost REDIS_URL (localhost:6379) is forbidden in production when REDIS_ENABLED=True.")

            # 6. Debug flags
            if getattr(self, "DEBUG", False) or self.LOG_LEVEL.upper() == "DEBUG":
                raise ValueError("DEBUG mode / LOG_LEVEL='DEBUG' is forbidden in production.")

            # 7. Refresh token rotation grace seconds
            if self.REFRESH_TOKEN_ROTATION_GRACE_SECONDS < 0:
                raise ValueError("REFRESH_TOKEN_ROTATION_GRACE_SECONDS must be >= 0.")

        return self

    model_config = SettingsConfigDict(
        env_file=os.getenv("ENV_FILE") or (
            ".env.test"
            if os.getenv("ENVIRONMENT", "").lower() in ("test", "testing", "loadtest")
            and os.path.exists(".env.test")
            else ".env"
        ),
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
