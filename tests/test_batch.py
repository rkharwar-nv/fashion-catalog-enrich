# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused tests for the additive fashion batch workflow."""

import csv
import json
from unittest.mock import patch

from fashion_catalog.audit import audit_row
from fashion_catalog.batch import run_batch
from fashion_catalog.service import enrich_product
from fashion_catalog.taxonomy import add_source_category_conflict, normalize_enrichment, validate_enrichment


def _valid_result():
    return {
        "product_type": {"value": "apparel.dresses", "confidence": 0.9, "status": "accepted", "sources": ["image"]},
        "attributes": {
            "pattern": {"value": "floral", "confidence": 0.9, "status": "accepted", "sources": ["image"]},
            "composition": {"value": "100% cotton", "confidence": 1.0, "status": "accepted", "sources": ["source_text"]},
            "care": {"value": None, "confidence": 0.0, "status": "unknown", "sources": []},
        },
        "content": {"title": "Floral Dress", "enriched_description": "A floral dress."},
    }


def test_taxonomy_accepts_valid_enrichment():
    assert validate_enrichment(_valid_result()) == []


def test_taxonomy_rejects_non_applicable_attribute():
    result = _valid_result()
    result["attributes"]["shaft_height"] = {"value": "ankle", "status": "accepted", "sources": ["image"]}
    assert "not applicable" in validate_enrichment(result)[0]


def test_taxonomy_rejects_image_only_composition():
    result = _valid_result()
    result["attributes"]["composition"]["sources"] = ["image"]
    assert "image-only evidence" in validate_enrichment(result)[0]


def test_taxonomy_requires_nonvisual_evidence_assessments():
    result = _valid_result()
    del result["attributes"]["composition"]

    errors = validate_enrichment(result)

    assert any(error.startswith("composition: evidence assessment is required") for error in errors)


def test_taxonomy_rejects_structured_composition_without_structured_field():
    result = _valid_result()
    result["attributes"]["composition"]["sources"] = ["source_structured"]

    errors = validate_enrichment(result, {"name": "Cotton Dress", "description": "Made from cotton."})

    assert "composition: source_structured evidence is unavailable" in errors[0]


def test_taxonomy_accepts_structured_composition_field():
    result = _valid_result()
    result["attributes"]["composition"]["sources"] = ["source_structured"]

    assert validate_enrichment(result, {"material": "100% cotton"}) == []


def test_taxonomy_relabels_verbatim_description_fact_as_source_text():
    result = _valid_result()
    result["attributes"]["composition"] = {
        "value": "100% cotton",
        "confidence": 1.0,
        "status": "accepted",
        "sources": ["source_structured"],
    }

    normalized = normalize_enrichment(result, {"description": "A dress made from 100% cotton."})

    assert normalized["attributes"]["composition"]["sources"] == ["source_text"]


def test_taxonomy_does_not_relabel_unverified_free_text_value():
    result = _valid_result()
    result["attributes"]["composition"] = {
        "value": "silk",
        "confidence": 0.8,
        "status": "accepted",
        "sources": ["source_structured"],
    }

    normalized = normalize_enrichment(result, {"description": "A formal dress."})

    assert normalized["attributes"]["composition"]["sources"] == ["source_structured"]


def test_taxonomy_rejects_value_when_status_is_unknown():
    result = _valid_result()
    result["attributes"]["care"] = {"value": "Machine wash", "status": "unknown", "sources": []}
    assert "unknown value must be null" in validate_enrichment(result)[0]


def test_taxonomy_requires_visual_conflict_to_match_selected_attribute():
    result = _valid_result()
    result["attributes"]["pattern"]["status"] = "conflicting"
    result["conflicts"] = [{
        "field": "pattern",
        "source_value": "solid",
        "visual_value": "stripe",
        "reason": "The two evidence sources disagree.",
    }]

    assert "pattern conflict: attribute value must match visual_value" in validate_enrichment(result)


