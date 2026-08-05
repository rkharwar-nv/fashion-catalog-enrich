# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Rebuild the fashion catalog from frozen enrichment, deterministically.

Enrichment is a live model call, so re-running it cannot reproduce a catalog
exactly. This script instead replays enrichment that has already been produced
and applies the *current* publication rules to it: three-signal classification,
identity keyed on distinguishing fields, and reviewed decisions.

That makes the output a pure function of its pinned inputs -- the source CSV, the
frozen enrichment runs, and the decision file -- so the same inputs always give
the same catalog, with no endpoint required.

``--enrichment`` is repeatable and consulted in priority order, because no single
run necessarily covers every row. ``--gate-run`` names the run whose
``eliminated_products.jsonl`` says which rows were contested; the classification
tie-breaker applies only to those, since it resolves disputes rather than acting
as a second filter over rows that were never in dispute. ``--baseline`` is
optional and marks each row ADDED, UPDATED, UNCHANGED or DROPPED against the
catalog currently in use.

Usage:
    PYTHONPATH=src python scripts/rebuild_catalog.py \
        --input-csv  shared/data/products_extended.csv \
        --enrichment shared/output/<latest-run> \
        --enrichment shared/output/<earlier-run> \
        --gate-run   shared/output/<latest-run> \
        --decisions  shared/decisions/products_extended.jsonl \
        --baseline   shared/data/enriched_products.jsonl \
        --output-dir data/catalog-recovery/run

