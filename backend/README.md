# Backend

FastAPI bridge between the algorithm pipeline outputs and the static frontend.

## Run

From the project root:

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Then visit:

- `http://localhost:8000/` — frontend (`app/`)
- `http://localhost:8000/docs` — Swagger / OpenAPI explorer
- `http://localhost:8000/api/health` — health check

## Layout

```
backend/
├── main.py              FastAPI app, CORS, static mount
├── api/
│   ├── scenarios.py     /api/scenarios/* listing, config, parties
│   ├── results.py       /api/scenarios/{id}/results/*
│   ├── maps.py          /api/scenarios/{id}/maps/*
│   └── diagnostics.py   /api/scenarios/{id}/diagnostics
├── services/
│   ├── output_service.py    Reads outputs/scenarios/{id}/* files
│   ├── config_service.py    Loads config/parties.csv
│   └── validation_service.py Runs integrity checks
├── schemas/__init__.py  Pydantic response models
└── README.md            (this file)
```

## Dataflow

```
algorithm pipeline
    ↓ writes
outputs/scenarios/<scenario_id>/{tables,geojson,json,documentation}/
    ↓ reads via OutputService
backend API (FastAPI)
    ↓ JSON over HTTP
app/ frontend (Jade)
```

The backend is **read-only**: it never modifies pipeline outputs. To
update the data the user runs `scripts/04_run_full_pipeline.py`. To
add a new scenario, generate a new outputs subfolder; the API picks
it up automatically.

## Adding a new endpoint

1. Define a Pydantic response schema in `backend/schemas/__init__.py`.
2. Add a route in the appropriate `backend/api/*.py` (or a new file).
3. If a new output file is required, add it to the `REQUIRED_FILES`
   list in `backend/services/validation_service.py` so the diagnostics
   check covers it.
4. Document the endpoint in `docs/frontend_api_guide.md`.

## Configuration

The backend reads:
- `config/scenario_config.json` — pipeline parameters (mostly used by the algorithm side; backend reads it indirectly via the per-scenario `scenario_summary.json`).
- `config/parties.csv` — party metadata for joining into responses.
- `outputs/scenarios/<id>/` — pipeline outputs.

There is no database. State lives entirely in files. This is intentional
for a coursework project; if a future version needs concurrent
simulations or user-submitted scenarios, the place to add a backing
store is `backend/services/output_service.py`.

## Production notes

The current configuration is suitable for local demo and coursework
submission. For a real deployment:

1. Replace the wildcard CORS origin in `main.py` with the actual
   frontend domain.
2. Set the `--workers` flag on uvicorn to scale beyond 1 process
   (note: the in-memory cache in `output_service` is per-process and
   will have to be replaced with something shared if outputs change at
   runtime, which they don't in the current pre-computed model).
3. Put nginx (or any reverse proxy) in front of uvicorn rather than
   exposing it directly.
4. Mount the `app/` directory through the proxy and let the proxy
   serve static files; the API can drop the StaticFiles mount.