def test_taxonomy_rejects_visual_correction_of_nonvisual_fact():
    result = _valid_result()
    result["attributes"]["composition"] = {
        "value": "metal",
        "confidence": 0.8,
        "status": "conflicting",
        "sources": ["source_text", "image"],
    }
    result["conflicts"] = [{
        "field": "composition",
        "source_value": "acetate",
        "visual_value": "metal",
        "reason": "Appearance differs from the supplied composition.",
    }]

    assert any(
        error.startswith("composition conflict: nonvisual facts cannot be visually corrected")
        for error in validate_enrichment(result)
    )


def test_taxonomy_normalizes_unique_leaf_and_empty_status_value():
    result = _valid_result()
    result["product_type"]["value"] = "dresses"
    result["attributes"]["care"] = {"value": "other", "status": "not_visible", "sources": []}

    normalized = normalize_enrichment(result)

    assert normalized["product_type"]["value"] == "apparel.dresses"
    assert normalized["attributes"]["care"]["value"] is None


def test_taxonomy_normalizes_common_evidence_source_names():
    result = _valid_result()
    result["attributes"]["pattern"]["sources"] = ["visual"]

    normalized = normalize_enrichment(result)

    assert normalized["attributes"]["pattern"]["sources"] == ["image"]


def test_taxonomy_normalizes_direct_description_content():
    result = _valid_result()
    result["content"] = "A grounded floral dress description."

    normalized = normalize_enrichment(result)

    assert normalized["content"] == {"enriched_description": "A grounded floral dress description."}


def test_taxonomy_flattens_nested_evidence_sources():
    result = _valid_result()
    result["attributes"]["pattern"]["sources"] = [["image"], "text"]

    normalized = normalize_enrichment(result)

    assert normalized["attributes"]["pattern"]["sources"] == ["image", "source_text"]


def test_taxonomy_discards_over_specific_product_type_suffix():
    result = _valid_result()
    result["product_type"]["value"] = "apparel.dresses.maxi"

    normalized = normalize_enrichment(result)

    assert normalized["product_type"]["value"] == "apparel.dresses"


def test_taxonomy_normalizes_singular_product_type_label():
    result = _valid_result()
    result["product_type"]["value"] = "apparel.dress"

    normalized = normalize_enrichment(result)

    assert normalized["product_type"]["value"] == "apparel.dresses"


def test_taxonomy_normalizes_product_type_inside_conflict():
    result = _valid_result()
    result["conflicts"] = [{
        "field": "product_type",
        "source_value": "skirt",
        "visual_value": "dresses",
        "reason": "The sources identify different products.",
    }]

    normalized = normalize_enrichment(result)

    assert normalized["conflicts"][0]["visual_value"] == "apparel.dresses"


def test_source_category_conflict_is_added_for_different_product_type():
    result = _valid_result()
    result["product_type"]["value"] = "apparel.jumpsuits"
    result["conflicts"] = []

    add_source_category_conflict(result, {"subcategory": "dress"})

    assert result["conflicts"][0]["field"] == "product_type"


def test_audit_missing_image_is_review(tmp_path):
    row = {"name": "Dress", "description": "Description", "category": "apparel", "subcategory": "dress", "price": "10", "image": "/images/missing.jpg"}
    result = audit_row(row, tmp_path)
    assert result.disposition == "REVIEW"
    assert result.issues == ("IMAGE_NOT_FOUND",)


