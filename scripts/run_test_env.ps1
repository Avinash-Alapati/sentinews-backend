# Start SentiNews backend in test/load-testing environment mode
Write-Host "Starting SentiNews API in Test Environment (Rate Limiting Disabled, Yahoo Latency 4s)..." -ForegroundColor Cyan
if (Test-Path ".\venv\Scripts\Activate.ps1") {
    .\venv\Scripts\Activate.ps1
}
uvicorn app.main:app --env-file .env.test --host 127.0.0.1 --port 8000 --reload
