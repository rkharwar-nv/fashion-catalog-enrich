# Conflict Resolution and Poor Product Information

For input shape, configuration, running and output, see the
[Runbook](RUNBOOK.md).

How the fashion pipeline decides whether a product reaches the catalog when the
merchant data is wrong, incomplete, or disagrees with the product image.

The guiding rule: **never invent a claim, but never lose a product to a problem
the data already answers.** Most merchant data is not ambiguous — it is merely
mislabelled, and the label that is wrong can usually be identified.

A real run's output, including its ledgers, is in
[`examples/fashion-catalog/`](../examples/fashion-catalog/).

- [Classification conflicts](#classification-conflicts)
- [Attribute conflicts](#attribute-conflicts)
- [Poor product information](#poor-product-information)
- [Ambiguous identity](#ambiguous-identity)
- [Reason codes](#reason-codes)

## Classification conflicts

Three independent signals say what a product is:

| Signal | Source | Example |
|---|---|---|
| **name** | merchant `name` column | "Jewel Sequin Jumpsuit" → `apparel.jumpsuits` |
| **subcategory** | merchant `subcategory` column | `dress` → `apparel.dresses` |
| **image** | visual analysis | `apparel.jumpsuits` |

A signal only counts when it is **specific**:

- `subcategory: shoes` permits heels, flats, boots, sandals and other shoes, so
  it cannot settle a dispute between them. It is *compatible*, not corroborating.
- A name citing two product types — "Woven Lace Blouse Sweater" — abstains
  rather than guessing which keyword was meant.

### The rule

A product is published when **a specific signal corroborates the visual type**,
or when **nothing contradicts it**. It is held back only when a specific signal
contradicts the image and no other signal can break the tie.

```
name corroborates          -> publish
subcategory corroborates   -> publish
nothing contradicts        -> publish
otherwise                  -> UNRESOLVED_PRODUCT_CLASSIFICATION
```

The disagreeing signal is recorded in `enrichment_review.csv` as
`published_with_outlier_signal`, naming whether the `name` or the `subcategory`
was the outlier, so the source catalog can be corrected.

### Worked examples

| Name | Subcategory | Image | Outcome |
|---|---|---|---|
| Jewel Sequin **Jumpsuit** | `dress` | jumpsuits | published `apparel/jumpsuits`, **subcategory** flagged |
| Vivacious Velvet **Dress** | `top blouse sweater` | dresses | published `apparel/dresses`, **subcategory** flagged |
| Kaleidoscope Floral Print **Dress** | `skirt` | skirts | published `apparel/skirts`, **name** flagged |
| Woven Lace **Blouse Sweater** *(abstains)* | `top blouse sweater` | blouses | published `apparel/blouses` |
| Opulent Velvet **Ballet Flats** | `shoes` *(too coarse)* | heels | **held** — genuine tie |

The last row is the shape that needs a human: the name says flats, the image
says heels, and `shoes` covers both. See
[Reviewed Decisions](DECISIONS.md).

### Why not simply trust the image

Visual analysis is the strongest single signal but not infallible, and a product
whose taxonomy contradicts its own name produces incoherent search results and
filters. Requiring a second signal keeps the published classification defensible
against something in the merchant record.

## Attribute conflicts

Attributes are resolved per field, and a disagreement on one attribute never
eliminates a product.

- When the image contradicts a merchant attribute, the **visual value is
  published** and the contradicted source claim is omitted from the enriched
  description.
- Attributes whose status is not `accepted` are **not emitted at all**. Absence
  means "not established", never "false".
- `composition` and `care` are **free-text merchant claims** and cannot be
  sourced from the image alone. "60% cotton" is not visible. An attempt to
  derive them visually is rejected.
- `target_audience` — `womens`, `mens`, `adult_all_genders`, `kids` — is the
  **department a product is merchandised under**, not a statement about any
  person. The merchant's value wins. Failing that it may be inferred only from
  how the product is cut or constructed, and only where that is decisive. It is
  never inferred from the appearance, body, presentation, or perceived gender of
  a person in the image.

  `adult_all_genders` is a positive answer, not a fallback: it means adult
  sizing cut to be worn by anyone, and it must be evidenced like any other
  value. It is named for adults deliberately — `kids` is a separate department,
  not a subset of it, so a child's coat is `kids` however it is cut. It is
  not what to assume about a product that merely looks ungendered — merchants
  shelve handbags, eyewear and jewellery by department routinely, so a category
  alone never settles this. A product photographed on a woman is not thereby
  womenswear.

  For feed interoperability this maps to the conventional `gender` attribute as
  `womens` → female, `mens` → male, `adult_all_genders` → unisex. The catalog keeps
  the merchandising vocabulary; the mapping happens at export.

## Poor product information

### Missing or invalid required fields

These fail before any model call, because no publishable record can be built:

| Problem | Reason code |
|---|---|
| no `name` or no `description` | `MISSING_REQUIRED_FIELD` |
| `price` missing, non-numeric, or negative | `INVALID_PRICE` |
| image file absent | `IMAGE_NOT_FOUND` |
| image file present but undecodable | `IMAGE_UNREADABLE` |

None of these is overridable by a reviewer. Without an image there is no visual
evidence to adjudicate; without a price or name there is no product record. They
are reported in `enrichment_review.csv` during `--validate-only` runs too, which
exist to surface exactly these before spending model calls.

Note that `category` and `subcategory` are **not** required. A blank subcategory
simply abstains from the classification vote.

### Partial records

Enrichment is retried up to three times. If errors remain, they are separated:

- **Record-fatal** — an unusable `product_type`, or a missing
  `enriched_description`. There is no publishable record, so the product is
  eliminated with `MODEL_ENRICHMENT_FAILED`.
- **Per-attribute** — one optional field that could not be evidenced legally.
  The attribute is marked `unknown` with a null value and the product is
  **published without it**.

A neutralised attribute stays present in the enrichment result so the schema's
evidence-assessment requirement is still met, but with `status: unknown` it is
never emitted into the catalog record. No value is invented.

Every omission is reported in `enrichment_review.csv` as
`published_without_attribute`, and the message states whether the dropped field
is **filterable** — a missing `primary_color` removes the product from colour
filters, whereas a missing `composition` only weakens semantic search.

### Colour stated in the name

`primary_color` is filterable, so a product named "…in Navy" published as
`black` will not appear when a shopper filters for navy, while its own name
promises it. When the merchant name states a colour the record denies, the
product is **flagged, not held**, and reported in `enrichment_review.csv` as
`published_with_color_mismatch`.

Flagged rather than held because a colour word is weaker evidence than a product
noun: it may describe a trim, a lens, or one colour of a multicoloured item.
Confidence reflects that:

| Confidence | When | Example |
|---|---|---|
| **high** | the name puts the colour where it can only describe the product | "Sleek Stiletto Heels **in Navy**" published as `black` |
| **low** | the colour appears in the name but may name a component or be branding | "**Navy** Gradient Sunglasses" — black frame, navy lenses |

Colour words that double as personal or brand names — Jade, Amber, Coral, Rose,
Olive — count only in the unambiguous position, so "Jade Luxe Sunglasses" does
not conflict with `gold`. Shades resolve to their enum value: ivory and cream
are `white`, burgundy is `red`. A name citing one colour of a `multicolor`
product never conflicts.

## Ambiguous identity

When the merchant supplies no `product_id`, `sku`, or `id`, identity is derived
from the fields that actually distinguish a product: `name`, `image`, `url`,
`price`, and `description`.

Two rows sharing a name and an image are **not** duplicates if their price or
description differ — that is one image reused across two products, which is a
merchant mistake, not an identity clash. Both publish, each with its own
generated id.

Only rows that are indistinguishable across all identity fields raise
`DUPLICATE_NAME_IMAGE`, and then none of them is published, because there is no
way to tell which is canonical.

> Supplying a stable `sku` or `product_id` column avoids this entirely and makes
> record ids stable across catalog revisions. Generated ids are content hashes,
> so they change whenever the row changes.

## Reason codes

| Code | Meaning | Reviewer can override |
|---|---|---|
| `UNRESOLVED_PRODUCT_CLASSIFICATION` | signals disagree and no majority | yes |
| `DUPLICATE_NAME_IMAGE` | rows indistinguishable, none canonical | yes |
| `MODEL_ENRICHMENT_FAILED` | no schema-valid record after retries | no |
| `ENRICHMENT_NOT_AVAILABLE` | no complete, consistent record | no |
| `IMAGE_NOT_FOUND` | referenced image missing | no |
| `IMAGE_UNREADABLE` | image could not be decoded | no |
| `MISSING_REQUIRED_FIELD` | no name or description | no |
| `INVALID_PRICE` | price missing, non-numeric, or negative | no |

The non-overridable codes describe an absent input or an absent record. A
reviewer cannot supply enrichment that was never produced, or evidence from an
image that does not exist. A decision file naming one of them is rejected when
it is loaded, rather than silently never matching.

## Rebuilding a catalog without re-running enrichment

Enrichment is a live model call, so re-running it cannot reproduce a catalog
exactly. `fashion-catalog-rebuild` instead replays enrichment that has
already been produced and applies the current publication rules to it, making
the output a pure function of pinned inputs with no endpoint required:

```bash
fashion-catalog-rebuild \
  --input-csv  sample/products.csv \
  --enrichment shared/output/<latest-run> \
  --enrichment shared/output/<earlier-run> \
  --gate-run   shared/output/<latest-run> \
  --decisions  decisions/products_extended.jsonl \
  --output-dir data/catalog-recovery/run
```

`--enrichment` is repeatable and consulted in priority order, because no single
run necessarily covers every row. `--gate-run` names the run whose
`eliminated_products.jsonl` defines which rows were **contested**; the
classification tie-breaker applies only to those. Rows the gate already
published are not re-filtered — the rule resolves disputes, it is not a second
filter over rows that were never in dispute.

Outputs are `enriched_products.jsonl`, a per-row `rebuild_ledger.jsonl` naming
how each row was resolved and which run its enrichment came from, plus a summary
and a manifest pinning every input hash.

## Incoherent product copy

A correct classification is not enough. A product named "Ballet Flats" filed
under `footwear/heels` is incoherent to a shopper whatever the taxonomy says:
the name contradicts the category, the filters, and the enriched description,
and the merchant description usually contradicts them too.

So when the product name states a product type that the published
classification denies, the row is held with
`NAME_CONTRADICTS_CLASSIFICATION`. It publishes only when a decision supplies a
corrected `name`. Loading a decision that resolves this reason without one is an
error, because publishing without corrected copy is the very thing the rule
exists to prevent.

When corrected copy is supplied:

- the corrected `name` is published
- the original name is recorded in the decision ledger, not republished, so the
  catalog keeps only fields the schema declares
- the merchant `description` is **not** published, since it describes the
  contradicted product type; the enriched description already describes the
  product correctly

Some product types can legitimately describe one product, and those do not
count as contradictions — a heeled sandal and a heeled boot are ordinary
products, so a name mentioning a heel does not contradict `footwear/sandals`.
A heeled flat is not, so that pair still raises the conflict.

### Reviewing a rebuild

Every ledger row carries a `status` and, where relevant, a `changes` field
naming exactly what differs from the catalog in use:

| status | meaning |
|---|---|
| `ADDED` | not present in the previous catalog |
| `UPDATED` | published, but its classification or name changed |
| `UNCHANGED` | published identically |
| `DROPPED` | not published; `reason_detail` names the fix |

Generated record ids are content hashes over the identity fields, so they change
for every row whenever those fields change. That is counted once in the summary
as `record_ids_changed` rather than flagged per row, where it would bury the
substantive changes.

`reconciliation.csv` holds everything that was contested, added, updated or
dropped. `dropped_products.csv` holds the exclusions alone, each with a
plain-language explanation and the fix required.
