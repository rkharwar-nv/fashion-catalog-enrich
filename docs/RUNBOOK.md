# Fashion Catalog Enrichment — Runbook

Everything needed to run this end to end: what goes in, what comes out, how to
configure it, and how to read the results.

- [Two ways to run](#two-ways-to-run)
- [Input](#input)
- [Configuration](#configuration)
- [Running](#running)
- [What happens](#what-happens)
- [Output](#output)
- [Reading the results](#reading-the-results)
- [When a product is held](#when-a-product-is-held)

## Two ways to run

| | Full run | Rebuild |
|---|---|---|
| Command | `python -m fashion_catalog.batch` | `python fashion-catalog-rebuild` |
| Calls a model | yes, one per product | **no** |
| Needs a VLM endpoint | yes | no |
| Reproducible byte-for-byte | no — the model is not deterministic | **yes** |
| Use when | enriching a catalog for the first time, or after the CSV changes | re-deriving a catalog from enrichment you already have, or changing publication rules |

Start with a full run to produce enrichment. Use rebuilds afterwards to change
policy, apply decisions, or reproduce a result without paying for inference.

## Input

### CSV

One row per product, header required, UTF-8 (a BOM is tolerated):

```csv
category,subcategory,name,description,url,price,image
jewelry,bracelet,Southwest Bracelet,"The Southwest Bracelet combines elegance...",/images/Southwest_Bracelet.jpg,169.99,/images/Southwest_Bracelet.jpg
```

| Column | Required | Notes |
|---|---|---|
| `name` | **yes** | missing → `MISSING_REQUIRED_FIELD` |
| `description` | **yes** | missing → `MISSING_REQUIRED_FIELD` |
| `price` | **yes** | must parse as a non-negative number, else `INVALID_PRICE` |
| `image` | **yes** | only the basename is used to find the file |
| `category`, `subcategory` | no | not validated; `subcategory` votes on classification when present |
| `url` | no | carried through unchanged |
| `product_id` / `sku` / `id` | no | **strongly recommended** — see below |

Extra columns are carried through untouched.

> **Supply a stable id.** Without `product_id`, `sku`, or `id`, `record_id` is a
> hash of name + image + url + price + description, so it changes whenever the
> row changes and two identical rows cannot be told apart. A stable id makes ids
> durable across catalog revisions and removes the whole `DUPLICATE_NAME_IMAGE`
> failure mode.

### Images

A directory of image files. Each row's `image` basename must resolve to a
readable, decodable file there. Missing → `IMAGE_NOT_FOUND`; present but corrupt
→ `IMAGE_UNREADABLE`. Both hold the product back; neither is overridable.

Check the inputs before spending inference:

```bash
fashion-catalog-enrich \
  --input-csv products.csv --images-dir images/ \
  --output-dir /tmp/preflight --validate-only
```

That makes no model calls and writes `enrichment_review.csv` listing every row
with an input problem.

## Configuration

The full run uses the repository's configured VLM — `shared/config/config.yaml`,
overridable per-deployment:

| Variable | Purpose |
|---|---|
| `NGC_API_KEY` | credential for the NVIDIA endpoint |
| `VLM_API_BASE_URL` | override the endpoint URL |
| `VLM_MODEL` | override the model |

The rebuild needs no configuration — it calls nothing.

Whatever produced a run is recorded in its `run_manifest.json` (`vlm_endpoint`,
`vlm_model`), so results stay traceable to the model that made them.

## Running

### Full run

```bash
fashion-catalog-enrich \
  --input-csv   products.csv \
  --images-dir  images/ \
  --output-dir  out/run-1 \
  --locale      en-US \
  --decisions   decisions/products_extended.jsonl   # optional
```

`--currency` adds a currency field; `--validate-only` skips enrichment.

### Rebuild

```bash
fashion-catalog-rebuild \
  --input-csv   products.csv \
  --enrichment  out/run-2 \
  --enrichment  out/run-1 \
  --gate-run    out/run-2 \
  --decisions   decisions/products_extended.jsonl \
  --baseline    previous_catalog.jsonl \
  --output-dir  out/rebuild
```

- `--enrichment` is **repeatable, in priority order**. The first run holding an
  enriched description for a row wins. Use this when no single run covers
  everything.
- `--gate-run` names the run whose `eliminated_products.jsonl` says which rows
  were contested. The classification tie-breaker applies only to those — it
  settles disputes, it is not a second filter over rows that were never in
  dispute.
- `--baseline` is optional and marks each row `ADDED`, `UPDATED`, `UNCHANGED` or
  `DROPPED` against a previous catalog.

Both refuse to run if the CSV's SHA-256 does not match what the decision file and
the frozen runs were built against, because row numbers only mean something for
one exact file.

## What happens

1. **Audit** — required fields, price, and image are checked. Failures never
   reach the model.
2. **Enrich** — one VLM call per product, reading the image and the merchant row
   together. Retried up to three times against a schema; an optional attribute
   that cannot be evidenced is marked unknown rather than losing the product.
3. **Classify** — three signals decide the product type: the merchant name, the
   `subcategory` column, and the image. A specific corroborating signal publishes
   it; a contradiction with nothing to break the tie holds it.
4. **Check coherence** — a product whose name states a different product type
   than its category is held, since that is incoherent to a shopper however
   sound the taxonomy.
5. **Publish** — surviving records are written with their evidence trail.

Details in [Conflict Resolution](CONFLICT_RESOLUTION.md).

## Output

### The catalog — `enriched_products.jsonl`

One JSON object per line. Source columns, plus:

```json
{
  "category": "jewelry", "subcategory": "bracelets",
  "name": "Southwest Bracelet",
  "description": "The Southwest Bracelet combines elegance...",
  "url": "/images/Southwest_Bracelet.jpg", "price": "169.99",
  "image": "/images/Southwest_Bracelet.jpg",
  "record_id": "generated:27d88f71a7ed7afe",
  "source_row": 2,
  "primary_color": "gold", "pattern": "geometric",
  "composition": "high-quality crystals and natural stones",
  "jewelry_form": "cuff", "metal_color": "gold_tone",
  "enriched_description": "The Southwest Bracelet is a gold-toned cuff adorned with..."
}
```

`source_row` is the CSV line number, header being line 1.

**Attributes are category-conditional**, and only ones the model could evidence
appear. An absent attribute means "not established", never "false".

| | Attributes |
|---|---|
| all products | `primary_color`, `pattern`, `composition`, `care`, `target_audience` |
| apparel | `neckline`, `sleeve_length`, `garment_length`, `silhouette`, `closure` |
| footwear | `toe_shape`, `heel_type`, `fastening`, `shaft_height` |
| bags | `carry_method`, `bag_closure`, `structure` |
| eyewear | `frame_shape`, `lens_appearance` |
| jewelry | `jewelry_form`, `metal_color` |

### Everything else

| File | Both | Contents |
|---|---|---|
| `enriched_products.jsonl` | ✓ | the catalog |
| `run_manifest.json` / `rebuild_manifest.json` | ✓ | input hashes, versions, model |
| `batch_summary.json` / `rebuild_summary.json` | ✓ | counts |
| `eliminated_products.jsonl` | full run | held products with reason codes |
| `enrichment_review.csv` | full run | per-field evidence, conflicts, omissions |
| `decision_ledger.jsonl` | full run | which decisions were applied, by whom |
| `rebuild_ledger.jsonl` | rebuild | every source row and how it resolved |
| `reconciliation.csv` | rebuild | only rows worth a look |
| `dropped_products.csv` | rebuild | held products with the fix required |

A real example of all of these: [`examples/fashion-catalog/`](../examples/fashion-catalog/).

## Reading the results

Start with the summary, then `dropped_products.csv` (what didn't make it and
why), then `reconciliation.csv` (what changed).

Each ledger row carries a `status`:

| status | meaning |
|---|---|
| `ADDED` | not in the previous catalog |
| `UPDATED` | published, but something differs — `changes` says what |
| `UNCHANGED` | published identically |
| `DROPPED` | not published — `reason_detail` names the fix |

And a `resolved_by`:

| value | meaning |
|---|---|
| `uncontested` | nothing disputed it |
| `signal_majority` | signals disagreed; a specific one broke the tie |
| `reviewed_decision` | a human adjudicated it |

`reconciliation.csv` deliberately excludes rows that differ only in enriched
prose — expected when the enrichment source changes, and counted in the summary
as `content_only_changes`. Generated `record_id`s change whenever identity
fields change, so that is reported once as `record_ids_changed` rather than
flagged per row.

An `attribute_overrides` column means a reviewer corrected a value the model got
wrong; the decision file names who and why.

A `color_flag` column means the product's name states a colour that its
`primary_color` denies. These publish; `color_flag_confidence` is `high` when
the name puts the colour where it can only describe the product, and `low` when
it may name a component or be branding.

An `outlier` column means the product published despite one merchant signal
disagreeing. The classification is sound; the named signal should be corrected
in the source catalog.

## When a product is held

| Reason | Overridable | What to do |
|---|---|---|
| `UNRESOLVED_PRODUCT_CLASSIFICATION` | yes | add a decision naming the classification, or fix the source row |
| `NAME_CONTRADICTS_CLASSIFICATION` | yes | add a decision supplying a corrected `name` |
| `DUPLICATE_NAME_IMAGE` | yes | give each row a stable `sku`/`product_id` |
| `IMAGE_NOT_FOUND` / `IMAGE_UNREADABLE` | no | supply a readable image |
| `MISSING_REQUIRED_FIELD` / `INVALID_PRICE` | no | fix the source row |
| `MODEL_ENRICHMENT_FAILED` | no | re-run; if it persists the record cannot be evidenced |

### Guided review

`fashion-catalog-review` walks the rows that need a call, shows the
evidence for each, records what you decide, and rebuilds:

```bash
fashion-catalog-review \
  --input-csv   products.csv \
  --enrichment  out/run-2 --enrichment out/run-1 \
  --gate-run    out/run-2 \
  --decisions   decisions/products_extended.jsonl \
  --output-dir  out/rebuild
```

Add `--list` to see what needs attention and change nothing. Skipping is always
an option, and a skipped row is left exactly as it is.

It surfaces more than the held rows. A published product can need a call too:

| Finding | Meaning |
|---|---|
| `UNRESOLVED_PRODUCT_CLASSIFICATION` | the name and the image identify different products |
| `NAME_CONTRADICTS_CLASSIFICATION` | the name states a different product type than its category |
| `DUPLICATE_NAME_IMAGE` | rows are indistinguishable, so none is canonical |
| `COLOR_FLAG` | the name states a colour the record denies |
| `UNFILTERABLE_COLOR` | `primary_color` is absent or `other`, so no colour filter matches |
| `RECLASSIFIED` | the classification moved against the previous catalog |
| `DUPLICATE_PUBLISHED_NAME` | two published products share a name |

It keeps going until nothing new appears, because resolving one problem can
surface another — naming a classification can leave the product name
contradicting it. Rows that no decision can fix, such as a missing image, are
reported separately with the fix required rather than prompted for.

At the end it prints where the catalog, the reconciliation view, and the drop
list are.

### By hand

To publish a held product, add one line to the decision file:

```json
{"source_row": 172, "resolves": ["UNRESOLVED_PRODUCT_CLASSIFICATION"],
 "classification": "footwear/heels", "reviewer": "you@example.com",
 "rationale": "Image shows a visible stiletto heel."}
```

Then re-run. Decisions are bound to the CSV's hash, so a stale file fails the run
rather than applying yesterday's ruling to a shifted row. Full format:
[Reviewed Decisions](DECISIONS.md).
