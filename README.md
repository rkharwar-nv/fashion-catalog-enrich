# Fashion Catalog Enrichment

Turns a merchant CSV and a folder of product photos into an enriched, filterable
catalog — and refuses to publish a product it cannot stand behind.

The enrichment is one multimodal model call per product, reading the image and
the merchant row together. What makes this more than a wrapper is everything
around it: a publication gate that catches merchant data contradicting itself, a
decision file that records human adjudications so runs reproduce them, and a
ledger that says why every product did or did not ship.

## Why a gate

Merchant catalogs disagree with themselves constantly. A product named "Ballet
Flats" whose photo shows stilettos. A subcategory column saying `dress` on a
jumpsuit. A name promising navy on a record published as black.

Publishing all of it puts incoherent products in front of shoppers. Publishing
none of it loses real products to problems the data already answers. So the gate
weighs three independent signals — the product **name**, the **subcategory**
column, and the **image** — and publishes when they reach a majority, holding
only genuine ties for a person.

Over a 218-row reference catalog that left **one** product needing a human call.

## Install

```bash
pip install -e ".[dev]"
```

Python 3.11+. Runtime dependencies are `openai` and `pillow`; nothing else.

## Try it

A self-contained sample ships with the repo. This validates the input contract
and makes no model calls:

```bash
fashion-catalog-enrich \
  --input-csv  sample/products.csv \
  --images-dir sample/images \
  --output-dir out/preflight \
  --validate-only
```

```
{ "total": 5, "pass": 4, "review": 1, ... }
```

The flagged row is deliberate: `sample/products.csv` references an image that
does not exist, so you can see how a bad input is reported before inference is
spent on it.

Drop `--validate-only` and point `FASHION_VLM_*` at an endpoint to enrich for
real.

## Configuration

One endpoint, one model, one credential. Everything else — taxonomy,
publication policy, decisions — is versioned data, not configuration.

| Variable | Default | |
|---|---|---|
| `FASHION_VLM_MODEL` | — | **required** |
| `FASHION_VLM_URL` | `https://integrate.api.nvidia.com/v1` | |
| `FASHION_VLM_API_KEY` | falls back to `NGC_API_KEY` | |
| `FASHION_VLM_TIMEOUT` | `120` | |

## Commands

| | |
|---|---|
| `fashion-catalog-enrich` | run enrichment over a CSV; the only command that calls a model |
| `fashion-catalog-rebuild` | re-derive a catalog from enrichment you already have — deterministic, no endpoint needed |
| `fashion-catalog-review` | walk the products needing a human call, record decisions, rebuild |

Rebuild exists because enrichment is a live model call and cannot be reproduced
exactly. Replaying frozen enrichment through the current publication rules can
be, so changing policy or applying a decision never costs inference and always
gives a byte-identical result.

## Documentation

- **[Runbook](docs/RUNBOOK.md)** — start here: input shape, config, running, output shape, reading the results
- **[Conflict Resolution](docs/CONFLICT_RESOLUTION.md)** — how merchant data that is wrong, incomplete, or contradicts the image is handled
- **[Reviewed Decisions](docs/DECISIONS.md)** — recording human adjudications so runs reproduce them
- **[Single-Call Enrichment](docs/SINGLE_CALL.md)** — a lighter alternative for one product against any OpenAI-compatible endpoint
- **[Example run](examples/fashion-catalog/)** — real output over a 218-row catalog, with its ledgers

## What it will not do

- Invent a fact. Composition and care cannot be read off a photo, so they come
  from the merchant or stay absent. An absent attribute means "not established",
  never "false".
- Publish a product whose name contradicts its own category.
- Silently correct a value. A corrected attribute or classification carries a
  reviewer and a rationale in the decision file.
- Lose a product to one unevidenced optional field.

## License

Apache 2.0.
