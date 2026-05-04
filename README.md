# Portugal Mixed-Member Electoral Model — Algorithm + Backend

Static-website pipeline simulating a proposed two-tier mixed-member
proportional representation system for Portuguese legislative elections.

This repository contains:
- An offline Python pipeline that processes CAOP 2025 administrative
  geometries and AR 2025 election data, computes the redesigned
  upper-tier districts, and runs Hamilton + D'Hondt + lower-tier
  districting.
- A FastAPI backend that exposes the pre-computed scenario outputs as
  a clean HTTP API for the static frontend.
- Sample scripts and tests covering both layers.

## Methodology summary

1. **District redesign** — original distritos are reshaped into
   redesigned upper-tier districts via three operations:
   permanent merging (Madeira, Açores), configured merging
   (Trás-os-Montes, Alentejo, Beira Baixa), and algorithmic splitting
   (Lisboa into 3, Porto into 2, balanced on registered voters).
2. **Hamilton apportionment** — 226 mandates are distributed across
   the 19 redesigned districts in proportion to registered voters
   (largest remainder method).
3. **Tier split** — each district's mandates are split into party-list
   seats `P_i = floor(0.7 * S_i)` and single-member seats
   `U_i = S_i - P_i`. In 2025 this gives 152 party-list and 74
   single-member seats (mainland 70 + islands 4).
4. **D'Hondt allocation** — each district's party-list seats are
   distributed among parties in proportion to votes (highest averages).
5. **Lower-tier districting** — within each upper-tier district,
   parishes are aggregated into `U_i` contiguous lower-tier districts
   of approximately equal voters via seed-based region-growing.
6. **Single-member winners** — without candidate-level data, the
   party with the most votes in each lower-tier district is treated
   as the winner (a counterfactual simplifying assumption documented
   in every relevant output).

## Repository layout

```
project_root/
├── config/
│   ├── scenario_config.json     pipeline parameters
│   └── parties.csv              party metadata (id, slug, name, colour)
├── data_raw/                    input data (gitignored)
├── data_clean/                  ETL outputs (intermediate)
├── outputs/
│   └── scenarios/
│       └── baseline_2025/
│           ├── geojson/         upper_districts, lower_districts
│           ├── tables/          all CSV outputs
│           ├── json/            scenario_summary, parliament JSON
│           └── documentation/
├── scripts/
│   ├── 01_prepare_data.py       clean and unify raw data
│   ├── 02_run_apportionment.py  Hamilton + tier split only
│   ├── 03_validate_against_client.py
│   ├── 04_run_full_pipeline.py  full end-to-end pipeline
│   ├── run_server.sh            start backend (Linux/macOS)
│   └── run_server.bat           start backend (Windows)
├── src/
│   ├── config.py                JSON config loader
│   ├── io_utils.py              data loaders, GIS, CSV
│   ├── apportionment.py         Hamilton, tier split, D'Hondt
│   ├── upper_redesign.py        merge + split rules
│   ├── lower_districting.py     seed-based region growing
│   ├── spatial_utils.py         adjacency graphs, balanced partition
│   ├── vote_aggregation.py      parish -> district vote rollup
│   ├── results.py               combine into final party totals
│   ├── slugs.py                 name -> URL/JSON-friendly slug
│   └── validation.py            schema and invariant checks
├── backend/
│   ├── main.py                  FastAPI app + CORS + static mount
│   ├── api/                     scenarios, results, maps, diagnostics
│   ├── services/                file loaders + integrity checks
│   ├── schemas/                 Pydantic response models
│   └── README.md
├── app/                         frontend (Jade's domain)
├── tests/
│   ├── test_apportionment.py    algorithm tests
│   └── test_backend.py          backend integration tests
├── docs/
│   ├── design_notes.md          architecture decisions (English)
│   ├── frontend_api_guide.md    API reference for the frontend team
│   └── notes_zh.md              中文笔记 (internal)
└── requirements.txt
```

## Quick start

