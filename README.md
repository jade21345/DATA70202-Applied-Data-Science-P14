# Portugal Mixed-Member Electoral Model — Algorithm Pipeline

Static-website pipeline simulating a proposed two-tier mixed-member
proportional representation system for Portuguese legislative elections.

This repository contains the offline Python pipeline that reads CAOP
2025 administrative geometries and AR 2025 election data, computes the
redesigned upper-tier districts, allocates seats with Hamilton + D'Hondt,
and emits frontend-ready CSV/GeoJSON files for the static-site team.

## Methodology summary

1. **District redesign** — original distritos are reshaped into
   redesigned upper-tier districts via three operations:
   permanent merging (Madeira, Açores), configured merging
   (Trás-os-Montes, Alentejo, Beira Baixa), and algorithmic splitting
   (Lisboa into 3, Porto into 2, balanced on registered voters).
2. **Hamilton apportionment** — 226 mandates are distributed across the
   redesigned districts in proportion to registered voters (largest
   remainder method).
3. **Tier split** — each district's mandates are split into party-list
   seats `P_i = floor(0.7 * S_i)` and single-member seats `U_i = S_i - P_i`.
   In 2025 this gives 152 party-list and 74 single-member seats
   (mainland 70 + islands 4).
4. **D'Hondt allocation** — each district's party-list seats are
   distributed among parties in proportion to votes (highest averages).
5. **Lower-tier districting** *(WIP)* — within each upper-tier district,
   parishes are aggregated into `U_i` contiguous single-member districts
   of approximately equal voters.

## Repository layout

```
project_root/
├── config/
│   └── scenario_config.json         scenario parameters and rules
├── data_raw/                        input data (gitignored)
│   ├── *.gpkg                       CAOP 2025 geopackages
│   └── *.xlsx                       AR 2025 results
├── data_clean/                      ETL outputs (intermediate)
├── outputs/                         frontend deliverables
│   ├── geojson/
│   ├── tables/
│   ├── json/
│   └── documentation/
├── scripts/
│   ├── 01_prepare_data.py           clean and unify raw data
│   ├── 02_run_apportionment.py      Hamilton + tier split + redesign
│   └── 03_validate_against_client.py compare to RESULTS_2025.xlsx
├── src/
│   ├── config.py                    JSON config loader
│   ├── io_utils.py                  data loaders, GIS, CSV
│   ├── apportionment.py             Hamilton, tier split, D'Hondt
│   ├── upper_redesign.py            merge + split rules
│   ├── spatial_utils.py             adjacency graphs, balanced partition
│   └── validation.py                schema and invariant checks
├── tests/
│   └── test_apportionment.py        pytest suite
└── docs/
    ├── design_notes.md              architecture decisions
    └── notes_zh.md                  中文模块笔记 (internal)
```

## Setup

```bash
pip install pandas geopandas networkx numpy openpyxl pyogrio shapely
pip install pytest         # for tests
```

Place the four CAOP 2025 geopackages and the AR 2025 spreadsheets under
`data_raw/`. See `config/scenario_config.json` for expected file names.

## Run

```bash
python scripts/01_prepare_data.py
python scripts/02_run_apportionment.py
python scripts/03_validate_against_client.py
```

Each script logs progress to stdout and writes outputs to disk. Module
functions are pure and importable from `src/` for use in notebooks.

## Validation status

Against the client's `RESULTS_2025.xlsx` ground truth (run on
2025 data, `tier_split_rounding=floor`):

| Metric | Pipeline | Client | Match |
|---|---|---|---|
| Total mandates | 226 | 226 | yes |
| Total single-member seats | 74 | 74 | yes |
| Mainland single-member | 70 | 70 | yes |
| Island single-member | 4 | 4 | yes |
| Districts with matching mandates | 17/19 | | partial |
| Non-split districts (15 of 19) | exact match | | yes |

The two non-matching districts are Lisboa 1 and Lisboa 3, where the
algorithm chooses a different (more voter-balanced) split of the
Lisboa distrito than the client's manual mapping. Total mandates are
preserved (15 + 16 + 16 = 47 = client total). See
`docs/design_notes.md` for the trade-off discussion.

## Configuration

All policy choices live in `config/scenario_config.json`:

- `total_seats`, `party_list_ratio`, `tier_split_rounding`
- `upper_tier_redesign.merge_groups` — which distritos merge
- `upper_tier_redesign.split_rules` — which distritos split, into k pieces
- `upper_tier_redesign.always_merged` — Madeira and Açores
- `lower_tier.skip_districts`, `tolerance`, etc.

Changing any of these does not require code edits. Adding a new
*algorithm* (e.g. an automatic merge-discovery heuristic) does require
code; the config keeps a `merge_strategy` field that can dispatch to
future implementations.

## License

Project for academic use (DATA70202 Applied Data Science, Manchester).
