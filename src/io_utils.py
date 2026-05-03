"""IO utilities for the Portugal mixed-member model pipeline.

This module owns the boundary between raw vendor files (CAOP geopackages,
official election spreadsheets) and the project's internal canonical
schemas. Downstream modules should never read raw files directly; they go
through these loaders so that schema normalisation happens in exactly one
place.
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger(__name__)


# Layer names in each CAOP2025 geopackage. The naming follows the CAOP
# convention: cont_* for mainland, ram_* for Madeira, raa_cen_ori_* for
# Central+Eastern Azores, raa_oci_* for Western Azores.
GPKG_LAYERS = {
    "continente": "cont_freguesias",
    "madeira": "ram_freguesias",
    "acores_central_eastern": "raa_cen_ori_freguesias",
    "acores_western": "raa_oci_freguesias",
}

# Columns kept from each freguesia layer when building the unified
# parishes geodataframe. All four CAOP2025 sub-files expose the same
# field names for these columns.
PARISH_KEEP_COLS = [
    "dtmnfr",          # 6-digit DICOFRE code: dd mm nfr
    "freguesia",       # parish name
    "municipio",       # municipality name
    "distrito_ilha",   # original distrito or island name
    "nuts3_cod",
    "nuts3",
    "nuts2",
    "nuts1",
    "geometry",
]


def load_freguesias_unified(
    continente_path: str | Path,
    madeira_path: str | Path,
    acores_co_path: str | Path,
    acores_w_path: str | Path,
    target_crs: str = "EPSG:3763",
) -> gpd.GeoDataFrame:
    """Load all four CAOP2025 geopackages and return a single GeoDataFrame
    of parishes (freguesias) covering all of Portugal.

    Each source file uses a different projected CRS (EPSG:3763 for the
    mainland, EPSG:5016 for Madeira, EPSG:5015 / 5014 for Azores). All
    layers are reprojected to ``target_crs`` and concatenated.

    Parameters
    ----------
    continente_path, madeira_path, acores_co_path, acores_w_path : Path
        Paths to the four CAOP2025 geopackage files.
    target_crs : str
        EPSG code that all layers will be reprojected to. EPSG:3763
        (ETRS89 / PT-TM06) is the standard mainland projection and the
        small distortion it introduces on the islands is acceptable for
        district-level work (parish centroids only ever differ by a few
        metres, which does not affect adjacency or area-based decisions).

    Returns
    -------
    GeoDataFrame
        One row per parish, columns in PARISH_KEEP_COLS, CRS = target_crs.
    """
    logger.info("Loading mainland freguesias from %s", continente_path)
    cont = gpd.read_file(continente_path, layer=GPKG_LAYERS["continente"])

    logger.info("Loading Madeira freguesias from %s", madeira_path)
    ram = gpd.read_file(madeira_path, layer=GPKG_LAYERS["madeira"])

    logger.info("Loading Azores Central+Eastern freguesias from %s", acores_co_path)
    raa_co = gpd.read_file(acores_co_path, layer=GPKG_LAYERS["acores_central_eastern"])

    logger.info("Loading Azores Western freguesias from %s", acores_w_path)
    raa_w = gpd.read_file(acores_w_path, layer=GPKG_LAYERS["acores_western"])

    parts = []
    for label, gdf in [
        ("continente", cont),
        ("madeira", ram),
        ("acores_central_eastern", raa_co),
        ("acores_western", raa_w),
    ]:
        if str(gdf.crs) != target_crs:
            gdf = gdf.to_crs(target_crs)
        # Defensive: not every layer might have every optional column; subset.
        present = [c for c in PARISH_KEEP_COLS if c in gdf.columns]
        if "dtmnfr" not in present or "geometry" not in present:
            raise ValueError(
                f"Layer '{label}' is missing required columns dtmnfr/geometry. "
                f"Found: {list(gdf.columns)}"
            )
        parts.append(gdf[present])

    combined = pd.concat(parts, ignore_index=True)
    combined = gpd.GeoDataFrame(combined, crs=target_crs, geometry="geometry")

    # Normalise DICOFRE: strip whitespace, ensure 6-digit zero-padded string.
    combined["dtmnfr"] = (
        combined["dtmnfr"].astype(str).str.strip().str.zfill(6)
    )

    # Sanity: DICOFRE must be unique nationwide.
    dup = combined["dtmnfr"].duplicated()
    if dup.any():
        bad = combined.loc[dup, "dtmnfr"].head(5).tolist()
        raise ValueError(
            f"Duplicate DICOFRE codes after merging the four CAOP files: "
            f"{bad}. This is unexpected; check source data."
        )

    logger.info(
        "Loaded %d parishes total (continente=%d, madeira=%d, acores=%d)",
        len(combined), len(cont), len(ram), len(raa_co) + len(raa_w),
    )
    return combined


def load_official_results(
    xlsx_path: str | Path,
    sheet: str = "AR_2025_Freguesia",
    header_row: int = 3,
) -> pd.DataFrame:
    """Load the parish-level election results from the official spreadsheet.

    Returns a long-format DataFrame with columns:
        codigo, nome_territorio, inscritos, party, votes

    The official spreadsheet stores parties as columns; this function
    melts them into rows so downstream aggregation is uniform.

    Parameters
    ----------
    xlsx_path : Path
        Path to the official spreadsheet (e.g. AR_2025_Globais...xlsx).
    sheet : str
        Sheet name containing parish-level results. Defaults to the AR
        2025 sheet name.
    header_row : int
        0-indexed row number containing column headers. The official
        export has 3 header rows; the actual headers are on row index 3.
    """
    logger.info("Loading official results from %s [%s]", xlsx_path, sheet)
    df = pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row)

    # Identify metadata columns (everything that is not a party).
    # The standard metadata columns in the AR exports are:
    META_COLS = {"código", "nome do território", "inscritos"}
    # Some other metadata columns may exist (e.g. "votantes", "brancos",
    # "nulos") but we only need party votes here.
    NON_PARTY_COLS = META_COLS | {
        "votantes", "brancos", "nulos", "% votantes",
        "abstenção", "subscritos", "% subscritos",
    }

    party_cols = [c for c in df.columns if c not in NON_PARTY_COLS]

    long = df.melt(
        id_vars=["código", "nome do território", "inscritos"],
        value_vars=party_cols,
        var_name="party",
        value_name="votes",
    )

    # Drop rows where votes is NaN (party did not run in that parish).
    long = long.dropna(subset=["votes"]).copy()
    long["votes"] = long["votes"].astype(float).astype(int)

    # Drop the national summary row, if present.
    long = long[long["nome do território"] != "Território Nacional"].copy()

    # Normalise código to 6-digit zero-padded string for parish-level rows.
    long["código"] = long["código"].astype(str).str.replace(r"\.0$", "", regex=True)
    long["código"] = long["código"].str.replace(r"\D", "", regex=True)
    long["código"] = long["código"].str.zfill(6)

    long = long.rename(columns={
        "código": "codigo",
        "nome do território": "nome_territorio",
    })

    # inscritos may be string with commas in some exports.
    long["inscritos"] = (
        long["inscritos"].astype(str).str.replace(",", "").astype(float).astype(int)
    )

    long = long.reset_index(drop=True)
    logger.info(
        "Loaded %d (parish, party) rows for %d parishes",
        len(long), long["codigo"].nunique(),
    )
    return long


def load_municipality_voters(
    xlsx_path: str | Path,
    sheet: str = "AR_2025_Concelho",
    header_row: int = 3,
) -> pd.DataFrame:
    """Load municipality-level registered voters from the official spreadsheet.

    Returns a DataFrame with columns:
        municipality_id  (4-digit DTMN code, e.g. '0101' for Águeda)
        municipality_name
        registered_voters

    Why a separate loader: the municipality sheet is the authoritative
    source for ``inscritos`` because parish-level codes drift between
    elections (freguesia mergers since 2013), but DTMN codes are stable.
    Aggregating parish-level inscritos can undercount voters by ~10
    percent due to orphan parish rows.
    """
    logger.info("Loading municipality voters from %s [%s]", xlsx_path, sheet)
    df = pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row)

    # Filter out non-municipality rows (totals, blank rows, footer dates).
    df = df[df["nome do território"].notna()].copy()
    df = df[df["nome do território"] != "Território Nacional"].copy()

    # Coerce código to 6-digit string. Parish-level rows are 6 digits;
    # municipality rows in this sheet should also be 6 digits where the
    # last 2 are 00. Strip non-digits.
    df["código"] = (
        df["código"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"\D", "", regex=True)
        .str.zfill(6)
    )
    df = df[df["código"].str.len() == 6].copy()
    df = df[df["código"] != "000000"].copy()  # drop the placeholder national row

    df["inscritos"] = (
        df["inscritos"].astype(str).str.replace(",", "").astype(float).astype(int)
    )

    out = pd.DataFrame({
        "municipality_id": df["código"].str[:4],
        "municipality_name": df["nome do território"],
        "registered_voters": df["inscritos"],
    }).drop_duplicates("municipality_id").reset_index(drop=True)

    logger.info("Loaded %d municipalities with voter totals", len(out))
    return out



def filter_to_parish_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only rows with 6-digit codes (parish-level), discarding
    aggregates at municipality, distrito, country, or consulado levels.

    Accepts either ``codigo`` or ``parish_id`` as the code column.
    """
    code_col = "codigo" if "codigo" in df.columns else "parish_id"
    return df[df[code_col].astype(str).str.len() == 6].copy()


