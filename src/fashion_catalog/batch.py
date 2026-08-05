# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Command-line batch enrichment for fashion catalogs."""

import argparse
import csv
import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fashion_catalog.audit import audit_row
from fashion_catalog.service import enrich_product
from fashion_catalog.config import VLMConfig
from fashion_catalog.decisions import DECISIONS_VERSION, ledger_entry, load_decisions
from fashion_catalog.taxonomy import (
    ATTRIBUTE_VALUES,
    ATTRIBUTE_VERSION,
    CORROBORATES,
    PRODUCT_ATTRIBUTES,
    TAXONOMY_VERSION,
    color_mismatch,
    name_product_signal,
    resolve_product_type,
    types_compatible,
)

# (category, subcategory) -> canonical product type; the forward mapping takes
# the first and last dotted parts, and each type yields a unique pair.
CLASSIFICATION_TO_TYPE = {
    (product_type.split(".")[0], product_type.split(".")[-1]): product_type
    for product_type in PRODUCT_ATTRIBUTES
}

logger = logging.getLogger("catalog_enrichment.fashion.batch")
PUBLICATION_POLICY_VERSION = "fashion-publication/0.4"

ELIMINATION_EXPLANATIONS = {
    "DUPLICATE_NAME_IMAGE": "Cause: ambiguous product identity. Multiple input rows use the same product name and image but do not provide stable source IDs. The workflow cannot determine whether they are duplicates, variants, or separate products, so none of the ambiguous rows is published.",
    "IMAGE_NOT_FOUND": "Cause: missing visual evidence. The referenced image was not found, so visual enrichment could not verify the product classification or ground the enriched description.",
    "IMAGE_UNREADABLE": "Cause: unusable visual evidence. The referenced image file could not be decoded, so visual analysis could not be completed.",
    "MISSING_REQUIRED_FIELD": "Cause: incomplete input data. The input row is missing a required product name or description, so a usable enriched catalog record cannot be created.",
    "INVALID_PRICE": "Cause: invalid input data. The input price is missing, negative, or not numeric, so the record fails the publication contract.",
    "MODEL_ENRICHMENT_FAILED": "Cause: model-output validation failure. After three attempts, the model output still failed schema, taxonomy, value, applicability, or evidence validation. This does not by itself mean that the input text conflicts with the image.",
    "UNRESOLVED_PRODUCT_CLASSIFICATION": "Cause: input-text-versus-image conflict. The input text or structured data and visual analysis identify different product types. Publishing either classification without review would create an unverified catalog identity.",
    "ENRICHMENT_NOT_AVAILABLE": "Cause: incomplete enrichment. The workflow could not produce a complete, internally consistent, publication-ready record.",
    "NAME_CONTRADICTS_CLASSIFICATION": "Cause: incoherent product copy. The product name states a different product type than the one the product was classified as. Publishing it would show shoppers a name that contradicts the category, filters, and description, so corrected copy is required before publication.",
}


def _has_source_id(row: dict[str, Any]) -> bool:
    return any(str(row.get(key) or "").strip() for key in ("product_id", "sku", "id"))


# Fields that distinguish one product from another when the merchant supplies no
# stable id. Name and image alone are not enough: two rows can share both and
# still be different products at different prices.
IDENTITY_FIELDS = ("name", "image", "url", "price", "description")


def _identity(row: dict[str, Any]) -> dict[str, str]:
    return {key: str(row.get(key) or "").strip().lower() for key in IDENTITY_FIELDS}


def record_id_for(csv_path: Path, row_number: int, row: dict[str, Any]) -> str:
    for key in ("product_id", "sku", "id"):
        if str(row.get(key) or "").strip():
            return str(row[key]).strip()
    digest = hashlib.sha256(json.dumps(_identity(row), sort_keys=True).encode()).hexdigest()[:16]
    return f"generated:{digest}"


def duplicate_key(row: dict[str, Any]) -> tuple[str, ...]:
    """Rows are ambiguous only when nothing distinguishes them.

    Sharing a name and image is not enough. If price or description differ, the
    rows describe different products that happen to reuse an image, and each can
    be published under its own generated id.
    """
    identity = _identity(row)
    identity["image"] = Path(identity["image"]).name
    return tuple(identity[key] for key in sorted(identity))


