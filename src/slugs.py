"""Slug helpers.

Converts human-readable district and party names into URL/JSON-key
friendly identifiers (lowercase ASCII, underscores, no punctuation).

Examples:
    'Lisboa 1'         -> 'lisboa_1'
    'Trás-os-Montes'   -> 'tras_os_montes'
    'Açores'           -> 'acores'
    'Lisboa 2 - SM 3'  -> 'lisboa_2_sm_3'
    'PPD/PSD.CDS-PP'   -> 'ppd_psd_cds_pp'
"""
from __future__ import annotations

import re
import unicodedata


def slugify(name: str) -> str:
    """Return a lowercase ASCII slug for a district or party name.

    The output is suitable for use in URLs, JSON keys, CSS class names,
    and HTML element ids. It is stable: applying slugify twice yields
    the same string. Empty or whitespace-only input returns an empty
    string.
    """
    if name is None:
        return ""
    s = str(name).strip()
    if not s:
        return ""
    # Normalise diacritics: 'Açores' -> 'Acores', 'São Tomé' -> 'Sao Tome'.
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    # Replace any non-alphanumeric run with a single underscore.
    s = re.sub(r"[^a-z0-9]+", "_", s)
    # Collapse leading/trailing underscores.
    s = s.strip("_")
    return s
