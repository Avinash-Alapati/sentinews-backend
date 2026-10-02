@echo off
REM Start SentiNews backend in test/load-testing environment mode
echo Starting SentiNews API in Test Environment (Rate Limiting Disabled, Yahoo Latency 4s)...
call ..\venv\Scripts\activate.bat 2>nul || call venv\Scripts\activate.bat 2>nul
uvicorn app.main:app --env-file .env.test --host 127.0.0.1 --port 8000 --reload