Outputs: enriched_products.jsonl (the catalog), rebuild_ledger.jsonl (every row),
reconciliation.csv (contested, added, updated or dropped rows),
dropped_products.csv (exclusions with the fix required), plus a summary and a
manifest pinning every input hash.
"""

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fashion_catalog.batch import (
    PUBLICATION_POLICY_VERSION,
    duplicate_key,
    record_id_for,
)
from fashion_catalog.decisions import DECISIONS_VERSION, load_decisions
from fashion_catalog.taxonomy import (
    ATTRIBUTE_VERSION,
    PRODUCT_ATTRIBUTES,
    TAXONOMY_VERSION,
    color_mismatch,
    derived_audience,
    name_product_signal,
    resolve_product_type,
    types_compatible,
)

# Fields carried straight from the source row.
SOURCE_FIELDS = ("category", "subcategory", "name", "description", "url", "price", "image")
# Fields the enrichment run adds that are not attributes.
NON_ATTRIBUTE_FIELDS = SOURCE_FIELDS + (
    "record_id", "source_row", "enriched_description", "_enrichment_run",
)

# (category, subcategory) -> canonical product type. The forward mapping is
# product_type.split("."), taking the first and last parts, and each product
# type yields a unique pair, so it inverts cleanly.
CLASSIFICATION_TO_TYPE = {
    (product_type.split(".")[0], product_type.split(".")[-1]): product_type
    for product_type in PRODUCT_ATTRIBUTES
}


# Plain-language explanation for every way a row can fail to reach the catalog,
# and what would have to happen for it to be published.
DROP_EXPLANATIONS = {
    "DUPLICATE_NAME_IMAGE": (
        "Two or more rows are identical across name, image, price and description, so there is no "
        "way to tell which is canonical. Fix: give each row a stable sku or product_id."
    ),
    "NO_FROZEN_ENRICHMENT": (
        "No enrichment exists for this row in any of the supplied runs, so there is no description "
        "or attributes to publish. Fix: resolve the underlying gate reason, then re-run enrichment."
    ),
    "UNMAPPABLE_CLASSIFICATION": (
        "The enrichment run classified this row into a category outside the current taxonomy. "
        "Fix: re-run enrichment against the current taxonomy version."
    ),
    "UNRESOLVED_PRODUCT_CLASSIFICATION": (
        "The product name and the image identify different product types, and the subcategory "
        "column is too coarse to break the tie. Fix: add a reviewed decision naming the correct "
        "classification, or correct the source row."
    ),
    "REVIEWER_EXCLUDED": (
        "A reviewer removed this row from the catalog. The decision file names who and why."
    ),
    "NAME_CONTRADICTS_CLASSIFICATION": (
        "The product name states a different product type than the category it was filed under, "
        "which would show shoppers a name contradicting its own category and filters. "
        "Fix: add a reviewed decision supplying a corrected name."
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rebuild(
    input_csv: Path,
    enrichment_dirs: list[Path],
    gate_run: Path,
    decisions_path: Path | None,
    output_dir: Path,
    baseline: Path | None = None,
    derive_audience: bool = False,
) -> dict[str, Any]:
    input_sha = _sha256(input_csv)

    manifests = []
    for directory in enrichment_dirs:
        manifest = json.loads((directory / "run_manifest.json").read_text())
        if manifest.get("input_sha256") != input_sha:
            raise SystemExit(
                f"Frozen enrichment in {directory.name} was produced from a different CSV "
                f"({manifest.get('input_sha256', '?')[:12]}… vs {input_sha[:12]}…). Row numbers "
                "would not line up."
            )
        manifests.append(manifest)

    registry = None
    if decisions_path:
        registry = load_decisions(decisions_path)
        registry.verify_input(input_sha)

    with input_csv.open(newline="", encoding="utf-8-sig") as handle:
        source_rows = {i + 2: row for i, row in enumerate(csv.DictReader(handle))}

    # Enrichment sources in priority order; no single run covers every row.
    enriched: dict[int, dict[str, Any]] = {}
    for directory in enrichment_dirs:
        for record in _read_jsonl(directory / "enriched_products.jsonl"):
            if record.get("enriched_description") and record["source_row"] not in enriched:
                enriched[record["source_row"]] = {**record, "_enrichment_run": directory.name}

    # Optional comparison against the catalog currently in use, so the review
    # can show what actually changed rather than only what the gate contested.
    baseline_records = None
    baseline_rows = None
    if baseline:
        baseline_records = {record["source_row"]: record for record in _read_jsonl(baseline)}
        baseline_rows = set(baseline_records)

    # Rows the gate contested. Everything else it already published, so the
    # classification tie-breaker does not apply -- it resolves disputes, it is
    # not a second filter over rows that were never in dispute.
    gate_reasons = {
        record["source_row"]: record.get("elimination_reasons", [])
        for record in _read_jsonl(gate_run / "eliminated_products.jsonl")
    }
    contested = set(gate_reasons)

    # Identity is ambiguous only when nothing distinguishes two rows.
    duplicate_counts: dict[tuple[str, ...], int] = {}
    for row in source_rows.values():
        key = duplicate_key(row)
        duplicate_counts[key] = duplicate_counts.get(key, 0) + 1

    catalog: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []

    for source_row in sorted(source_rows):
        source = source_rows[source_row]
        record = enriched.get(source_row) or {}
        decision = registry.for_row(source_row) if registry else None
        record_id = decision.record_id if decision and decision.record_id else record_id_for(
            input_csv, source_row, source
        )
        entry = {
            "source_row": source_row,
            "name": source.get("name", ""),
            "record_id": record_id,
            "published": False,
            "reason": None,
            "classification": None,
            "resolved_by": None,
            # Set up front so rows that exit early still appear in the
            # reconciliation view.
            "contested": source_row in contested,
            "gate_reasons": sorted(gate_reasons.get(source_row, [])),
            "source_category": f"{source.get('category', '')}/{source.get('subcategory', '')}",
            "in_baseline": source_row in baseline_rows if baseline_rows is not None else None,
        }

        if decision and decision.exclude:
            entry["reason"] = "REVIEWER_EXCLUDED"
            entry["reviewer"] = decision.reviewer
            ledger.append(entry)
            continue

        if duplicate_counts[duplicate_key(source)] > 1:
            entry["reason"] = "DUPLICATE_NAME_IMAGE"
            ledger.append(entry)
            continue

        if not record.get("enriched_description"):
            # No usable enrichment exists for this row in the frozen run.
            entry["reason"] = "NO_FROZEN_ENRICHMENT"
            ledger.append(entry)
            continue

        product_type = CLASSIFICATION_TO_TYPE.get((record.get("category"), record.get("subcategory")))
        if not product_type:
            entry["reason"] = "UNMAPPABLE_CLASSIFICATION"
            ledger.append(entry)
            continue

        classification = f"{record['category']}/{record['subcategory']}"
        resolution = resolve_product_type(source, product_type)
        entry.update(
            visual_classification=classification,
            name_signal=resolution["name_signal"],
            name_verdict=resolution["name_verdict"],
            subcategory_verdict=resolution["column_verdict"],
        )
        if source_row not in contested:
            entry["resolved_by"] = "uncontested"
        elif resolution["publish"]:
            entry["resolved_by"] = "signal_majority"
            if resolution["outlier"]:
                entry["outlier"] = resolution["outlier"]
        elif decision and decision.resolves_reason("UNRESOLVED_PRODUCT_CLASSIFICATION"):
            entry["resolved_by"] = "reviewed_decision"
            entry["reviewer"] = decision.reviewer
        else:
            entry["reason"] = "UNRESOLVED_PRODUCT_CLASSIFICATION"
            ledger.append(entry)
            continue

        if decision and decision.classification:
            # An explicit classification wins regardless of whether the gate
            # contested the row; a reviewer who names one has looked at it.
            if classification != decision.classification:
                entry["classification_override"] = (
                    f"{classification} -> {decision.classification}"
                )
            classification = decision.classification
            entry["reviewer"] = decision.reviewer

        # A product whose own name names a different product type than the one it
        # is filed under is incoherent to a shopper, however sound the taxonomy.
        # It is held until a decision supplies corrected copy.
        published_type = CLASSIFICATION_TO_TYPE.get(tuple(classification.split("/", 1)))
        name_signal = name_product_signal(source.get("name", ""))
        if name_signal and published_type and not types_compatible(name_signal, published_type):
            if not (decision and decision.name):
                entry["reason"] = "NAME_CONTRADICTS_CLASSIFICATION"
                entry["resolved_by"] = None
                ledger.append(entry)
                continue
            entry["corrected_name"] = decision.name
            entry["merchant_name"] = source.get("name", "")

        category, subcategory = classification.split("/", 1)
        published = {key: source.get(key, "") for key in SOURCE_FIELDS}
        if decision and decision.name:
            # The original name is recorded in the ledger, not republished. Nor is
            # the merchant description, which describes the contradicted type.
            published["name"] = decision.name
            published["description"] = ""
        published["category"] = category
        published["subcategory"] = subcategory
        published["record_id"] = record_id
        published["source_row"] = source_row
        for field, value in record.items():
            if field not in NON_ATTRIBUTE_FIELDS and value not in (None, "", []):
                published[field] = value
        published["enriched_description"] = record["enriched_description"]
        if derive_audience and not published.get("target_audience"):
            # Set from the classification, not from the image or the person in
            # it. Only types whose construction settles the department appear in
            # the table; the rest stay unset.
            audience = derived_audience(
                published["category"], published["subcategory"], published,
            )
            if audience:
                published["target_audience"] = audience
                entry["target_audience_source"] = "derived_from_classification"

        if decision and decision.attributes:
            # A reviewer corrected an attribute the model got wrong. Recorded in
            # the ledger, so the published value always has a named author.
            published.update(decision.attributes)
            entry["attribute_overrides"] = ", ".join(
                f"{k}={v!r}" for k, v in sorted(decision.attributes.items())
            )

        catalog.append(published)
        entry["published_name"] = published["name"]
        # Flagged, not held: a colour word in a name is weaker evidence than a
        # product noun and may describe a trim or a component.
        mismatch = color_mismatch(published["name"], published.get("primary_color"))
        if mismatch:
            entry["color_flag"] = (
                f"name says {mismatch['name_color']}, primary_color is "
                f"{mismatch['primary_color']}"
                + ("" if mismatch["unambiguous"] else " (may be branding or a component)")
            )
            entry["color_flag_confidence"] = "high" if mismatch["unambiguous"] else "low"
        entry.update(
            published=True,
            classification=classification,
            enrichment_run=record.get("_enrichment_run"),
        )
        ledger.append(entry)

    # A single status per row, so a reviewer can flag what needs attention
    # without reconstructing it from several fields.
    published_by_row = {record["source_row"]: record for record in catalog}
    for entry in ledger:
        changes: list[str] = []
        if entry["published"]:
            previous = (baseline_records or {}).get(entry["source_row"])
            if previous is None:
                entry["status"] = "ADDED" if baseline_records is not None else "PUBLISHED"
            else:
                was = f"{previous.get('category')}/{previous.get('subcategory')}"
                if was != entry["classification"]:
                    changes.append(f"classification {was} -> {entry['classification']}")
                if previous.get("name") != entry.get("published_name"):
                    changes.append(
                        f"name {previous.get('name')!r} -> {entry.get('published_name')!r}"
                    )
                # Compare the whole record, not just identity. Two runs of the same
                # model produce different prose, so a row can be substantively
                # different while its classification and name are untouched.
                current = published_by_row.get(entry["source_row"], {})
                content = sorted(
                    field for field in (set(previous) | set(current)) - {"record_id"}
                    if previous.get(field) != current.get(field)
                    and field not in {"category", "subcategory", "name"}
                )
                if content:
                    entry["changed_fields"] = ", ".join(content)
                    changes.append(f"{len(content)} field(s) differ: {', '.join(content[:4])}"
                                   + (" …" if len(content) > 4 else ""))
                # Generated ids are content hashes over a changed field set, so
                # they differ for every row. Tracking that as a per-row change
                # would bury the substantive ones; it is reported once in the
                # summary instead.
                entry["record_id_changed"] = previous.get("record_id") != entry["record_id"]
                entry["status"] = "UPDATED" if changes else "UNCHANGED"
            if entry.get("outlier"):
                changes.append(f"source {entry['outlier']} disagrees with published class")
        else:
            entry["status"] = "DROPPED"
        entry["changes"] = "; ".join(changes)

    for entry in ledger:
        if not entry["published"]:
            detail = DROP_EXPLANATIONS.get(entry["reason"], entry["reason"])
            if entry["reason"] == "NO_FROZEN_ENRICHMENT" and entry["gate_reasons"]:
                detail = f"{detail} Gate reason: {', '.join(entry['gate_reasons'])}."
            entry["reason_detail"] = detail

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "enriched_products.jsonl").open("w", encoding="utf-8") as handle:
        for record in catalog:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (output_dir / "rebuild_ledger.jsonl").open("w", encoding="utf-8") as handle:
        for entry in ledger:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # A focused view of only the rows the gate contested. The full ledger is
    # mostly uncontested rows, which say nothing about how reconciliation works.
    reconciliation_fields = [
        "source_row", "name", "status", "changes", "record_id", "record_id_changed", "published",
        "in_baseline", "gate_reasons",
        "source_category", "visual_classification", "classification", "name_signal",
        "name_verdict", "subcategory_verdict", "outlier", "resolved_by", "reviewer", "reason",
        "reason_detail", "color_flag", "color_flag_confidence", "target_audience_source",
        "classification_override",
        "attribute_overrides",
        "changed_fields",
        "merchant_name", "corrected_name", "enrichment_run",
    ]
    with (output_dir / "reconciliation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=reconciliation_fields, extrasaction="ignore")
        writer.writeheader()
        for entry in ledger:
            # Substantive changes only. Two runs of the same model produce
            # different prose for nearly every row; listing those here would bury
            # the rows that actually need a look.
            substantive = (
                entry["status"] in {"ADDED", "DROPPED"}
                or entry.get("contested")
                or entry.get("color_flag")
                or entry.get("attribute_overrides")
                or entry.get("classification_override")
                or entry.get("resolved_by") == "reviewed_decision"
                or "classification " in (entry["changes"] or "")
                or "name " in (entry["changes"] or "")
            )
            if substantive:
                row = dict(entry)
                row["gate_reasons"] = ", ".join(entry.get("gate_reasons") or [])
                writer.writerow(row)

    dropped = [entry for entry in ledger if not entry["published"]]
    with (output_dir / "dropped_products.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["source_row", "name", "status", "record_id", "reason", "reason_detail",
                        "gate_reasons", "source_category", "in_baseline"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for entry in dropped:
            writer.writerow({**entry, "gate_reasons": ", ".join(entry.get("gate_reasons") or [])})

    excluded: dict[str, int] = {}
    # A single status per row, so a reviewer can flag what needs attention
    # without reconstructing it from several fields.
    published_by_row = {record["source_row"]: record for record in catalog}
    for entry in ledger:
        changes: list[str] = []
        if entry["published"]:
            previous = (baseline_records or {}).get(entry["source_row"])
            if previous is None:
                entry["status"] = "ADDED" if baseline_records is not None else "PUBLISHED"
            else:
                was = f"{previous.get('category')}/{previous.get('subcategory')}"
                if was != entry["classification"]:
                    changes.append(f"classification {was} -> {entry['classification']}")
                if previous.get("name") != entry.get("published_name"):
                    changes.append(
                        f"name {previous.get('name')!r} -> {entry.get('published_name')!r}"
                    )
                # Compare the whole record, not just identity. Two runs of the same
                # model produce different prose, so a row can be substantively
                # different while its classification and name are untouched.
                current = published_by_row.get(entry["source_row"], {})
                content = sorted(
                    field for field in (set(previous) | set(current)) - {"record_id"}
                    if previous.get(field) != current.get(field)
                    and field not in {"category", "subcategory", "name"}
                )
                if content:
                    entry["changed_fields"] = ", ".join(content)
                    changes.append(f"{len(content)} field(s) differ: {', '.join(content[:4])}"
                                   + (" …" if len(content) > 4 else ""))
                # Generated ids are content hashes over a changed field set, so
                # they differ for every row. Tracking that as a per-row change
                # would bury the substantive ones; it is reported once in the
                # summary instead.
                entry["record_id_changed"] = previous.get("record_id") != entry["record_id"]
                entry["status"] = "UPDATED" if changes else "UNCHANGED"
            if entry.get("outlier"):
                changes.append(f"source {entry['outlier']} disagrees with published class")
        else:
            entry["status"] = "DROPPED"
        entry["changes"] = "; ".join(changes)

    for entry in ledger:
        if not entry["published"]:
            excluded[entry["reason"]] = excluded.get(entry["reason"], 0) + 1
    summary = {
        "source_rows": len(source_rows),
        "published": len(catalog),
        "excluded": len(ledger) - len(catalog),
        "excluded_by_reason": excluded,
        "contested_rows": len(contested),
        "resolved_by_uncontested": sum(e.get("resolved_by") == "uncontested" for e in ledger),
        "resolved_by_signal_majority": sum(e.get("resolved_by") == "signal_majority" for e in ledger),
        "resolved_by_reviewed_decision": sum(e.get("resolved_by") == "reviewed_decision" for e in ledger),
        "published_with_outlier_signal": sum(bool(e.get("outlier")) for e in ledger),
    }
    if baseline_rows is not None:
        summary["status_counts"] = {
            status: sum(entry["status"] == status for entry in ledger)
            for status in ("ADDED", "UPDATED", "UNCHANGED", "DROPPED")
        }
        summary["record_ids_changed"] = sum(bool(e.get("record_id_changed")) for e in ledger)
        summary["reclassified_vs_baseline"] = [
            {"source_row": e["source_row"], "name": e["name"], "changes": e["changes"]}
            for e in ledger
            if e["status"] == "UPDATED"
            and ("classification " in (e["changes"] or "") or "name " in (e["changes"] or ""))
        ]
        # Every other UPDATED row differs only in enriched prose or attributes,
        # which is expected when the enrichment source run changes. Per-row
        # detail is in rebuild_ledger.jsonl.
        summary["target_audience_derived"] = sum(
            1 for e in ledger if e.get("target_audience_source") == "derived_from_classification"
        )
        summary["color_mismatches"] = {
            "high_confidence": sum(
                1 for e in ledger if e.get("color_flag_confidence") == "high"
            ),
            "low_confidence": sum(
                1 for e in ledger if e.get("color_flag_confidence") == "low"
            ),
        }
        summary["content_only_changes"] = sum(
            1 for e in ledger
            if e["status"] == "UPDATED"
            and "classification " not in (e["changes"] or "")
            and "name " not in (e["changes"] or "")
        )
        summary["added_vs_baseline"] = sorted(
            e["source_row"] for e in ledger if e["published"] and not e["in_baseline"]
        )
        summary["dropped_vs_baseline"] = sorted(
            e["source_row"] for e in ledger if not e["published"] and e["in_baseline"]
        )
    (output_dir / "rebuild_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output_dir / "rebuild_manifest.json").write_text(json.dumps({
        "input_csv": str(input_csv),
        "input_sha256": input_sha,
        "enrichment_runs": [str(directory) for directory in enrichment_dirs],
        "gate_run": str(gate_run),
        "decisions_path": str(decisions_path) if decisions_path else None,
        "decisions_sha256": registry.file_sha256 if registry else None,
        "decisions_version": DECISIONS_VERSION if registry else None,
        "taxonomy_version": TAXONOMY_VERSION,
        "attribute_version": ATTRIBUTE_VERSION,
        "publication_policy_version": PUBLICATION_POLICY_VERSION,
        "enrichment_source": "replayed from a frozen run; no model calls were made",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument(
        "--enrichment", type=Path, required=True, action="append",
        help="Frozen enrichment run; repeat in priority order, as no single run covers every row",
    )
    parser.add_argument(
        "--gate-run", type=Path, required=True,
        help="Run whose eliminated_products.jsonl defines which rows were contested",
    )
    parser.add_argument("--decisions", type=Path)
    parser.add_argument(
        "--baseline", type=Path,
        help="Catalog currently in use, to mark which rows are new or dropped",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(
        rebuild(args.input_csv, args.enrichment, args.gate_run, args.decisions,
                args.output_dir, args.baseline, args.derive_audience),
        indent=2,
    ))


if __name__ == "__main__":
    main()