def test_validation_only_writes_reports(tmp_path, sample_image_bytes):
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    (images / "dress.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    fields = ["category", "subcategory", "name", "description", "url", "price", "image"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"category": "apparel", "subcategory": "dress", "name": "Dress", "description": "A dress", "url": "/images/dress.png", "price": "20", "image": "/images/dress.png"})

    summary = run_batch(csv_path, images, output, validate_only=True)

    assert summary == {
        "total": 1, "ready": 0, "eliminated": 0, "pass": 1, "review": 0, "fail": 0, "skipped": 0,
        "validate_only": True, "decisions_applied": 0, "decisions_unresolved": 0,
    }
    assert (output / "enrichment_review.csv").exists()
    assert json.loads((output / "batch_summary.json").read_text())["pass"] == 1


def test_invalid_input_is_reported_as_failed(tmp_path, sample_image_bytes):
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    (images / "dress.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "description", "price", "image"])
        writer.writeheader()
        writer.writerow({"name": "", "description": "Description", "price": "invalid", "image": "/images/dress.png"})

    summary = run_batch(csv_path, images, output)
    review = next(csv.DictReader((output / "enrichment_review.csv").open()))

    assert summary["fail"] == 1
    assert review["field"] == "input_validation"
    assert review["status"] == "failed"
    assert "MISSING_REQUIRED_FIELD" in review["attention_reason"]
    assert "INVALID_PRICE" in review["attention_reason"]


def test_missing_image_is_reported_for_review(tmp_path):
    output = tmp_path / "output"
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "description", "price", "image"])
        writer.writeheader()
        writer.writerow({"name": "Dress", "description": "Description", "price": "20", "image": "/images/missing.png"})

    summary = run_batch(csv_path, tmp_path / "images", output)
    review = next(csv.DictReader((output / "enrichment_review.csv").open()))

    assert summary["review"] == 1
    assert review["field"] == "image"
    assert review["status"] == "review"
    assert review["attention_reason"] == "IMAGE_NOT_FOUND"
    eliminated = json.loads((output / "eliminated_products.jsonl").read_text())
    assert eliminated["elimination_reasons"] == ["IMAGE_NOT_FOUND"]
    assert "visual enrichment" in eliminated["elimination_explanations"][0]


