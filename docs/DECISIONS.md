# Reviewed Decisions

For input shape, configuration, running and output, see the
[Runbook](RUNBOOK.md).

Most conflicts are resolved from the data itself — see
[Conflict Resolution and Poor Product Information](CONFLICT_RESOLUTION.md)
for how classification, attribute, and identity conflicts are settled without a
human.

What is left is genuine ambiguity: a specific signal contradicts the image and
no other signal can break the tie, or two rows are indistinguishable. Those need
a person. A decision file records those adjudications so a run **reproduces**
them instead of re-litigating them.

For the 218-row reference catalog this is two rows.

Decisions can be written by hand or captured with
`fashion-catalog-review`, which walks the rows needing a call and records
the answers. One decision per row: a row needing a second call has its existing
decision extended rather than duplicated.

## Running with decisions

```bash
fashion-catalog-enrich
```

or directly:

```bash
fashion-catalog-enrich \
  --input-csv sample/products.csv \
  --images-dir sample/images \
  --output-dir data/catalog-recovery/run \
  --decisions decisions/products_extended.jsonl
```

## File format

Line-delimited JSON. The first line binds the file to one exact CSV:

```json
{"kind": "decision_header", "decisions_version": "fashion-decisions/0.1", "input_sha256": "3b58651736…"}
```

Each subsequent line is one adjudication:

```json
{"source_row": 161, "resolves": ["UNRESOLVED_PRODUCT_CLASSIFICATION"],
 "classification": "apparel/jumpsuits", "reviewer": "someone@example.com",
 "rationale": "Name and image both say jumpsuit; only the subcategory column said dress."}
```

| Field | Required | Meaning |
|---|---|---|
| `source_row` | yes | CSV line number, header being line 1 |
| `resolves` | yes | Elimination reason codes this decision adjudicates |
| `reviewer` | yes | Who made the call |
| `rationale` | yes | Why — recorded in the run's decision ledger |
| `classification` | no | `category/subcategory` override for the published record |
| `record_id` | no | Stable id, used to distinguish otherwise-identical rows |
| `name` | conditional | Corrected product name; **required** when resolving `NAME_CONTRADICTS_CLASSIFICATION` |
| `attributes` | no | Attribute values to correct, e.g. `{"primary_color": "brown"}` |
| `exclude` | no | `true` removes the row from the catalog |

When a decision supplies a `name`, the corrected name is published, the original
is recorded here and in the run ledger rather than republished, and the merchant
`description` is dropped because it describes the contradicted product type.

### Correcting an attribute

A decision may correct an attribute the model got wrong, with an empty
`resolves` when the product was published anyway:

```json
{"source_row": 62, "resolves": [], "attributes": {"primary_color": "brown"},
 "reviewer": "you@example.com",
 "rationale": "Gold frame, brown lenses; the lenses dominate and the name says Mocha."}
```

Values are checked against the attribute's enum when it has one, so a typo or an
off-taxonomy value fails at load time rather than reaching the catalog. The
override is recorded in the run ledger, so a corrected value always has a named
author and a reason.

### Removing a row

`exclude` takes a row out of the catalog with the reason `REVIEWER_EXCLUDED`:

```json
{"source_row": 30, "resolves": [], "exclude": true,
 "reviewer": "you@example.com",
 "rationale": "Duplicate listing sharing one image with row 38 at a different price."}
```

A decision may exclude a row **or** correct one, not both — those are different
intents, and a file trying to do both is rejected at load time.

### Overriding a classification outright

A `classification` applies whether or not the gate contested the row. A reviewer
who names one has looked at the product, so it wins over what enrichment
concluded, and the change is recorded as `classification_override` in the run
ledger.

## What a reviewer may and may not resolve

Three reasons are resolvable:

- `UNRESOLVED_PRODUCT_CLASSIFICATION` — signals disagree with no majority
- `DUPLICATE_NAME_IMAGE` — rows indistinguishable, none canonical
- `NAME_CONTRADICTS_CLASSIFICATION` — the name states a different product type
  than the category. Resolving this **without** a corrected `name` is rejected
  when the file loads, since publishing incoherent copy is what the rule exists
  to prevent.

Everything else stays a hard stop:

- `IMAGE_NOT_FOUND` / `IMAGE_UNREADABLE` — no visual evidence to adjudicate
- `MISSING_REQUIRED_FIELD` / `INVALID_PRICE` — the input row is invalid
- `MODEL_ENRICHMENT_FAILED` / `ENRICHMENT_NOT_AVAILABLE` — no record was
  produced, and a reviewer cannot supply enrichment that does not exist

A decision naming any other reason is rejected at load time rather than
silently never matching.

## Reproducibility guarantees

- **Decisions are bound to the CSV.** Row numbers only mean something for one
  exact input file. If the CSV changes, the run fails with a `DecisionError`
  telling you to re-review, instead of applying row 161's decision to whatever
  now sits on line 161.
- **The manifest pins everything.** `run_manifest.json` records `input_sha256`,
  `decisions_sha256`, `taxonomy_version`, `attribute_version`,
  `publication_policy_version`, `decisions_version`, and the `vlm_endpoint` and
  `vlm_model` that produced the run.
- **Every applied decision is logged.** `decision_ledger.jsonl` records, per
  row, the gate's reasons, which were resolved, which were not, the reviewer,
  the rationale, and whether the row ended up published.

### What is *not* guaranteed

Enrichment is a live model call. Identical inputs and identical decisions will
not necessarily produce byte-identical enriched text, because the VLM is not
deterministic. What the decision file makes reproducible is the **policy**:
which rows publish, under which classification, on whose authority. To
reproduce a catalog exactly, reuse the published artifacts rather than re-running
enrichment.

## When a decision is needed at all

Most eliminations are not genuine ambiguity — they are merchant metadata that
disagrees with itself. The gate weighs three independent signals before asking
for a human:

| Signal | Source |
|---|---|
| product name | the merchant `name` column |
| subcategory | the merchant `subcategory` column |
| image | the visual product type |

A signal only counts when it is specific. `subcategory: shoes` covers heels,
flats, boots and sandals, so it cannot settle a dispute between them, and a name
mentioning two types ("Woven Lace Blouse Sweater") abstains rather than guessing.

The product publishes when a specific signal corroborates the visual type, or
when nothing contradicts it. The disagreeing signal is recorded in
`enrichment_review.csv` as `published_with_outlier_signal` so the source catalog
can be corrected. Only a genuine tie — one specific signal against the image,
with no third signal able to break it — needs a decision entry.

A decision is also needed when a published product's **name** states a different
product type than its category, since that is incoherent to a shopper whatever
the taxonomy says. See
[Incoherent product copy](CONFLICT_RESOLUTION.md#incoherent-product-copy).

For the 218-row reference catalog this reduced the decision file from seven
entries to two, both of which were adjudicated against the product image.