def voters_by_parish(votes_long: pd.DataFrame) -> pd.DataFrame:
    """Extract the unique (parish_id, parish_name, registered_voters) table
    from the long-format vote table.

    Accepts either canonical (parish_id, parish_name) or raw
    (codigo, nome_territorio) column names; auto-renames to canonical.
    """
    df = votes_long.copy()
    if "codigo" in df.columns and "parish_id" not in df.columns:
        df = df.rename(columns={"codigo": "parish_id"})
    if "nome_territorio" in df.columns and "parish_name" not in df.columns:
        df = df.rename(columns={"nome_territorio": "parish_name"})

    out = (
        df.drop_duplicates(subset=["parish_id"])
        [["parish_id", "parish_name", "inscritos"]]
        .rename(columns={"inscritos": "registered_voters"})
        .reset_index(drop=True)
    )
    return out


def write_geojson(gdf: gpd.GeoDataFrame, path: str | Path, export_crs: str = "EPSG:4326") -> None:
    """Write a GeoDataFrame to GeoJSON in the export CRS."""
    out = gdf.to_crs(export_crs) if str(gdf.crs) != export_crs else gdf
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(out_path, driver="GeoJSON")
    logger.info("Wrote GeoJSON: %s (%d features)", out_path, len(out))


def write_csv(df: pd.DataFrame, path: str | Path) -> None:
    """Write a DataFrame to CSV with consistent settings."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8")
    logger.info("Wrote CSV: %s (%d rows)", out_path, len(df))