```bash
pip install -r requirements.txt

# Place the four CAOP 2025 geopackages and AR 2025 spreadsheets in data_raw/
# (see config/scenario_config.json for the exact filenames).

python scripts/01_prepare_data.py             # clean and unify raw data (~5s)
python scripts/04_run_full_pipeline.py        # full pipeline (~25s)
python scripts/03_validate_against_client.py  # cross-check vs client RESULTS_2025

# Start the backend + frontend
./scripts/run_server.sh                       # Linux/macOS
scripts\run_server.bat                        # Windows
```

Then open `http://localhost:8000/`. The frontend (`app/`) and the API
(`/api/*`) share one origin.

## Validation status

Apportionment-side validation against the client's `RESULTS_2025.xlsx`
ground truth (2025 data, `tier_split_rounding=floor`):

| Metric | Pipeline | Client | Match |
|---|---|---|---|
| Total mandates | 226 | 226 | yes |
| Total single-member seats | 74 | 74 | yes |
| Mainland single-member | 70 | 70 | yes |
| Island single-member | 4 | 4 | yes |
| Districts with matching mandates | 17/19 | | partial |
| Single-member seat counts per district | 19/19 match | | yes |

The two mandate mismatches (Lisboa 1 = 15 vs client 16; Lisboa 3 = 16
vs client 15) reflect the algorithm's voter-balanced sub-district
choice versus the client's manual mapping. Total Lisboa mandates (47)
are preserved.

End-to-end pipeline produces:
- 19 redesigned upper-tier districts
- 74 lower-tier (single-member) districts
- 152 party-list seats allocated by D'Hondt
- 71/74 lower districts contiguous (3 island districts use virtual bridges)
- 1 lower district has no recorded votes (data quality issue,
  documented in `/api/.../diagnostics`)

## API

The backend exposes a read-only HTTP API. See
`docs/frontend_api_guide.md` for full documentation and
`http://localhost:8000/docs` for the interactive Swagger UI.

| Endpoint | Returns |
|---|---|
| `/api/health` | Backend status |
| `/api/scenarios` | List of scenarios |
| `/api/scenarios/{id}/config` | Scenario config (year, total seats, methods) |
| `/api/scenarios/{id}/parties` | Party metadata (id, name, colour) |
| `/api/scenarios/{id}/maps/upper-districts` | Upper-tier polygons GeoJSON |
| `/api/scenarios/{id}/maps/lower-districts` | Lower-tier polygons GeoJSON with winners |
| `/api/scenarios/{id}/results/final` | National party seat totals |
| `/api/scenarios/{id}/results/hamilton` | Hamilton apportionment table |
| `/api/scenarios/{id}/results/tier-split` | Per-district party-list / single-member split |
| `/api/scenarios/{id}/results/dhondt` | D'Hondt allocations per (district, party) |
| `/api/scenarios/{id}/results/single-member-winners` | Winners and margins |
| `/api/scenarios/{id}/diagnostics` | Validation status and warnings |

All responses use slug-style ids (e.g. `psd_cds`, `lisboa_1`) for
machine-friendly keys; display names are kept as separate fields.

## Configuration

All policy choices live in `config/scenario_config.json`:

- `total_seats`, `party_list_ratio`, `tier_split_rounding`
- `upper_tier_redesign.merge_groups` — which distritos merge
- `upper_tier_redesign.split_rules` — which distritos split, into k pieces
- `upper_tier_redesign.always_merged` — Madeira and Açores
- `lower_tier.skip_districts`, `tolerance`, `seed_strategy`, etc.

Changing any of these does not require code edits. Adding a new
*algorithm* (e.g. an automatic merge-discovery heuristic) does require
code; the config keeps a `merge_strategy` field that can dispatch to
future implementations.

## Tests

```bash
pytest tests/ -v
```

25 tests covering Hamilton/tier-split/D'Hondt correctness, schema
validation, and end-to-end backend behaviour.

## License

Project for academic use (DATA70202 Applied Data Science, Manchester).
