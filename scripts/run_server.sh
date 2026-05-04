#!/usr/bin/env bash
# Convenience launcher for the backend + frontend dev server.
#
# Usage:
#   ./scripts/run_server.sh              # default port 8000
#   ./scripts/run_server.sh 8080         # custom port
#
# Run from the project root.

set -e

PORT="${1:-8000}"
cd "$(dirname "$0")/.."

if ! python3 -c "import uvicorn" 2>/dev/null; then
    echo "uvicorn not installed. Run: pip install -r requirements.txt"
    exit 1
fi

if [ ! -d "outputs/scenarios" ] || [ -z "$(ls -A outputs/scenarios 2>/dev/null)" ]; then
    echo "No scenario outputs found. Generate them first:"
    echo "    python scripts/01_prepare_data.py"
    echo "    python scripts/04_run_full_pipeline.py"
    exit 1
fi

echo "Starting backend on http://localhost:${PORT}/"
echo "  Frontend:  http://localhost:${PORT}/"
echo "  API docs:  http://localhost:${PORT}/docs"
echo
exec python3 -m uvicorn backend.main:app --reload --port "$PORT"
