@echo off
REM Convenience launcher for the backend + frontend dev server (Windows).
REM
REM Usage:
REM   scripts\run_server.bat            REM default port 8000
REM   scripts\run_server.bat 8080       REM custom port
REM
REM Run from the project root.

setlocal

set PORT=%1
if "%PORT%"=="" set PORT=8000

cd /d "%~dp0\.."

python -c "import uvicorn" 2>nul
if errorlevel 1 (
    echo uvicorn not installed. Run: pip install -r requirements.txt
    exit /b 1
)

if not exist "outputs\scenarios" (
    echo No scenario outputs found. Generate them first:
    echo     python scripts\01_prepare_data.py
    echo     python scripts\04_run_full_pipeline.py
    exit /b 1
)

echo Starting backend on http://localhost:%PORT%/
echo   Frontend:  http://localhost:%PORT%/
echo   API docs:  http://localhost:%PORT%/docs
echo.
python -m uvicorn backend.main:app --reload --port %PORT%
