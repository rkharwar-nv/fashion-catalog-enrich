# Single-Call Fashion Enrichment

`fashion_catalog.single_call` enriches one fashion product with **one** vision-language
model call, against any OpenAI-compatible endpoint.

It is a self-contained alternative to the batch pipeline in
`fashion_catalog`, not a replacement for it. Both are in the tree because they
answer different questions.

## Which one to use

| | `fashion_catalog` (batch) | `fashion_catalog.single_call` (single call) |
|---|---|---|
| Scope | a whole catalog CSV | one product |
| Output | controlled taxonomy, per-field evidence and status | one flat, typed record |
| Publication gate | yes — conflicts, identity, coherence | none |
| Audit trail | review report, eliminations, decision ledger | a `_meta` block |
| Endpoint | the repository's configured VLM | any OpenAI-compatible endpoint |
| Model calls per product | several | one |

Use the batch pipeline to **publish a catalog**: it is the one that decides what
is fit to ship and records why. Use this module to enrich a **single product**
cheaply, or to run against a locally hosted or third-party VLM.

If you need controlled vocabularies, conflict resolution, or an audit trail, this
module does not provide them — see
[Conflict Resolution](CONFLICT_RESOLUTION.md).

## Usage

```python
from fashion_catalog.single_call import enrich_product

record = enrich_product(
    image_bytes,
    "image/jpeg",
    product_data={"name": "Silk Blouse", "material": "silk"},
    locale="en-US",
    brand_voice="warm and understated",
)
```

`product_data` is optional; without it the garment is described from the image
alone. `enrich_product` raises `FashionEnrichmentError` when no attempt yields a
record with both a `title` and a `product_type`, and `ValueError` on invalid
arguments.

## Configuration

Pass a `VLMConfig`, or let it build one from the environment:

| Variable | Default | Meaning |
|---|---|---|
| `FASHION_VLM_MODEL` | — | **Required.** Model served by the endpoint |
| `FASHION_VLM_URL` | `https://integrate.api.nvidia.com/v1` | Endpoint base URL |
| `FASHION_VLM_API_KEY` | falls back to `NGC_API_KEY`, then `OPENAI_API_KEY` | Credential; use a placeholder for local endpoints |
| `FASHION_VLM_TIMEOUT` | `120` | Request timeout in seconds |
| `FASHION_VLM_TEMPERATURE` | `0.1` | |
| `FASHION_VLM_TOP_P` | `0.9` | |
| `FASHION_VLM_MAX_TOKENS` | `4096` | |
| `FASHION_VLM_EXTRA_BODY` | `{}` | JSON object merged into the request body |

Every backend completion call in this repository disables model "thinking", so
the request always sends `{"chat_template_kwargs": {"enable_thinking": False}}`.
`FASHION_VLM_EXTRA_BODY` merges on top: add fields with
`'{"nvext": {"guided_json": true}}'`, or clear the default for an endpoint that
rejects it with `'{"chat_template_kwargs": {}}'`.

## Output

A flat record with every key in `FASHION_SCHEMA` always present — missing values
get schema defaults rather than going absent, and unknown keys from the model are
dropped:

`title`, `description`, `product_type`, `category`, `gender`, `colors`,
`materials`, `pattern`, `fit`, `style`, `occasion`, `season`, `care`, `tags`,
`confidence`, `notes`

Plus a `_meta` block recording `attempts`, `locale`, and `model`.

`confidence` maps a field name to `low`/`medium`/`high` for values the model had
to guess, and `notes` records what it could not determine.

## What it does not invent

The prompt carries the reconciliation and anti-hallucination rules from the
original `vlm.py` pipeline:

- The **image is ground truth** for anything visible. Where merchant text
  conflicts with what is clearly visible, the conflicting merchant term is
  dropped.
- **Material is the exception.** Composition cannot be verified from a photo, so
  a merchant-supplied material wins. Material inferred from the image only when
  the merchant gave none, and then marked `low` in `confidence`.
- **Absence from the image is not a contradiction.** Non-visible merchant
  metadata — brand, SKU, price, fabric — is kept.
- Measurements, weight, size, exact composition, certifications, origin, and
  care instructions are never inferred unless printed in the image or supplied.

## Status

Standalone and not wired into the HTTP API. Nothing imports it; adding an
endpoint is a product decision that has not been taken.
