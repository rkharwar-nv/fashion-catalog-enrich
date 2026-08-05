# Example: fashion catalog rebuild

Real output from `fashion-catalog-rebuild` over a 218-row merchant CSV. These
files are here to show what the pipeline produces and what its audit trail looks
like — the shapes are the point, not the specific products.

Nothing here is input to anything. The catalog a deployment consumes is produced
by running the pipeline against its own data.

## The run

218 source rows in, **215 products published**, 3 held.

| | |
|---|---:|
| published without dispute | 205 |
| recovered by signal majority | 10 |
| published by reviewed decision | 1 |
| held | 2 |

Of the published records, 3 carry an outlier flag: the classification is sound
but one merchant signal disagrees with it, so the source catalog can be
corrected.

## Files

| File | What it shows |
|---|---|
| `enriched_products.jsonl` | the catalog — 216 records, one per line |
| `reconciliation.csv` | the 20 rows worth a human look: added, held, reclassified, or contested |
| `dropped_products.csv` | the 2 held products, each with a plain-language reason and the fix |
| `rebuild_summary.json` | counts, status breakdown, and the substantive differences |
| `rebuild_ledger.jsonl` | every source row and how it resolved |
| `rebuild_manifest.json` | hashes and versions pinning the run |

### Why `reconciliation.csv` has 20 rows and the ledger has 218

Nearly every carried-over product differs from the previous catalog, because the
enriched prose comes from a different enrichment run. Listing all of those would
bury the rows that actually need attention, so the reconciliation view holds only
substantive changes — added, dropped, reclassified, renamed, or contested — and
the summary counts the rest as `content_only_changes`.

## Worth looking at

- **`dropped_products.csv`** — the two held products are the interesting failure
  modes. One has a name and image that identify different products with no third
  signal to break the tie; the other has no image at all. Neither is a model
  failure, and each names the fix.
- **`reclassified_vs_baseline`** in the summary — three products whose
  classification changed against the previous catalog, including one that the
  previous catalog had filed under the wrong category.
- **The colour flags** in `reconciliation.csv` — one high-confidence flag,
  where a product named "…in Navy" was published as black, and two
  low-confidence ones where the colour names a lens rather than the frame. The
  high one is a real error; the low ones are correct.
- **The outlier flags** in `reconciliation.csv` — products published despite a
  disagreeing merchant signal, with the disagreeing signal named.

## Reproducing

Deterministic: the same inputs always give a byte-identical catalog, and no model
is called. See
[Rebuilding a catalog without re-running enrichment](../../docs/CONFLICT_RESOLUTION.md#rebuilding-a-catalog-without-re-running-enrichment)
for the command.

`rebuild_manifest.json` records absolute paths from the machine that generated
this example, along with the SHA-256 of every input. The hashes are the portable
part; the paths are not.
