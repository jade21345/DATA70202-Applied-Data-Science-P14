# Frontend API Guide — for Jade

This guide explains how to consume the algorithm pipeline's results
through the backend HTTP API. It is written for Jade who is building
the static frontend (Leaflet maps, charts, tables) and wants to keep
the JavaScript focused on presentation rather than data wrangling.

## TL;DR

Start the backend from the project root:

```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Then open `http://localhost:8000/` — the backend serves `app/`
directly, so the existing `index.html`, `script.js`, `style.css` are
loaded as before. API endpoints sit under `/api/*` on the same origin,
so no CORS headaches and no separate dev server.

Interactive API explorer: `http://localhost:8000/docs`

---

## 1. Why the API instead of fetching files?

You used to do this:

```js
fetch("portugal_district.geojson")
  .then(res => res.json())
  .then(data => { /* ... */ });
```

That works for one map. The problem comes when:

- Filenames change (`portugal_district.geojson` → `upper_districts.geojson`)
- You need to join party metadata (id, name, colour) to vote results
- You want to support more than one scenario (e.g. baseline_2025 vs an alternative)
- You need to display data-quality warnings the algorithm produced

The API takes care of all of this. You call `fetch(/api/...)` and get a
clean JSON shape that already has every field you need.

---

## 2. Endpoint list

All endpoints are `GET` and return JSON unless noted. `<id>` is a scenario
id like `baseline_2025`. The current scenario list is at `/api/scenarios`.

| Endpoint | Returns |
|---|---|
| `/api/health` | Backend status |
| `/api/scenarios` | List of scenarios |
| `/api/scenarios/<id>/config` | Scenario config (year, total seats, methods, ratios) |
| `/api/scenarios/<id>/parties` | Party metadata (id, name, colour) |
| `/api/scenarios/<id>/maps/upper-districts` | GeoJSON of the 19 upper-tier districts |
| `/api/scenarios/<id>/maps/lower-districts` | GeoJSON of the 74 single-member districts |
| `/api/scenarios/<id>/results/final` | National party seat totals |
| `/api/scenarios/<id>/results/hamilton` | Hamilton apportionment table |
| `/api/scenarios/<id>/results/tier-split` | Per-district party-list vs single-member seats |
| `/api/scenarios/<id>/results/dhondt` | D'Hondt seat allocation per (district, party) |
| `/api/scenarios/<id>/results/single-member-winners` | Winning party in each lower-tier district |
| `/api/scenarios/<id>/diagnostics` | Validation status and data-quality warnings |

---

## 3. Page-to-endpoint mapping

If your site has these sections (matching the original brief), here is
which endpoint each one calls:

| Page section | Endpoint(s) |
|---|---|
| Method overview | `/config` |
| Upper-tier map | `/maps/upper-districts` |
| Lower-tier map | `/maps/lower-districts` |
| Hamilton table | `/results/hamilton` |
| Tier split table | `/results/tier-split` |
| D'Hondt result table | `/results/dhondt` |
| Single-member winner map / table | `/maps/lower-districts` (for map) + `/results/single-member-winners` (for table) |
| Final parliament chart | `/results/final` |
| Data-quality banner | `/diagnostics` |
| Party legend / colours | `/parties` |

---

## 4. Helper module to drop into `app/`

Save this as `app/api.js` and include it before `script.js` with
`<script src="api.js"></script>`. It centralises the scenario id and
gives you typed-ish helpers.

```js
// app/api.js
const SCENARIO_ID = "baseline_2025";
const API_BASE = "/api";

async function api(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    throw new Error(`API ${path} -> ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