def _elimination_explanations(reasons: list[str], result: dict[str, Any] | None = None, detail: str = "") -> list[str]:
    explanations: list[str] = []
    has_conflict_detail = False
    if result:
        for conflict in result.get("conflicts") or []:
            if isinstance(conflict, dict) and conflict.get("reason"):
                has_conflict_detail = True
                field = "category/subcategory" if conflict.get("field") == "product_type" else conflict.get("field")
                source_value = conflict.get("source_value")
                visual_value = conflict.get("visual_value")
                comparison = ""
                if source_value not in (None, "") and visual_value not in (None, ""):
                    comparison = f" Input text/structured data says {source_value!r}; visual analysis says {visual_value!r}."
                explanations.append(
                    f"Cause: input-text-versus-image conflict for {field!r}.{comparison} "
                    f"Evidence detail: {conflict['reason']} The product was not published because choosing either "
                    "value without review could make its taxonomy, filters, or enriched description incorrect."
                )
    for reason in reasons:
        if has_conflict_detail and reason == "UNRESOLVED_PRODUCT_CLASSIFICATION":
            continue
        explanation = ELIMINATION_EXPLANATIONS.get(reason, reason)
        if explanation not in explanations:
            explanations.append(explanation)
    if detail:
        explanations.append(f"Validation detail: {detail}")
    return explanations


def _input_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _classification(product_type: str) -> tuple[str, str]:
    parts = product_type.split(".")
    return parts[0], parts[-1]


def _sources(value: dict[str, Any]) -> str:
    return "+".join(value.get("sources") or [])


def _review_status(value: dict[str, Any]) -> str:
    status = value.get("status")
    if status == "accepted":
        return "accepted"
    if status in {"conflicting", "needs_review"}:
        return "review"
    return "unknown"