@patch("fashion_catalog.batch.enrich_product", return_value=_valid_result())
def test_enriched_output_is_flat_and_review_is_explanatory(mock_enrich, tmp_path, sample_image_bytes):
    images = tmp_path / "images"
    output = tmp_path / "output"
    images.mkdir()
    (images / "dress.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    fields = ["category", "subcategory", "name", "description", "price", "image"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"category": "apparel", "subcategory": "dress", "name": "Dress", "description": "Original", "price": "20", "image": "/images/dress.png"})

    run_batch(csv_path, images, output)

    record = json.loads((output / "enriched_products.jsonl").read_text())
    assert record["category"] == "apparel"
    assert record["subcategory"] == "dresses"
    assert record["description"] == "Original"
    assert record["enriched_description"] == "A floral dress."
    assert record["pattern"] == "floral"
    assert "product_type" not in record
    assert "semantic_search_text" not in record
    assert record["record_id"].startswith("generated:")

    review = list(csv.DictReader((output / "enrichment_review.csv").open()))
    assert review[0]["field"] == "category/subcategory"
    assert review[0]["confidence"] == "0.9"
    assert review[0]["provenance"] == "image"


@patch("fashion_catalog.batch.enrich_product")
def test_classification_conflict_is_eliminated(mock_enrich, tmp_path, sample_image_bytes):
    result = _valid_result()
    result["conflicts"] = [{"field": "product_type", "source_value": "apparel.skirts", "visual_value": "apparel.dresses", "reason": "Source and image disagree."}]
    mock_enrich.return_value = result
    images = tmp_path / "images"
    images.mkdir()
    (images / "dress.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["category", "subcategory", "name", "description", "price", "image"])
        writer.writeheader()
        writer.writerow({"category": "apparel", "subcategory": "skirt", "name": "Product", "description": "Description", "price": "20", "image": "/images/dress.png"})

    summary = run_batch(csv_path, images, tmp_path / "output")

    assert summary["ready"] == 0
    assert summary["eliminated"] == 1
    assert not (tmp_path / "output" / "enriched_products.jsonl").exists()
    eliminated = json.loads((tmp_path / "output" / "eliminated_products.jsonl").read_text())
    assert eliminated["elimination_reasons"] == ["UNRESOLVED_PRODUCT_CLASSIFICATION"]
    assert len(eliminated["elimination_explanations"]) == 1
    assert "Input text/structured data says 'apparel.skirts'" in eliminated["elimination_explanations"][0]
    assert "visual analysis says 'apparel.dresses'" in eliminated["elimination_explanations"][0]
    review = list(csv.DictReader((tmp_path / "output" / "enrichment_review.csv").open()))
    pattern = next(row for row in review if row["field"] == "pattern")
    assert pattern["decision"] == "value_not_published"
    assert "identity remains unresolved" in pattern["decision_reason"]


@patch("fashion_catalog.batch.enrich_product")
def test_attribute_conflict_publishes_visual_correction(mock_enrich, tmp_path, sample_image_bytes):
    result = _valid_result()
    result["content"]["enriched_description"] = "A visually grounded floral dress."
    result["conflicts"] = [{"field": "pattern", "source_value": "solid", "visual_value": "floral", "reason": "Source and image disagree."}]
    mock_enrich.return_value = result
    images = tmp_path / "images"
    images.mkdir()
    (images / "dress.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["category", "subcategory", "name", "description", "price", "image"])
        writer.writeheader()
        writer.writerow({"category": "apparel", "subcategory": "dress", "name": "Product", "description": "Description", "price": "20", "image": "/images/dress.png"})

    summary = run_batch(csv_path, images, tmp_path / "output")

    assert summary["ready"] == 1
    assert summary["eliminated"] == 0
    assert not (tmp_path / "output" / "eliminated_products.jsonl").exists()
    product = json.loads((tmp_path / "output" / "enriched_products.jsonl").read_text())
    assert product["pattern"] == "floral"
    assert product["enriched_description"] == "A visually grounded floral dress."
    review = list(csv.DictReader((tmp_path / "output" / "enrichment_review.csv").open()))
    pattern = next(row for row in review if row["field"] == "pattern")
    assert pattern["original_value"] == "solid"
    assert pattern["enriched_value"] == "floral"
    assert pattern["status"] == "corrected"
    assert pattern["decision"] == "published_with_visual_correction"
    assert "replaced" in pattern["decision_reason"]


def test_ambiguous_duplicates_are_eliminated_without_model_calls(tmp_path, sample_image_bytes):
    images = tmp_path / "images"
    images.mkdir()
    (images / "bag.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "description", "price", "image"])
        writer.writeheader()
        writer.writerow({"name": "Same Bag", "description": "Identical", "price": "20", "image": "/images/bag.png"})
        writer.writerow({"name": "Same Bag", "description": "Identical", "price": "20", "image": "/images/bag.png"})

    with patch("fashion_catalog.batch.enrich_product") as mock_enrich:
        summary = run_batch(csv_path, images, tmp_path / "output")

    assert summary["ready"] == 0
    assert summary["eliminated"] == 2
    assert mock_enrich.call_count == 0
    eliminated = [json.loads(line) for line in (tmp_path / "output" / "eliminated_products.jsonl").read_text().splitlines()]
    assert eliminated[0]["record_id"] == eliminated[1]["record_id"]
    assert eliminated[0]["elimination_reasons"] == ["DUPLICATE_NAME_IMAGE"]
    assert "cannot determine" in eliminated[0]["elimination_explanations"][0]


def test_rows_sharing_a_name_and_image_are_distinct_when_price_differs(tmp_path, sample_image_bytes):
    """Reusing one image for two products is a merchant mistake, not an identity clash."""
    images = tmp_path / "images"
    images.mkdir()
    (images / "bag.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["name", "description", "price", "image"])
        writer.writeheader()
        writer.writerow({"name": "Same Bag", "description": "First", "price": "20", "image": "/images/bag.png"})
        writer.writerow({"name": "Same Bag", "description": "Second", "price": "30", "image": "/images/bag.png"})

    with patch("fashion_catalog.batch.enrich_product") as mock_enrich:
        mock_enrich.return_value = _valid_result()
        summary = run_batch(csv_path, images, tmp_path / "output")

    assert summary["eliminated"] == 0
    assert mock_enrich.call_count == 2
    records = [json.loads(line) for line in (tmp_path / "output" / "enriched_products.jsonl").read_text().splitlines()]
    # Distinct products must not collide on a generated id.
    assert records[0]["record_id"] != records[1]["record_id"]


@patch("fashion_catalog.service.enrich_with_omni")
def test_enrichment_retries_once_after_invalid_schema(mock_omni):
    invalid = _valid_result()
    invalid["attributes"] = []
    mock_omni.side_effect = [invalid, _valid_result()]

    result = enrich_product({"name": "Dress"}, b"image", "image/jpeg")

    assert result["product_type"]["value"] == "apparel.dresses"
    assert result["_retry_corrections"] == ["attributes: must be an object"]
    assert mock_omni.call_count == 2
    assert mock_omni.call_args_list[0].kwargs["validation_errors"] is None
    assert mock_omni.call_args_list[0].kwargs["previous_result"] is None
    assert mock_omni.call_args_list[1].kwargs["validation_errors"] == ["attributes: must be an object"]
    assert mock_omni.call_args_list[1].kwargs["previous_result"] is invalid


@patch("fashion_catalog.service.enrich_with_omni")
def test_enrichment_recovers_from_nonvisual_material_correction(mock_omni):
    invalid = _valid_result()
    invalid["attributes"]["composition"] = {
        "value": "metal",
        "confidence": 0.8,
        "status": "conflicting",
        "sources": ["source_text", "image"],
    }
    invalid["conflicts"] = [{
        "field": "composition",
        "source_value": "acetate",
        "visual_value": "metal",
        "reason": "Appearance differs from the supplied composition.",
    }]
    corrected = _valid_result()
    corrected["attributes"]["composition"] = {
        "value": "acetate",
        "confidence": 1.0,
        "status": "accepted",
        "sources": ["source_text"],
    }
    mock_omni.side_effect = [invalid, corrected]

    result = enrich_product({"name": "Sunglasses", "description": "Acetate sunglasses."}, b"image", "image/jpeg")

    error = (
        "composition conflict: nonvisual facts cannot be visually corrected; remove this conflict, preserve the "
        "supplied composition value with source_text evidence only, and remove the visual alternative from "
        "enriched_description"
    )
    assert result["attributes"]["composition"]["value"] == "acetate"
    assert result["_retry_corrections"] == [error]
    assert mock_omni.call_args_list[1].kwargs["validation_errors"] == [error]
    assert mock_omni.call_args_list[1].kwargs["previous_result"] is invalid


@patch("fashion_catalog.service.enrich_with_omni")
def test_enrichment_retries_once_after_parse_failure(mock_omni):
    mock_omni.side_effect = [ValueError("invalid JSON"), _valid_result()]

    result = enrich_product({"name": "Dress"}, b"image", "image/jpeg")

    assert result["product_type"]["value"] == "apparel.dresses"
    assert mock_omni.call_count == 2


@patch("fashion_catalog.service.enrich_with_omni")
def test_enrichment_allows_a_final_bounded_retry(mock_omni):
    invalid = _valid_result()
    invalid["product_type"]["value"] = "not-in-taxonomy"
    mock_omni.side_effect = [invalid, invalid, _valid_result()]

    result = enrich_product({"name": "Dress"}, b"image", "image/jpeg")

    assert result["product_type"]["value"] == "apparel.dresses"
    assert mock_omni.call_count == 3


@patch("fashion_catalog.batch.enrich_product")
def test_successful_model_retry_is_published_and_audited(mock_enrich, tmp_path, sample_image_bytes):
    result = _valid_result()
    result["_retry_corrections"] = ["composition conflict: nonvisual facts cannot be visually corrected"]
    mock_enrich.return_value = result
    images = tmp_path / "images"
    images.mkdir()
    (images / "dress.png").write_bytes(sample_image_bytes)
    csv_path = tmp_path / "products.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["category", "subcategory", "name", "description", "price", "image"])
        writer.writeheader()
        writer.writerow({"category": "apparel", "subcategory": "dress", "name": "Product", "description": "Description", "price": "20", "image": "/images/dress.png"})

    summary = run_batch(csv_path, images, tmp_path / "output")

    assert summary["ready"] == 1
    assert summary["eliminated"] == 0
    assert summary["review"] == 1
    product = json.loads((tmp_path / "output" / "enriched_products.jsonl").read_text())
    assert "_retry_corrections" not in product
    review = list(csv.DictReader((tmp_path / "output" / "enrichment_review.csv").open()))
    processing = next(row for row in review if row["field"] == "processing")
    assert processing["status"] == "review"
    assert processing["decision"] == "published_after_model_retry"
    assert "invalid model output was discarded" in processing["decision_reason"]