const PortugalAPI = {
  scenarioId: SCENARIO_ID,

  config:           ()  => api(`/scenarios/${SCENARIO_ID}/config`),
  parties:          ()  => api(`/scenarios/${SCENARIO_ID}/parties`),
  upperDistricts:   ()  => api(`/scenarios/${SCENARIO_ID}/maps/upper-districts`),
  lowerDistricts:   ()  => api(`/scenarios/${SCENARIO_ID}/maps/lower-districts`),
  finalResults:     ()  => api(`/scenarios/${SCENARIO_ID}/results/final`),
  hamilton:         ()  => api(`/scenarios/${SCENARIO_ID}/results/hamilton`),
  tierSplit:        ()  => api(`/scenarios/${SCENARIO_ID}/results/tier-split`),
  dhondt:           ()  => api(`/scenarios/${SCENARIO_ID}/results/dhondt`),
  singleMemberWinners: () => api(`/scenarios/${SCENARIO_ID}/results/single-member-winners`),
  diagnostics:      ()  => api(`/scenarios/${SCENARIO_ID}/diagnostics`),
};
```

---

## 5. Replacing the existing map fetch

The current `script.js` does this:

```js
fetch("portugal_district.geojson")
  .then(res => res.json())
  .then(data => {
    const geojson = L.geoJSON(data, { style, onEachFeature }).addTo(map);
  });
```

The minimal change to use the API is:

```js
PortugalAPI.upperDistricts().then(data => {
  const geojson = L.geoJSON(data, { style, onEachFeature }).addTo(map);
});
```

The shape Leaflet sees is identical (RFC 7946 GeoJSON FeatureCollection),
so `style` and `onEachFeature` keep working. The only differences are in
`feature.properties`:

**Before** (the old static GeoJSON had whatever fields were authored):
- `feature.properties.name` — district name

**After** (every feature has at least these fields):
- `feature.properties.upper_district_id` — slug like `"lisboa_1"`
- `feature.properties.upper_district_name` — display name like `"Lisboa 1"`
- `feature.properties.registered_voters` — int
- `feature.properties.total_mandates` — int
- `feature.properties.party_list_seats` — int
- `feature.properties.single_member_seats` — int
- `feature.properties.dominant_party_id` — slug of party with most party-list seats
- `feature.properties.dominant_party` — raw party id (for cross-reference)
- `feature.properties.dominant_party_seats` — int
- `feature.properties.scenario_id` — `"baseline_2025"`

So the existing tooltip becomes:

```js
mouseover: function (e) {
  const p = feature.properties;
  layer.bindTooltip(
    `<b>${p.upper_district_name}</b><br>` +
    `Voters: ${p.registered_voters.toLocaleString()}<br>` +
    `Total mandates: ${p.total_mandates}<br>` +
    `Party-list: ${p.party_list_seats}, ` +
    `Single-member: ${p.single_member_seats}`
  ).openTooltip();
  layer.setStyle({ fillColor: "#15a821" });
}
```

You no longer need the hardcoded `stats = { "Lisboa": "Turnout: 68%" }`
table — the data is on every feature.

---

## 6. Lower-tier map example (with party colour fill)

The lower-tier GeoJSON includes the winning party and its colour as
properties, so the fill style can be a one-liner:

```js
function lowerStyle(feature) {
  return {
    color: "#333",
    weight: 0.5,
    fillColor: feature.properties.winner_party_color || "#cccccc",
    fillOpacity: 0.7,
  };
}

