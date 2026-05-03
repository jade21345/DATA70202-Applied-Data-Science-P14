# Design Notes

This document records the non-obvious decisions made in building the
pipeline. The aim is to make it easy for a reviewer (or a future
maintainer) to understand *why* the code looks the way it does, not
just *what* it does.

## 1. Why municipality-level voter totals, not parish-level

The official AR 2025 export gives registered voters at parish, municipality,
and distrito levels. Naively one would aggregate parish totals up to the
upper-tier district. We do not, for two reasons:

1. **Code drift.** The 6-digit DICOFRE parish codes have changed since
   2013 due to freguesia mergers, and again after 2024. Aggregating
   parish-level `inscritos` against the CAOP 2025 boundary file
   produces ~10 percent of voters falling on orphan codes that no
   longer exist as polygons. Municipality (DTMN, 4-digit) codes are
   stable across these reorganisations.
2. **Authoritative source.** The `AR_2025_Concelho` sheet is the
   official municipality-level total and matches the client's grand
   total of 9,265,493 voters across 308 municipalities. The parish
   sheet sums to 9,251,918 — the 13,575 difference is exactly the
   orphan parish problem above.

The pipeline therefore uses `AR_2025_Concelho` as the authoritative
voter source for all upper-tier work. Parish-level vote data is
retained because the lower-tier (single-member) districting needs
parish granularity, but at that stage we accept a small data loss on
parishes that no longer exist in the CAOP boundary.

## 2. Tier-split rounding rule: floor

The Methodology document states `P_i = round(0.7 * S_i)`, but the
client's RESULTS_2025.xlsx implements `P_i = floor(0.7 * S_i)`. The
discrepancy is real and material: `round` produces 69 single-member
seats nationally; `floor` produces 74 (= mainland 70 + islands 4).

We default to `floor` because:
- It reproduces the client spreadsheet exactly.
- It enforces the principle that party-list never exceeds 70 percent.
- It tends to give small districts an extra single-member seat, which
  improves local representation in low-magnitude constituencies.

The rule is configurable (`tier_split_rounding` in scenario_config.json)
so that `round` or `ceil` can be used for sensitivity analysis. The
report should explicitly call out which rule was used.

## 3. Merge rules: configuration not code

The three merge groups (Trás-os-Montes, Alentejo, Beira Baixa) are not
nor easily discoverable from data alone. They reflect Portuguese
historical-geographic identity (Trás-os-Montes, Alentejo) plus a
pragmatic decision (Beira Baixa, where Castelo Branco and Guarda are
both small but Guarda would historically be called "Beira Alta").

Rather than hardcode the names, the pipeline reads the groups from
`config/scenario_config.json` and applies them mechanically. To support
a future request for algorithmic merging, the config also carries a
`merge_strategy` field with reserved values `auto_threshold` and
`auto_nuts3` that currently raise `NotImplementedError`. Adding either
implementation is one new function in `upper_redesign.py` plus a
dispatch line.

## 4. Algorithmic Lisboa/Porto split vs client's manual map

The client's RESULTS_2025.xlsx uses a manual municipality-to-subdistrict
mapping. Our pipeline instead runs a balanced contiguous partition
algorithm on the municipalities of each large distrito (k=3 for Lisboa,
k=2 for Porto), using registered voters as the balance target.

Why algorithmic and not manual:
- The project requirement is to support multiple election years; manual
  mappings would silently become wrong as voter populations shift.
- The algorithm produces a deterministic, auditable result given a
  fixed `random_state`.

Trade-off: the algorithm finds **more voter-balanced** splits than the
client's manual mapping (Porto 1/2 are 793k/798k vs client's 787k/804k),
but does not respect cultural-geographic groupings the way a human
would (e.g. our Lisboa 2 contains both Lisboa city and the agricultural
north of the distrito). For the 2025 baseline this means Lisboa 1 has
15 mandates rather than the client's 16, and Lisboa 3 has 16 rather
than 15. **Total Lisboa mandates are preserved (47 either way) so this
does not affect the national total.**

If the client requires the manual mapping to be reproduced exactly,
the simplest fix is to add a `manual_map` field to the relevant
`SplitRule` and dispatch to a manual-assignment branch. The
infrastructure for that is in place (the `method` field of `SplitRule`)
but the manual branch is intentionally not implemented for now.

## 5. Adjacency: Queen + virtual bridges for islands

Adjacency graphs use Queen contiguity (parishes share at least one
boundary point) computed via a Shapely STRtree spatial index. This
matches the existing Assignment1 notebook and is the standard for
spatial weights work.

Because the Azorean and Madeiran archipelagos consist of islands that
do not touch each other, a single Queen graph over the country has
multiple disconnected components. The graph builder optionally adds
one **virtual edge** per orphan component, joining it to its nearest
parish in the largest component. These edges are flagged with a
`virtual=True` attribute so downstream code can detect them.

For the upper-tier work (which collapses Madeira and Açores into single
districts) virtual bridges are not needed. They become important for
lower-tier districting if/when we run the algorithm inside the islands.

## 6. Why a `parish_id` aliased to DICOFRE rather than a new ID

DICOFRE codes are 6-digit strings, stable across many years for any
given parish that has not been reorganised, and embed the
distrito (2-digit) and municipality (4-digit) prefixes. A separate
synthetic ID would buy nothing and would force every join to go
through a translation table. The pipeline therefore keeps DICOFRE as
the canonical `parish_id`, with the explicit promise that any code
that drops the leading zero (e.g. `10101` for `010101`) is a bug.
