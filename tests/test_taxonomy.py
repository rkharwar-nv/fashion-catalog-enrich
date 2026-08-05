# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for three-signal classification and partial-record publication.

The catalog carries three independent voices on what a product is: the merchant
product name, the subcategory column, and the image. These tests pin down when
two of them are enough to publish and when a genuine tie must go to a human.
"""

import pytest

from fashion_catalog.taxonomy import (
    COMPATIBLE,
    CONTRADICTS,
    CORROBORATES,
    SILENT,
    color_mismatch,
    column_verdict,
    name_product_signal,
    neutralize_attributes,
    partition_errors,
    resolve_product_type,
    validate_enrichment,
)


@pytest.mark.parametrize("name,expected", [
    ("Jewel Sequin Jumpsuit", "apparel.jumpsuits"),
    ("Vivacious Velvet Dress", "apparel.dresses"),
    ("Opulent Velvet Ballet Flats", "footwear.flats"),
    # "espadrille" names a sole construction, not a product type.
    ("Elegant Embroidered Espadrilles", None),
    ("Vintage Vignette Sunglasses", "eyewear.sunglasses"),
    # Two product types named at once cannot adjudicate between them.
    ("Woven Lace Blouse Sweater", None),
    ("Ultra Soft Cashmere Blend Sweater Blouse", None),
    ("Jeweled Bracelet Watch", None),
    ("Something Unrecognisable", None),
])
def test_name_product_signal(name, expected):
    assert name_product_signal(name) == expected


@pytest.mark.parametrize("subcategory,product_type,expected", [
    ("dress", "apparel.dresses", CORROBORATES),
    ("skirt", "apparel.skirts", CORROBORATES),
    ("dress", "apparel.jumpsuits", CONTRADICTS),
    # 'shoes' covers heels, flats, boots and sandals, so it cannot settle a
    # dispute between them.
    ("shoes", "footwear.heels", COMPATIBLE),
    ("top blouse sweater", "apparel.tops.blouses", COMPATIBLE),
    ("top blouse sweater", "apparel.dresses", CONTRADICTS),
    ("", "apparel.dresses", SILENT),
])
def test_column_verdict(subcategory, product_type, expected):
    assert column_verdict(subcategory, product_type) == expected


@pytest.mark.parametrize("name,subcategory,product_type,publish,outlier", [
    # Name and image agree; only the subcategory column is mislabelled.
    ("Jewel Sequin Jumpsuit", "dress", "apparel.jumpsuits", True, "subcategory"),
    ("Vivacious Velvet Dress", "top blouse sweater", "apparel.dresses", True, "subcategory"),
    # Column and image agree; the merchant name is misleading.
    ("Kaleidoscope Floral Print Dress", "skirt", "apparel.skirts", True, "name"),
    ("Jeweled Bracelet Watch", "bracelet", "jewelry.bracelets", True, None),
    # Name is silent and nothing contradicts the image.
    ("Woven Lace Blouse Sweater", "top blouse sweater", "apparel.tops.blouses", True, None),
    # A real tie: the name says flats, the image says heels, and 'shoes'
    # covers both. No signal can break it, so a human must.
    ("Opulent Velvet Ballet Flats", "shoes", "footwear.heels", False, None),
    # A heeled sandal is one product, so the name neither corroborates a
    # specific type nor contradicts the classification.
    ("Ocean Wave Espadrille Heels", "shoes", "footwear.sandals", True, None),
])
def test_resolve_product_type(name, subcategory, product_type, publish, outlier):
    resolution = resolve_product_type(
        {"name": name, "subcategory": subcategory}, product_type
    )
    assert resolution["publish"] is publish
    assert resolution["outlier"] == outlier


def test_partition_errors_separates_fatal_from_attribute():
    fatal, per_attribute = partition_errors([
        "content: enriched_description is required",
        "composition: image-only evidence is not allowed",
        "primary_color: invalid source",
    ])
    assert fatal == ["content: enriched_description is required"]
    assert sorted(per_attribute) == ["composition", "primary_color"]


def test_partition_errors_treats_unknown_fields_as_fatal():
    fatal, per_attribute = partition_errors(["invalid product_type"])
    assert fatal == ["invalid product_type"]
    assert per_attribute == {}


def _result() -> dict:
    return {
        "product_type": {"value": "eyewear.sunglasses", "status": "accepted",
                         "sources": ["image"]},
        "attributes": {
            "primary_color": {"value": "gold", "status": "accepted", "sources": ["image"]},
            "pattern": {"value": "solid", "status": "accepted", "sources": ["image"]},
            "composition": {"value": "metal", "status": "accepted", "sources": ["image"]},
            "care": {"value": None, "status": "unknown", "sources": []},
        },
        "content": {"enriched_description": "Round rose gold aviator sunglasses."},
        "conflicts": [],
    }


def test_image_only_composition_is_rejected():
    """The rule that lost row 43 is still enforced."""
    errors = validate_enrichment(_result(), {"name": "Aria Aviator Sunglasses"})
    assert any(error.startswith("composition:") for error in errors)


def test_neutralized_attribute_passes_validation_and_is_not_published():
    result = _result()
    neutralize_attributes(result, ["composition"])

    assert validate_enrichment(result, {"name": "Aria Aviator Sunglasses"}) == []
    # Present for the evidence-assessment requirement, but null and unknown, so
    # _catalog_record (which emits only accepted values) leaves it out.
    assert result["attributes"]["composition"] == {
        "value": None, "status": "unknown", "sources": [],
    }


def test_neutralizing_drops_related_conflicts():
    result = _result()
    result["conflicts"] = [
        {"field": "composition", "source_value": "acetate", "visual_value": "metal",
         "reason": "Text says acetate, image shows metal."},
        {"field": "pattern", "source_value": "striped", "visual_value": "solid",
         "reason": "Image shows a solid pattern."},
    ]
    neutralize_attributes(result, ["composition"])

    assert [item["field"] for item in result["conflicts"]] == ["pattern"]


# --------------------------------------------------------------------------- #
# Colour mismatch between the merchant name and the published colour
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,primary_color,flagged,unambiguous", [
    # The name puts the colour where it can only describe the product.
    ("Sleek Stiletto Heels in Navy", "black", True, True),
    ("Felicity Flats in Navy", "navy", False, False),
    # Present but not positionally certain: may name a component or be branding.
    ("Navy Gradient Sunglasses", "black", True, False),
    ("Black Velvet Ankle Boots", "black", False, False),
    # Colour words that double as personal names only count in the "in X" slot.
    ("Jade Luxe Sunglasses", "gold", False, False),
    ("Aria Amber Aviator Sunglasses", "brown", False, False),
    ("Coral Silk Maxi Dress", "pink", False, False),
    # Shades resolve to their enum value rather than reading as a conflict.
    ("Ivory Satin Sheath Dress", "white", False, False),
    ("Burgundy Wrap Dress", "red", False, False),
    # A name citing one colour of a multicoloured product is not wrong.
    ("Kaleidoscope Print Midi Skirt in Red", "multicolor", False, False),
    # Nothing to compare against.
    ("Sleek Stiletto Heels in Navy", None, False, False),
])
def test_color_mismatch(name, primary_color, flagged, unambiguous):
    result = color_mismatch(name, primary_color)
    assert (result is not None) is flagged
    if flagged:
        assert result["unambiguous"] is unambiguous


def test_color_mismatch_reports_both_values():
    result = color_mismatch("Sleek Stiletto Heels in Navy", "black")
    assert result["name_color"] == "navy"
    assert result["primary_color"] == "black"


def test_espadrille_flat_is_not_a_sandal():
    """A closed-upper espadrille is a flat; the name must not vote for sandals."""
    assert name_product_signal("Elegant Embroidered Espadrilles") is None


# --------------------------------------------------------------------------- #
# target_audience
# --------------------------------------------------------------------------- #
def test_every_product_type_has_a_department_rule():
    """A type with no rule leaves its products out of a filterable field."""
    from fashion_catalog.taxonomy import AUDIENCE_BY_PRODUCT_TYPE, PRODUCT_ATTRIBUTES
    assert set(PRODUCT_ATTRIBUTES) == set(AUDIENCE_BY_PRODUCT_TYPE)


def test_target_audience_applies_to_every_product_type():
    from fashion_catalog.taxonomy import PRODUCT_ATTRIBUTES
    missing = [t for t, attrs in PRODUCT_ATTRIBUTES.items() if "target_audience" not in attrs]
    assert missing == []


def test_target_audience_values():
    from fashion_catalog.taxonomy import ATTRIBUTE_VALUES
    assert ATTRIBUTE_VALUES["target_audience"] == {"womens", "mens", "adult_all_genders", "kids"}


def test_prompt_prefers_adult_all_genders_over_a_gendered_default():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]
              / "src" / "fashion_catalog" / "models.py").read_text()
    assert "Prefer adult_all_genders" in source


def test_target_audience_can_be_sourced_from_a_merchant_column():
    """A merchant 'gender' or 'department' column should satisfy the evidence rule."""
    from fashion_catalog.taxonomy import STRUCTURED_SOURCE_FIELDS
    assert {"gender", "department", "audience"} <= STRUCTURED_SOURCE_FIELDS["target_audience"]


def test_prompt_forbids_reading_audience_off_the_model():
    """The rule that keeps this from becoming a guess about a person."""
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1]
              / "src" / "fashion_catalog" / "models.py").read_text()
    assert ("Never infer it from the appearance, body, presentation, or perceived gender "
            "of a person") in source
