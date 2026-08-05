# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The published example must satisfy the schema a consumer ingests it with.

The example ships a copy of a real consumer's schema so the contract can be
checked rather than asserted. A field the pipeline emits that the consumer does
not declare is invisible downstream; a field the consumer requires that the
pipeline omits breaks ingestion.
"""

import json
from pathlib import Path

import pytest

from fashion_catalog.taxonomy import ATTRIBUTE_VALUES, PRODUCT_ATTRIBUTES

yaml = pytest.importorskip("yaml")

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "fashion-catalog"
CATALOG = EXAMPLE / "enriched_products.jsonl"
SCHEMA = EXAMPLE / "consuming_schema.yaml"


@pytest.fixture(scope="module")
def catalog() -> list[dict]:
    return [json.loads(line) for line in CATALOG.read_text().splitlines() if line.strip()]


@pytest.fixture(scope="module")
def schema() -> dict:
    return yaml.safe_load(SCHEMA.read_text())


def test_catalog_emits_no_undeclared_field(catalog, schema):
    declared = (
        set(schema["fields"])
        | set(schema["record"].values())
        | set(schema["taxonomy"]["fields"])
        | {"source_row"}  # provenance back to the input row, not a catalog field
    )
    present = set().union(*(set(record) for record in catalog))
    assert sorted(present - declared) == []


def test_every_record_carries_what_the_consumer_maps(catalog, schema):
    """The consumer's record mapping names the fields it cannot render without."""
    required = [schema["record"][key] for key in ("product_id", "name", "image", "price")]
    missing = [
        (record["source_row"], field)
        for record in catalog
        for field in required
        if not str(record.get(field) or "").strip()
    ]
    assert missing == []


def test_a_description_is_always_resolvable(catalog, schema):
    """The consumer falls back to the merchant description, so one must resolve."""
    primary, fallback = schema["record"]["description"], schema["record"]["fallback_description"]
    unresolvable = [
        record["source_row"] for record in catalog
        if not str(record.get(primary) or "").strip()
        and not str(record.get(fallback) or "").strip()
    ]
    assert unresolvable == []


def test_declared_enum_fields_hold_taxonomy_values(catalog, schema):
    """A value outside its enum would not match any filter the consumer builds."""
    offenders = [
        (record["source_row"], field, record[field])
        for record in catalog
        for field, spec in schema["fields"].items()
        if spec.get("type") == "enum"
        and field in record
        and field in ATTRIBUTE_VALUES
        and record[field] not in ATTRIBUTE_VALUES[field]
    ]
    assert offenders == []


def test_attributes_are_applicable_to_their_product_type(catalog):
    """An attribute on a type that does not allow it is meaningless to a filter."""
    by_classification = {
        (product_type.split(".")[0], product_type.split(".")[-1]): attributes
        for product_type, attributes in PRODUCT_ATTRIBUTES.items()
    }
    offenders = [
        (record["source_row"], field)
        for record in catalog
        for field in record
        if field in ATTRIBUTE_VALUES
        and field not in by_classification.get((record["category"], record["subcategory"]), set())
    ]
    assert offenders == []