def _review_rows(record_id: str, row_number: int, source: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = result.get("conflicts") or []
    conflict_details = {item.get("field"): item for item in conflicts if isinstance(item, dict)}
    product_type = result["product_type"]
    category, subcategory = _classification(product_type["value"])
    classification_needs_review = "product_type" in conflict_details or _review_status(product_type) != "accepted"
    if "product_type" in conflict_details:
        classification_reason = "The source and visual evidence disagree on the product's identity, so no classification was selected automatically."
    elif classification_needs_review:
        classification_reason = "The product identity remains unresolved, so no classification was selected automatically."
    else:
        classification_reason = "The canonical classification passed taxonomy and evidence validation."
    rows = [{
        "record_id": record_id,
        "source_row": row_number,
        "product_name": source.get("name", ""),
        "field": "category/subcategory",
        "original_value": f"{source.get('category', '')} / {source.get('subcategory', '')}",
        "enriched_value": f"{category} / {subcategory}",
        "confidence": product_type.get("confidence", ""),
        "provenance": _sources(product_type),
        "status": "review" if classification_needs_review else "accepted",
        "attention_reason": (conflict_details.get("product_type") or {}).get("reason", ""),
        "decision": "eliminated_for_identity_review" if classification_needs_review else "accepted",
        "decision_reason": classification_reason,
    }]
    for field, value in (result.get("attributes") or {}).items():
        if not isinstance(value, dict):
            continue
        conflict = conflict_details.get(field)
        status = "corrected" if conflict else _review_status(value)
        if conflict and classification_needs_review:
            decision = "correction_not_published"
            decision_reason = "The visual correction was recorded, but the product was not published because its identity remains unresolved."
        elif conflict:
            decision = "published_with_visual_correction"
            decision_reason = "The attribute is directly visible, so the visual value replaced the conflicting source value in the published product and enriched description."
        elif status == "accepted" and classification_needs_review:
            decision = "value_not_published"
            decision_reason = "The field value passed validation, but the product was not published because its identity remains unresolved."
        elif status == "accepted":
            decision = "accepted"
            decision_reason = "The value passed taxonomy and evidence validation."
        else:
            decision = "omitted_not_available"
            decision_reason = "No sufficiently supported value was available, so the attribute was omitted without blocking the product."
        rows.append({
            "record_id": record_id,
            "source_row": row_number,
            "product_name": source.get("name", ""),
            "field": field,
            "original_value": (conflict or {}).get("source_value", ""),
            "enriched_value": (conflict or {}).get("visual_value", value.get("value") if value.get("value") is not None else ""),
            "confidence": value.get("confidence", ""),
            "provenance": _sources(value),
            "status": status,
            "attention_reason": (conflict or {}).get("reason", ""),
            "decision": decision,
            "decision_reason": decision_reason,
        })
    for claim in result.get("unsupported_claims") or []:
        claim_decision = "claim_not_published" if classification_needs_review else "claim_omitted"
        claim_reason = (
            "The claim was unsupported and the product was not published because its identity remains unresolved."
            if classification_needs_review
            else "The unsupported claim was excluded from grounded enrichment; the remaining product can still be published."
        )
        rows.append({
            "record_id": record_id, "source_row": row_number, "product_name": source.get("name", ""),
            "field": "unsupported_claim", "original_value": claim, "enriched_value": "", "confidence": "",
            "provenance": "source_text", "status": "review", "attention_reason": "Claim was not supported by the available evidence.",
            "decision": claim_decision,
            "decision_reason": claim_reason,
        })
    retry_corrections = result.get("_retry_corrections") or []
    if retry_corrections:
        rows.append({
            "record_id": record_id, "source_row": row_number, "product_name": source.get("name", ""),
            "field": "processing", "original_value": "", "enriched_value": "", "confidence": "",
            "provenance": "", "status": "review",
            "attention_reason": "Earlier model output rejected: " + "; ".join(retry_corrections),
            "decision": "published_after_model_retry",
            "decision_reason": "The invalid model output was discarded. A later response passed schema, taxonomy, applicability, and evidence validation, so the product was published.",
        })
    return rows


def _color_mismatch_review_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag published products whose name states a colour the record denies.

    This flags rather than holds. A colour word in a name is weaker evidence than
    a product-type noun -- it may describe a trim, a lens, or one colour of a
    multicoloured item -- so the product still publishes and a human decides.
    """
    rows = []
    for record in records:
        mismatch = color_mismatch(record.get("name", ""), record.get("primary_color"))
        if not mismatch:
            continue
        confidence = "high" if mismatch["unambiguous"] else "low"
        rows.append({
            "record_id": record.get("record_id", ""), "source_row": record.get("source_row", ""),
            "product_name": record.get("name", ""), "field": "primary_color",
            "original_value": mismatch["name_color"], "enriched_value": mismatch["primary_color"],
            "confidence": confidence, "provenance": "source_text", "status": "review",
            "attention_reason": (
                f"The product name states {mismatch['name_color']!r} but the published "
                f"primary_color is {mismatch['primary_color']!r}."
            ),
            "decision": "published_with_color_mismatch",
            "decision_reason": (
                "The name places the colour where it can only describe the product, so one of "
                "the two is wrong and primary_color is a filterable field."
                if mismatch["unambiguous"] else
                "The colour word may describe a trim or component rather than the product, or "
                "may be branding, so this is reported for confirmation rather than correction."
            ),
        })
    return rows


def _name_contradicts(
    source: dict[str, Any], result: dict[str, Any], classification: str | None,
) -> bool:
    """Whether the product name states a product type the classification denies."""
    if classification:
        published_type = CLASSIFICATION_TO_TYPE.get(tuple(classification.split("/", 1)))
    else:
        published_type = (result.get("product_type") or {}).get("value")
    signal = name_product_signal(source.get("name", ""))
    if not signal or not published_type:
        return False
    return not types_compatible(signal, published_type)


def _dropped_attribute_rows(
    record_id: str, row_number: int, source: dict[str, Any], result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Report attributes omitted because they could not be sourced legally."""
    rows = []
    for name, detail in (result.get("_dropped_attributes") or {}).items():
        filtered = name in ATTRIBUTE_VALUES
        rows.append({
            "record_id": record_id, "source_row": row_number, "product_name": source.get("name", ""),
            "field": name, "original_value": "", "enriched_value": "", "confidence": "",
            "provenance": "", "status": "review", "attention_reason": detail,
            "decision": "published_without_attribute",
            "decision_reason": (
                f"The product was published without {name}, which could not be evidenced within the "
                "retry budget. " + (
                    f"{name} is a filterable field, so this product will not appear when customers "
                    "filter on it." if filtered else
                    "No value was invented; the field is simply absent."
                )
            ),
        })
    return rows


def _outlier_review_row(
    record_id: str, row_number: int, source: dict[str, Any], resolution: dict[str, Any],
) -> dict[str, Any]:
    """Flag the one signal that disagreed with the published classification."""
    outlier = resolution["outlier"]
    original = source.get("name", "") if outlier == "name" else source.get("subcategory", "")
    return {
        "record_id": record_id, "source_row": row_number, "product_name": source.get("name", ""),
        "field": f"product_type:{outlier}", "original_value": original,
        "enriched_value": resolution["product_type"], "confidence": "",
        "provenance": "source_text" if outlier == "name" else "source_structured",
        "status": "review",
        "attention_reason": (
            f"The supplied {outlier} disagrees with the published classification "
            f"{resolution['product_type']!r}, which the other two signals support."
        ),
        "decision": "published_with_outlier_signal",
        "decision_reason": (
            f"Two of the three classification signals agree, so the product was published. "
            f"The {outlier} is likely wrong in the source catalog and should be corrected there."
        ),
    }


def _catalog_record(
    record_id: str,
    row_number: int,
    source: dict[str, Any],
    result: dict[str, Any],
    currency: str | None,
    classification: str | None = None,
    decision: Any = None,
) -> dict[str, Any]:
    product_type = result["product_type"]["value"]
    if classification:
        category, subcategory = classification.split("/", 1)
    else:
        category, subcategory = _classification(product_type)
    record: dict[str, Any] = {
        **source,
        "record_id": record_id,
        "source_row": row_number,
        "category": category,
        "subcategory": subcategory,
    }
    if currency:
        record["currency"] = currency
    conflicts = {
        item.get("field"): item
        for item in result.get("conflicts") or []
        if isinstance(item, dict) and item.get("field") != "product_type"
    }
    for field, value in (result.get("attributes") or {}).items():
        if field in conflicts and conflicts[field].get("visual_value") is not None:
            record[field] = conflicts[field]["visual_value"]
        elif isinstance(value, dict) and value.get("status") == "accepted" and value.get("value") is not None:
            record[field] = value["value"]
    enriched_description = (result.get("content") or {}).get("enriched_description")
    if enriched_description:
        record["enriched_description"] = enriched_description
    if decision is not None and getattr(decision, "attributes", None):
        # A reviewer corrected an attribute the model got wrong.
        record.update(decision.attributes)
    if decision is not None and getattr(decision, "name", None):
        # The original name is recorded in the decision ledger, not republished.
        # Nor is the merchant description, which describes the contradicted type.
        record["name"] = decision.name
        record["description"] = ""
    return record


def run_batch(
    input_csv: Path,
    images_dir: Path,
    output_dir: Path,
    *,
    locale: str = "en-US",
    currency: str | None = None,
    validate_only: bool = False,
    decisions_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and optionally enrich every CSV row."""
    output_dir.mkdir(parents=True, exist_ok=True)
    input_sha256 = _input_hash(input_csv)
    registry = None
    if decisions_path is not None:
        registry = load_decisions(decisions_path)
        # Row numbers only mean anything for one exact CSV.
        registry.verify_input(input_sha256)
        logger.info("Loaded %d reviewed decisions from %s", len(registry), decisions_path)

    with input_csv.open(newline="", encoding="utf-8-sig") as handle:
        source_rows = list(csv.DictReader(handle))
    duplicate_counts = Counter(duplicate_key(row) for row in source_rows if not _has_source_id(row))

    disposition_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    catalog_records: list[dict[str, Any]] = []
    eliminated_records: list[dict[str, Any]] = []
    decision_ledger: list[dict[str, Any]] = []

    for row_number, source in enumerate(source_rows, start=2):
        decision = registry.for_row(row_number) if registry else None
        record_id = record_id_for(input_csv, row_number, source)
        if decision and decision.record_id:
            # A reviewer distinguished otherwise-identical rows by giving each a stable id.
            record_id = decision.record_id
        audit = audit_row(source, images_dir)
        disposition = audit.disposition
        duplicate = not _has_source_id(source) and duplicate_counts[duplicate_key(source)] > 1
        if duplicate and decision and decision.resolves_reason("DUPLICATE_NAME_IMAGE"):
            duplicate = False

        result = None
        failure_reason = ""
        if duplicate:
            disposition = "REVIEW"
            review_rows.append({
                "record_id": record_id, "source_row": row_number, "product_name": source.get("name", ""),
                "field": "identity", "original_value": source.get("image", ""), "enriched_value": "", "confidence": "",
                "provenance": "source_structured", "status": "review",
                "attention_reason": "DUPLICATE_NAME_IMAGE: multiple rows share the same product name and image without a stable source identifier.",
                "decision": "eliminated_for_identity_review",
                "decision_reason": "No row was selected because the workflow cannot determine whether these are duplicates, variants, or separate products.",
            })
        elif not validate_only and audit.disposition != "FAIL" and audit.image_path and audit.content_type:
            try:
                result = enrich_product(source, audit.image_path.read_bytes(), audit.content_type, locale)
                if result.get("conflicts") or result.get("unsupported_claims") or result.get("_retry_corrections"):
                    disposition = "REVIEW"
            except Exception as exc:
                logger.exception("Fashion enrichment failed for %s", record_id)
                disposition = "FAIL"
                failure_reason = str(exc)
                review_rows.append({
                    "record_id": record_id, "source_row": row_number, "product_name": source.get("name", ""),
                    "field": "processing", "original_value": "", "enriched_value": "", "confidence": "",
                    "provenance": "", "status": "failed", "attention_reason": str(exc),
                    "decision": "eliminated_for_processing_failure",
                    "decision_reason": "No schema-valid enrichment was available after the bounded retry attempts.",
                })

        product_type_conflict = bool(result and any(
            isinstance(item, dict) and item.get("field") == "product_type"
            for item in result.get("conflicts") or []
        ))
        product_type_unresolved = bool(result and (result.get("product_type") or {}).get("status") != "accepted")

        if result is not None:
            review_rows.extend(_review_rows(record_id, row_number, source, result))
            review_rows.extend(_dropped_attribute_rows(record_id, row_number, source, result))
            contested = product_type_conflict or product_type_unresolved
            resolution = None
            if contested:
                resolution = resolve_product_type(source, (result.get("product_type") or {}).get("value") or "")
                if product_type_unresolved and resolution["outlier"] is None:
                    # The model itself was unsure of the value. Publish only when a
                    # signal specifically corroborates it, not merely fails to object.
                    resolution["publish"] = CORROBORATES in (
                        resolution["name_verdict"], resolution["column_verdict"],
                    )
            classification = decision.classification if decision and decision.classification else None
            incoherent = _name_contradicts(source, result, classification)
            if not validate_only and incoherent and not (decision and decision.name):
                # A name stating a different product type than the category is
                # incoherent to a shopper, however sound the taxonomy is.
                reasons = ["NAME_CONTRADICTS_CLASSIFICATION"]
                eliminated_records.append({
                    **source, "record_id": record_id, "source_row": row_number,
                    "elimination_reasons": reasons,
                    "elimination_explanations": _elimination_explanations(reasons, result),
                })
                if decision is not None:
                    decision_ledger.append(ledger_entry(decision, source, reasons, False))
            elif not validate_only and not contested:
                catalog_records.append(_catalog_record(
                    record_id, row_number, source, result, currency, classification, decision,
                ))
            elif not validate_only and resolution and resolution["publish"]:
                catalog_records.append(_catalog_record(
                    record_id, row_number, source, result, currency, classification, decision,
                ))
                disposition = "REVIEW"
                if resolution["outlier"]:
                    review_rows.append(_outlier_review_row(record_id, row_number, source, resolution))
            elif not validate_only:
                reasons = ["UNRESOLVED_PRODUCT_CLASSIFICATION"]
                reviewed = decision is not None and decision.resolves_reason(reasons[0])
                if reviewed:
                    catalog_records.append(_catalog_record(
                        record_id, row_number, source, result, currency,
                        decision.classification, decision,
                    ))
                    disposition = "REVIEW"
                else:
                    eliminated_records.append({
                        **source, "record_id": record_id, "source_row": row_number,
                        "elimination_reasons": reasons,
                        "elimination_explanations": _elimination_explanations(reasons, result),
                    })
                if decision is not None:
                    decision_ledger.append(ledger_entry(decision, source, reasons, reviewed))
        elif not validate_only:
            reasons = []
            if duplicate:
                reasons.append("DUPLICATE_NAME_IMAGE")
            reasons.extend(audit.issues)
            if failure_reason:
                reasons.append("MODEL_ENRICHMENT_FAILED")
            reasons = reasons or ["ENRICHMENT_NOT_AVAILABLE"]
            eliminated_records.append({
                **source, "record_id": record_id, "source_row": row_number,
                "elimination_reasons": reasons,
                "elimination_explanations": _elimination_explanations(reasons, detail=failure_reason),
            })
            if decision is not None:
                # A decision cannot supply enrichment that was never produced, so these
                # rows stay eliminated. The ledger records that the review was seen.
                decision_ledger.append(ledger_entry(decision, source, reasons, False))
        # Input problems are reported even in validate-only runs, which exist to
        # surface exactly these before spending model calls.
        if audit.issues:
            input_failed = audit.disposition == "FAIL"
            review_rows.append({
                "record_id": record_id, "source_row": row_number, "product_name": source.get("name", ""),
                "field": "input_validation" if input_failed else "image",
                "original_value": "" if input_failed else source.get("image", ""),
                "enriched_value": "", "confidence": "", "provenance": "",
                "status": "failed" if input_failed else "review",
                "attention_reason": ", ".join(audit.issues),
                "decision": "eliminated_for_invalid_input" if input_failed else "eliminated_for_missing_visual_evidence",
                "decision_reason": (
                    "The input row failed the required publication contract."
                    if input_failed else "The image could not be analyzed, so multimodal enrichment was not possible."
                ),
            })
        disposition_rows.append({"disposition": disposition})

    if catalog_records:
        with (output_dir / "enriched_products.jsonl").open("w", encoding="utf-8") as handle:
            for record in catalog_records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    if eliminated_records:
        with (output_dir / "eliminated_products.jsonl").open("w", encoding="utf-8") as handle:
            for record in eliminated_records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    review_rows.extend(_color_mismatch_review_rows(catalog_records))

    if decision_ledger:
        with (output_dir / "decision_ledger.jsonl").open("w", encoding="utf-8") as handle:
            for entry in decision_ledger:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    review_fields = [
        "record_id", "source_row", "product_name", "field", "original_value", "enriched_value",
        "confidence", "provenance", "status", "attention_reason", "decision", "decision_reason",
    ]
    _write_csv(output_dir / "enrichment_review.csv", review_rows, review_fields)

    try:
        vlm_config = VLMConfig.resolve()
    except Exception:  # config is unavailable in validate-only and test runs
        logger.debug("VLM config unavailable; manifest will not record the endpoint")
        vlm_config = None

    counts = {status: sum(row["disposition"] == status for row in disposition_rows) for status in ("PASS", "REVIEW", "FAIL", "SKIPPED")}
    summary = {
        "total": len(source_rows),
        "ready": len(catalog_records),
        "eliminated": len(eliminated_records),
        **{key.lower(): value for key, value in counts.items()},
        "validate_only": validate_only,
        "decisions_applied": sum(entry["outcome"] == "published" for entry in decision_ledger),
        "decisions_unresolved": sum(entry["outcome"] != "published" for entry in decision_ledger),
    }
    (output_dir / "batch_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    manifest = {
        "input_csv": str(input_csv),
        "input_sha256": input_sha256,
        "images_dir": str(images_dir),
        "taxonomy_version": TAXONOMY_VERSION,
        "attribute_version": ATTRIBUTE_VERSION,
        "publication_policy_version": PUBLICATION_POLICY_VERSION,
        "decisions_version": DECISIONS_VERSION if registry else None,
        "decisions_path": str(decisions_path) if decisions_path else None,
        "decisions_sha256": registry.file_sha256 if registry else None,
        # Enrichment is a model call, so a re-run is only as reproducible as the
        # endpoint behind it. Record which one produced this catalog.
        "vlm_endpoint": getattr(vlm_config, "url", None),
        "vlm_model": getattr(vlm_config, "model", None),
        "locale": locale,
        "currency": currency,
        "validate_only": validate_only,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and enrich a fashion catalog using the configured NIM models.")
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--images-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--locale", default="en-US")
    parser.add_argument("--currency")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--decisions",
        type=Path,
        help="Reviewed decision file letting named rows past the publication gate.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    summary = run_batch(
        args.input_csv, args.images_dir, args.output_dir,
        locale=args.locale, currency=args.currency, validate_only=args.validate_only,
        decisions_path=args.decisions,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