PortugalAPI.lowerDistricts().then(data => {
  L.geoJSON(data, {
    style: lowerStyle,
    onEachFeature: (feature, layer) => {
      const p = feature.properties;
      const partyName = p.winner_party_short_name || "No data";
      layer.bindTooltip(
        `<b>${p.lower_district_name}</b><br>` +
        `Winner: ${partyName}<br>` +
        `Margin: ${p.margin.toLocaleString()} votes (${(p.margin_pct * 100).toFixed(1)}%)`
      );
    },
  }).addTo(map);
});
```

---

## 7. Final results (parliament chart)

```js
PortugalAPI.finalResults().then(payload => {
  console.log(`${payload.allocated_seats} of ${payload.total_seats} seats allocated`);
  payload.data.forEach(p => {
    console.log(`${p.abbreviation}: ${p.total_seats} seats (${(p.seat_share * 100).toFixed(1)}%)`);
  });
  // Hand to Chart.js / D3 / whatever
});
```

Each row in `payload.data` has:

```json
{
  "party_id": "psd_cds",
  "party_name": "PSD/CDS coalition",
  "abbreviation": "PSD-CDS",
  "party_colour": "#FF8C00",
  "party_list_seats": 62,
  "single_member_seats": 55,
  "total_seats": 117,
  "seat_share": 0.52
}
```

`party_colour` matches the colours used in the lower-tier GeoJSON, so a
parliament bar chart and the lower-tier map will be visually consistent
without any manual mapping.

---

## 8. Data-quality banner

The diagnostics endpoint returns a status (`valid` | `valid_with_warnings`
| `invalid`) and a list of checks. A simple banner:

```js
PortugalAPI.diagnostics().then(d => {
  if (d.status === "valid") return;
  const issues = d.checks.filter(c => !c.passed || c.severity === "warning");
  if (!issues.length) return;
  const banner = document.createElement("div");
  banner.className = "data-quality-banner";
  banner.innerHTML = issues.map(c =>
    `<div class="warning">⚠ ${c.message}</div>`
  ).join("");
  document.body.prepend(banner);
});
```

There are currently four expected warnings on the 2025 scenario. They
are *informational* (data quality, not bugs) and you can choose to
display them or filter them out.

---

## 9. Bilingual content

Your existing `toggleLanguage()` walks `[data-en]` / `[data-pt]`
attributes. The API returns Portuguese names natively (`Açores`,
`Lisboa`, `Trás-os-Montes`, party display names) — they are
ready to render. If you need an English label for a Portuguese-only
field, add a small map in JS rather than expecting it from the API:

```js
const EN_LABEL = {
  "Açores": "Azores",
  "Madeira": "Madeira",
  "Trás-os-Montes": "Trás-os-Montes",  // no English equivalent
  "Beira Baixa": "Beira Baixa",
  "Alentejo": "Alentejo",
};
function display(name) {
  return currentLang === "en" ? (EN_LABEL[name] || name) : name;
}
```

---

## 10. Minimal `index.html` skeleton

```html
<!DOCTYPE html>
<html>
<head>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <div id="map" style="height: 600px;"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="api.js"></script>
  <script src="script.js"></script>
</body>
</html>
```

That's all. `api.js` provides `PortugalAPI`, `script.js` calls the
methods on it.

---

## 11. Common gotchas

- The backend serves `app/` at `/`, so always use **relative API paths**
  (`/api/...` or build via `API_BASE` constant). Don't hardcode
  `http://localhost:8000` — that breaks when the site is deployed.
- All map data is in **EPSG:4326** (lat/lon WGS84), which Leaflet
  expects natively. Don't pass through any reprojection helpers.
- `winner_party_id` is `null` for lower districts where the underlying
  parishes have no vote data (currently only Porto 1 - SM 6). Always
  guard with `feature.properties.winner_party_color || "#cccccc"`.
- Some parties in the AR 2025 source data have raw ids with
  punctuation (`PPD/PSD.CDS-PP`). The API responses always use the
  slug-style `party_id` (e.g. `psd_cds`) for keys and CSS classes;
  the raw id is preserved as `party_id_raw` only on `/parties`.

---

## 12. Local dev workflow

```bash
# Terminal 1: keep the algorithm pipeline outputs current
python scripts/04_run_full_pipeline.py

# Terminal 2: run the backend (auto-reloads on Python changes)
uvicorn backend.main:app --reload --port 8000

# Browser
open http://localhost:8000/             # Jade's frontend
open http://localhost:8000/docs         # Swagger API explorer
```

Frontend changes (in `app/`) reload on browser refresh; backend changes
reload automatically thanks to `--reload`.

If you ever want to confirm what an endpoint actually returns, the
fastest way is:

```bash
curl -s http://localhost:8000/api/scenarios/baseline_2025/results/final | jq
```
