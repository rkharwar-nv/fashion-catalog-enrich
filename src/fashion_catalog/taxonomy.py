# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Small, versioned taxonomy used by fashion enrichment."""

import re

from typing import Any, Iterable

TAXONOMY_VERSION = "fashion-product-types/0.1"
ATTRIBUTE_VERSION = "fashion-attributes/0.1"

COMMON_ATTRIBUTES = {"primary_color", "pattern", "composition", "care", "target_audience"}

PRODUCT_ATTRIBUTES = {
    "apparel.dresses": COMMON_ATTRIBUTES | {"neckline", "sleeve_length", "garment_length", "silhouette", "closure"},
    "apparel.skirts": COMMON_ATTRIBUTES | {"garment_length", "silhouette", "closure"},
    "apparel.tops.blouses": COMMON_ATTRIBUTES | {"neckline", "sleeve_length", "silhouette", "closure"},
    "apparel.tops.camisoles": COMMON_ATTRIBUTES | {"neckline", "sleeve_length"},
    "apparel.knitwear.sweaters": COMMON_ATTRIBUTES | {"neckline", "sleeve_length", "garment_length", "silhouette", "closure"},
    "apparel.jumpsuits": COMMON_ATTRIBUTES | {"neckline", "sleeve_length", "garment_length", "silhouette", "closure"},
    "footwear.boots": COMMON_ATTRIBUTES | {"toe_shape", "heel_type", "fastening", "shaft_height"},
    "footwear.sandals": COMMON_ATTRIBUTES | {"toe_shape", "heel_type", "fastening"},
    "footwear.flats": COMMON_ATTRIBUTES | {"toe_shape", "heel_type", "fastening"},
    "footwear.heels": COMMON_ATTRIBUTES | {"toe_shape", "heel_type", "fastening"},
    "footwear.other_shoes": COMMON_ATTRIBUTES | {"toe_shape", "heel_type", "fastening"},
    "bags.tote_bags": COMMON_ATTRIBUTES | {"carry_method", "bag_closure", "structure"},
    "bags.shoulder_bags": COMMON_ATTRIBUTES | {"carry_method", "bag_closure", "structure"},
    "bags.crossbody_bags": COMMON_ATTRIBUTES | {"carry_method", "bag_closure", "structure"},
    "bags.clutches": COMMON_ATTRIBUTES | {"carry_method", "bag_closure", "structure"},
    "bags.satchels": COMMON_ATTRIBUTES | {"carry_method", "bag_closure", "structure"},
    "bags.travel_bags": COMMON_ATTRIBUTES | {"carry_method", "bag_closure", "structure"},
    "bags.other_bags": COMMON_ATTRIBUTES | {"carry_method", "bag_closure", "structure"},
    "eyewear.sunglasses": COMMON_ATTRIBUTES | {"frame_shape", "lens_appearance"},
    "jewelry.bracelets": COMMON_ATTRIBUTES | {"jewelry_form", "metal_color"},
    "jewelry.earrings": COMMON_ATTRIBUTES | {"jewelry_form", "metal_color"},
    "jewelry.necklaces": COMMON_ATTRIBUTES | {"jewelry_form", "metal_color"},
    "jewelry.watches": COMMON_ATTRIBUTES | {"metal_color"},
}

ATTRIBUTE_VALUES = {
    "primary_color": {"black", "white", "gray", "silver", "gold", "brown", "beige", "red", "orange", "yellow", "green", "blue", "navy", "purple", "pink", "multicolor", "other"},
    "pattern": {"solid", "floral", "striped", "checked", "plaid", "polka_dot", "geometric", "abstract", "animal", "paisley", "graphic", "color_block", "other"},
    "neckline": {"crew", "v_neck", "scoop", "square", "boat", "halter", "high_neck", "turtleneck", "cowl", "sweetheart", "off_shoulder", "one_shoulder", "collared", "strapless", "other"},
    "sleeve_length": {"sleeveless", "short", "elbow", "three_quarter", "long", "other"},
    "garment_length": {"cropped", "mini", "knee_length", "midi", "maxi", "full_length", "other"},
    "silhouette": {"a_line", "straight", "fitted", "bodycon", "flared", "column", "fit_and_flare", "boxy", "other"},
    "closure": {"button", "zip", "hook_and_eye", "snap", "tie", "pull_on", "wrap", "open_front", "other"},
    "toe_shape": {"round", "pointed", "almond", "square", "open_toe", "other"},
    "heel_type": {"flat", "block", "stiletto", "kitten", "wedge", "platform", "other"},
    "fastening": {"slip_on", "buckle", "lace_up", "zip", "ankle_strap", "other"},
    "shaft_height": {"ankle", "mid_calf", "knee", "over_knee", "other"},
    "carry_method": {"handheld", "shoulder", "crossbody", "multiple", "other"},
    "bag_closure": {"zip", "magnetic", "snap", "buckle", "drawstring", "flap", "open", "other"},
    "structure": {"structured", "semi_structured", "soft", "basket", "other"},
    "frame_shape": {"aviator", "round", "square", "rectangular", "cat_eye", "oval", "geometric", "shield", "other"},
    "lens_appearance": {"clear", "dark", "gradient", "mirrored", "colored", "other"},
    "jewelry_form": {"chain", "beaded", "cuff", "bangle", "charm", "drop", "hoop", "stud", "pendant", "choker", "strand", "other"},
    "metal_color": {"gold_tone", "silver_tone", "rose_gold_tone", "mixed", "other"},
    # The department a product is merchandised under, not a statement about the
    # customer. "all_genders" covers everything cut to be worn by anyone, which
    # is how retailers increasingly shelve accessories, jewellery and much
    # apparel rather than defaulting them to a gendered aisle.
    "target_audience": {"womens", "mens", "all_genders", "kids"},
}

FREE_TEXT_ATTRIBUTES = {"composition", "care"}
STRUCTURED_SOURCE_FIELDS = {
    "composition": {"composition", "material", "materials", "fabric"},
    "care": {"care", "care_instructions"},
    "target_audience": {"target_audience", "audience", "gender", "department", "shop_for"},
}
STATUSES = {"accepted", "unknown", "not_visible", "not_applicable", "conflicting", "needs_review"}
SOURCES = {"source_structured", "source_text", "image", "image_ocr"}
SOURCE_ALIASES = {
    "visual": "image",
    "vision": "image",
    "text": "source_text",
    "description": "source_text",
    "source": "source_text",
    "structured": "source_structured",
    "ocr": "image_ocr",
}

SOURCE_SUBCATEGORY_PREFIXES = {
    "dress": ("apparel.dresses",),
    "skirt": ("apparel.skirts",),
    "top blouse sweater": ("apparel.tops.", "apparel.knitwear."),
    "shoes": ("footwear.",),
    "bag": ("bags.",),
    "sunglasses": ("eyewear.sunglasses",),
    "bracelet": ("jewelry.bracelets",),
    "earrings": ("jewelry.earrings",),
    "necklace": ("jewelry.necklaces",),
}


# Product-type keywords found in merchant product names, most specific first.
# The name is a third classification signal alongside the subcategory column and
# the image. A name that mentions several types is treated as saying nothing,
# not as voting for whichever keyword happens to appear first.
NAME_PRODUCT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("jumpsuit", "apparel.jumpsuits"),
    ("camisole", "apparel.tops.camisoles"),
    ("blouse", "apparel.tops.blouses"),
    ("sweater", "apparel.knitwear.sweaters"),
    ("cardigan", "apparel.knitwear.sweaters"),
    ("dress", "apparel.dresses"),
    ("skirt", "apparel.skirts"),
    ("sunglasses", "eyewear.sunglasses"),
    # "espadrille" is deliberately absent: it names a sole construction, not a
    # product type, and appears on flats, wedges and sandals alike. A term that
    # cannot pick one type must abstain rather than vote for a guess.
    ("boot", "footwear.boots"),
    ("sandal", "footwear.sandals"),
    ("flat", "footwear.flats"),
    ("heel", "footwear.heels"),
    ("pump", "footwear.heels"),
    ("stiletto", "footwear.heels"),
    ("clutch", "bags.clutches"),
    ("crossbody", "bags.crossbody_bags"),
    ("satchel", "bags.satchels"),
    ("tote", "bags.tote_bags"),
    ("shoulder bag", "bags.shoulder_bags"),
    ("travel bag", "bags.travel_bags"),
    ("bracelet", "jewelry.bracelets"),
    ("earring", "jewelry.earrings"),
    ("necklace", "jewelry.necklaces"),
    ("watch", "jewelry.watches"),
)

# Colour words that appear in merchant names, mapped to the primary_color enum.
# Shades map to the enum value they belong to; "navy" stays distinct from "blue"
# because the enum keeps them apart.
NAME_COLOR_KEYWORDS: dict[str, str] = {
    "black": "black", "white": "white", "ivory": "white", "cream": "white",
    "gray": "gray", "grey": "gray", "charcoal": "gray",
    "silver": "silver", "gold": "gold", "rose gold": "gold",
    "brown": "brown", "cognac": "brown", "chocolate": "brown", "mocha": "brown",
    "beige": "beige", "tan": "beige", "camel": "beige", "nude": "beige",
    "red": "red", "burgundy": "red", "merlot": "red", "wine": "red", "crimson": "red",
    "orange": "orange", "coral": "orange", "rust": "orange",
    "yellow": "yellow", "mustard": "yellow", "amber": "yellow",
    "green": "green", "olive": "green", "emerald": "green", "mint": "green", "jade": "green",
    "blue": "blue", "teal": "blue", "turquoise": "blue", "cobalt": "blue",
    "navy": "navy",
    "purple": "purple", "lavender": "purple", "violet": "purple", "plum": "purple",
    "pink": "pink", "blush": "pink", "rose": "pink", "fuchsia": "pink",
}

# Colour words that double as personal or brand names. In a merchant name these
# are as likely to be branding as description -- "Jade Luxe Sunglasses",
# "Aria Amber Aviator" -- so they only count when the name puts them in a
# position that can only be a colour, such as "Flats in Navy".
AMBIGUOUS_COLOR_WORDS = frozenset({
    "amber", "coral", "jade", "olive", "rose", "violet", "hazel", "ruby",
    "emerald", "mint", "plum", "sage", "wine",
})


def name_color_signal(name: str) -> tuple[str | None, bool]:
    """Return the colour a merchant name states, and whether it is unambiguous.

    The second element is True when the name puts the colour where it can only
    be a colour -- "Sleek Stiletto Heels in Navy" -- and False when the word
    merely appears somewhere in the name, which may be branding.
    """
    text = f" {str(name or '').strip().casefold()} "
    trailing = re.search(r"\bin\s+([a-z ]+?)\s*$", text)
    if trailing:
        phrase = trailing.group(1).strip()
        for word, value in NAME_COLOR_KEYWORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", phrase):
                return value, True

    matched = {
        value for word, value in NAME_COLOR_KEYWORDS.items()
        if re.search(rf"\b{re.escape(word)}\b", text)
        and word not in AMBIGUOUS_COLOR_WORDS
    }
    if len(matched) == 1:
        return matched.pop(), False
    return None, False


def color_mismatch(name: str, primary_color: str | None) -> dict[str, Any] | None:
    """Report a merchant name whose stated colour differs from the published one.

    Returns None when there is no stated colour, none was published, or they
    agree. ``multicolor`` never conflicts: a name naming one colour of a
    multicoloured product is not wrong.
    """
    if not primary_color or primary_color == "multicolor":
        return None
    stated, unambiguous = name_color_signal(name)
    if not stated or stated == primary_color:
        return None
    return {"name_color": stated, "primary_color": primary_color, "unambiguous": unambiguous}


# How a signal relates to the visually determined product type.
CORROBORATES = "corroborates"
CONTRADICTS = "contradicts"
COMPATIBLE = "compatible"
SILENT = "silent"


# Product types that can legitimately describe one product, so a name citing one
# does not contradict a classification of the other. A heeled sandal and a heeled
# boot are ordinary products; a heeled flat is not.
COMPATIBLE_TYPES: frozenset[frozenset[str]] = frozenset({
    frozenset({"footwear.heels", "footwear.sandals"}),
    frozenset({"footwear.heels", "footwear.boots"}),
    frozenset({"footwear.flats", "footwear.sandals"}),
})


def types_compatible(first: str, second: str) -> bool:
    """Whether two product types can describe the same product."""
    return first == second or frozenset({first, second}) in COMPATIBLE_TYPES


def name_product_signal(name: str) -> str | None:
    """Return the product type a merchant name implies, or None if it is unclear.

    A name mentioning two different product types ("Woven Lace Blouse Sweater")
    cannot adjudicate between them, so it abstains rather than guessing.
    """
    text = f" {str(name or '').strip().casefold()} "
    matched = {product_type for keyword, product_type in NAME_PRODUCT_KEYWORDS if keyword in text}
    return matched.pop() if len(matched) == 1 else None


def _column_product_types(subcategory: str) -> tuple[str, ...]:
    """Product types the supplied subcategory column allows."""
    prefixes = SOURCE_SUBCATEGORY_PREFIXES.get(str(subcategory or "").strip().lower())
    if not prefixes:
        return ()
    return tuple(
        product_type for product_type in PRODUCT_ATTRIBUTES
        if any(product_type == prefix or product_type.startswith(prefix) for prefix in prefixes)
    )


def column_verdict(subcategory: str, product_type: str) -> str:
    """How the subcategory column relates to the visually determined type."""
    allowed = _column_product_types(subcategory)
    if not allowed:
        return SILENT
    if product_type not in allowed:
        return CONTRADICTS
    # A column naming exactly one type corroborates it. A coarse column such as
    # 'shoes' covers several types, so it cannot settle a dispute between them.
    return CORROBORATES if len(allowed) == 1 else COMPATIBLE


def name_verdict(name: str, product_type: str) -> str:
    signal = name_product_signal(name)
    if signal is None:
        return SILENT
    if signal == product_type:
        return CORROBORATES
    # A name citing a type that can describe the same product -- "Heeled Sandals"
    # against footwear.sandals -- neither corroborates a specific type nor
    # contradicts one, so it must not be counted as a disagreement.
    return COMPATIBLE if types_compatible(signal, product_type) else CONTRADICTS


def resolve_product_type(source: dict[str, Any], product_type: str) -> dict[str, Any]:
    """Weigh name, subcategory column, and image to decide whether to publish.

    The catalog carries three independent voices on what a product is. Publishing
    only when all three agree discards products whose merchant metadata is merely
    mislabelled, which is the common case. Publishing whenever any one agrees
    would ignore genuine ambiguity. So a specific corroborating signal wins, and
    a contradiction with no specific corroboration is left for a human.
    """
    name = str(source.get("name") or "")
    subcategory = str(source.get("subcategory") or "")
    name_result = name_verdict(name, product_type)
    column_result = column_verdict(subcategory, product_type)

    if name_result == CORROBORATES or column_result == CORROBORATES:
        publish = True
    else:
        publish = name_result != CONTRADICTS and column_result != CONTRADICTS

    outlier = None
    if publish:
        if name_result == CONTRADICTS:
            outlier = "name"
        elif column_result == CONTRADICTS:
            outlier = "subcategory"

    return {
        "publish": publish,
        "product_type": product_type,
        "name_verdict": name_result,
        "column_verdict": column_result,
        "name_signal": name_product_signal(name),
        "outlier": outlier,
    }


def _normalize_sources(sources: Any) -> Any:
    if not isinstance(sources, list):
        return sources
    flattened = [item for source in sources for item in (source if isinstance(source, list) else [source])]
    return [SOURCE_ALIASES.get(str(source).strip().lower(), source) for source in flattened]


def _singular(value: str) -> str:
    if value.endswith("ies"):
        return f"{value[:-3]}y"
    if value.endswith(("sses", "shes", "ches", "xes", "zes")):
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss"):
        return value[:-1]
    return value


def _normalize_product_type(value: Any) -> Any:
    if value in PRODUCT_ATTRIBUTES:
        return value
    raw_value = str(value or "").strip().lower()
    prefix_matches = [code for code in PRODUCT_ATTRIBUTES if raw_value.startswith(f"{code}.")]
    if prefix_matches:
        return max(prefix_matches, key=len)
    leaf = raw_value.rsplit(".", 1)[-1]
    matches = [code for code in PRODUCT_ATTRIBUTES if code.rsplit(".", 1)[-1] == leaf]
    if not matches:
        matches = [code for code in PRODUCT_ATTRIBUTES if _singular(code.rsplit(".", 1)[-1]) == _singular(leaf)]
    return matches[0] if len(matches) == 1 else value


def normalize_enrichment(value: dict[str, Any], source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply safe structural normalization before strict validation."""
    if isinstance(value.get("content"), str):
        value["content"] = {"enriched_description": value["content"]}

    product = value.get("product_type")
    if isinstance(product, dict):
        product["sources"] = _normalize_sources(product.get("sources"))
        product["value"] = _normalize_product_type(product.get("value"))

    conflicts = value.get("conflicts")
    if isinstance(conflicts, list):
        for conflict in conflicts:
            if isinstance(conflict, dict) and conflict.get("field") == "product_type":
                conflict["visual_value"] = _normalize_product_type(conflict.get("visual_value"))

    attributes = value.get("attributes")
    if isinstance(attributes, dict):
        source_text = " ".join(str((source or {}).get(field) or "") for field in ("name", "description")).casefold()
        for name, attribute in attributes.items():
            if isinstance(attribute, dict):
                if attribute.get("status") in {"unknown", "not_visible", "not_applicable"}:
                    attribute["value"] = None
                sources = attribute.get("sources")
                attribute["sources"] = _normalize_sources(sources)
                structured_fields = STRUCTURED_SOURCE_FIELDS.get(name)
                has_structured_evidence = bool(
                    structured_fields and any(str((source or {}).get(field) or "").strip() for field in structured_fields)
                )
                attribute_value = str(attribute.get("value") or "").strip().casefold()
                if (
                    source is not None
                    and structured_fields
                    and "source_structured" in (attribute.get("sources") or [])
                    and not has_structured_evidence
                    and attribute_value
                    and attribute_value in source_text
                ):
                    attribute["sources"] = list(dict.fromkeys(
                        "source_text" if item == "source_structured" else item
                        for item in attribute["sources"]
                    ))
    return value


def validate_enrichment(value: dict[str, Any], source: dict[str, Any] | None = None) -> list[str]:
    """Return validation errors without mutating model output."""
    errors: list[str] = []
    content = value.get("content")
    if not isinstance(content, dict) or not str(content.get("enriched_description") or "").strip():
        errors.append("content: enriched_description is required")
    product = value.get("product_type")
    product_type = product.get("value") if isinstance(product, dict) else None
    if product_type not in PRODUCT_ATTRIBUTES:
        return ["invalid product_type"]
    if product.get("status") not in STATUSES:
        errors.append("product_type: invalid status")
    if product.get("status") == "accepted" and not product.get("sources"):
        errors.append("product_type: accepted value requires a source")

    attributes = value.get("attributes")
    if attributes is None:
        attributes = {}
    if not isinstance(attributes, dict):
        return errors + ["attributes: must be an object"]
    for required_field in sorted(FREE_TEXT_ATTRIBUTES - attributes.keys()):
        errors.append(
            f"{required_field}: evidence assessment is required; use an accepted supplied value or null with "
            f"status unknown, and do not introduce {required_field} from appearance in enriched_description"
        )
    for name, attribute in attributes.items():
        if name not in PRODUCT_ATTRIBUTES[product_type]:
            errors.append(f"{name}: not applicable to {product_type}")
            continue
        if not isinstance(attribute, dict):
            errors.append(f"{name}: must be an object")
            continue
        status = attribute.get("status")
        if status not in STATUSES:
            errors.append(f"{name}: invalid status")
        sources = attribute.get("sources") or []
        if not isinstance(sources, list) or any(not isinstance(source, str) or source not in SOURCES for source in sources):
            errors.append(f"{name}: invalid source")
        attribute_value = attribute.get("value")
        if status in {"unknown", "not_visible", "not_applicable"} and attribute_value is not None:
            errors.append(f"{name}: {status} value must be null")
        if status == "accepted" and not sources:
            errors.append(f"{name}: accepted value requires a source")
        if name in ATTRIBUTE_VALUES and attribute_value not in ATTRIBUTE_VALUES[name] and attribute_value is not None:
            errors.append(f"{name}: invalid value")
        if name in FREE_TEXT_ATTRIBUTES and sources == ["image"] and attribute_value:
            errors.append(f"{name}: image-only evidence is not allowed")
        structured_fields = STRUCTURED_SOURCE_FIELDS.get(name)
        has_structured_evidence = source is None or bool(
            structured_fields and any(str(source.get(field) or "").strip() for field in structured_fields)
        )
        if structured_fields and "source_structured" in sources and not has_structured_evidence:
            errors.append(
                f"{name}: source_structured evidence is unavailable; use source_text when the value comes from "
                "the supplied name or description"
            )

    conflicts = value.get("conflicts") or []
    if not isinstance(conflicts, list):
        errors.append("conflicts: must be an array")
    else:
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                errors.append("conflicts: each item must be an object")
                continue
            field = conflict.get("field")
            source_value = conflict.get("source_value")
            visual_value = conflict.get("visual_value")
            if not field or source_value in (None, "") or visual_value in (None, "") or not conflict.get("reason"):
                errors.append("conflicts: field, source_value, visual_value, and reason are required")
                continue
            if field == "product_type":
                if visual_value not in PRODUCT_ATTRIBUTES:
                    errors.append("product_type conflict: invalid visual_value")
                continue
            if field in FREE_TEXT_ATTRIBUTES:
                errors.append(
                    f"{field} conflict: nonvisual facts cannot be visually corrected; remove this conflict, "
                    f"preserve the supplied {field} value with source_text evidence only, and remove the visual "
                    "alternative from enriched_description"
                )
                continue
            attribute = attributes.get(field)
            if not isinstance(attribute, dict):
                errors.append(f"{field} conflict: matching attribute is required")
                continue
            if attribute.get("value") != visual_value:
                errors.append(f"{field} conflict: attribute value must match visual_value")
            if "image" not in (attribute.get("sources") or []):
                errors.append(f"{field} conflict: visual correction requires image evidence")
    return errors


ALL_ATTRIBUTES = set().union(*PRODUCT_ATTRIBUTES.values())


# Which department a product is merchandised under, by product type.
#
# Dresses, skirts, blouses and camisoles are gendered garment terms. The rest is
# merchandising policy for one catalog rather than anything the product itself
# decides -- a tote has no cut, and heels are worn by anyone -- so these are a
# reviewed default, not an inference, and a different retailer would set them
# differently.
AUDIENCE_BY_PRODUCT_TYPE: dict[str, str] = {
    "apparel.dresses": "womens",
    "apparel.skirts": "womens",
    "apparel.tops.blouses": "womens",
    "apparel.tops.camisoles": "womens",
    "apparel.knitwear.sweaters": "womens",
    "apparel.jumpsuits": "womens",
    "footwear.boots": "womens",
    "footwear.flats": "womens",
    "footwear.heels": "womens",
    "footwear.sandals": "womens",
    "footwear.other_shoes": "womens",
    "eyewear.sunglasses": "womens",
    "jewelry.bracelets": "womens",
    "jewelry.earrings": "womens",
    "jewelry.necklaces": "womens",
    "jewelry.watches": "womens",
    "bags.clutches": "womens",
    "bags.crossbody_bags": "all_genders",
    "bags.other_bags": "all_genders",
    "bags.satchels": "all_genders",
    "bags.shoulder_bags": "all_genders",
    "bags.tote_bags": "all_genders",
    "bags.travel_bags": "all_genders",
}

# Departments that turn on an attribute rather than the product type. An aviator
# frame is merchandised across departments in a way the other frame shapes in
# this catalog are not.
AUDIENCE_BY_ATTRIBUTE: dict[tuple[str, str, str], str] = {
    ("eyewear.sunglasses", "frame_shape", "aviator"): "all_genders",
}


def derived_audience(
    category: str, subcategory: str, attributes: dict[str, Any] | None = None,
) -> str | None:
    """The department this catalog's rules assign, or None if none applies.

    An attribute rule wins over the product-type default, so a distinction the
    type cannot express -- an aviator frame among otherwise gendered eyewear --
    does not require a per-product decision.
    """
    product_type = next(
        (key for key in AUDIENCE_BY_PRODUCT_TYPE | {k[0]: None for k in AUDIENCE_BY_ATTRIBUTE}
         if key.split(".")[0] == category and key.split(".")[-1] == subcategory),
        None,
    )
    if product_type is None:
        return None
    for (rule_type, attribute, value), audience in AUDIENCE_BY_ATTRIBUTE.items():
        if rule_type == product_type and (attributes or {}).get(attribute) == value:
            return audience
    return AUDIENCE_BY_PRODUCT_TYPE.get(product_type)


def partition_errors(errors: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Split validation errors into record-fatal ones and per-attribute ones.

    An unusable product_type or a missing enriched_description means there is no
    publishable record. A single optional attribute that could not be sourced
    legally does not, so those are reported separately and can be dropped.
    """
    fatal: list[str] = []
    per_attribute: dict[str, list[str]] = {}
    for error in errors:
        field = error.split(":", 1)[0].strip()
        if field in ALL_ATTRIBUTES:
            per_attribute.setdefault(field, []).append(error)
        else:
            fatal.append(error)
    return fatal, per_attribute


def neutralize_attributes(value: dict[str, Any], names: Iterable[str]) -> None:
    """Mark attributes as unknown instead of dropping the whole record.

    The attribute stays present so the schema's evidence-assessment requirement
    is still met, but with a null value and 'unknown' status it is not emitted
    into the catalog record. No claim is invented, and no product is lost to one
    unsourceable field.
    """
    attributes = value.setdefault("attributes", {})
    for name in names:
        attributes[name] = {"value": None, "status": "unknown", "sources": []}
    conflicts = value.get("conflicts")
    if isinstance(conflicts, list):
        value["conflicts"] = [
            item for item in conflicts
            if not (isinstance(item, dict) and item.get("field") in set(names))
        ]


def add_source_category_conflict(value: dict[str, Any], source: dict[str, Any]) -> None:
    """Record a deterministic disagreement with the supplied subcategory."""
    subcategory = str(source.get("subcategory") or "").strip().lower()
    prefixes = SOURCE_SUBCATEGORY_PREFIXES.get(subcategory)
    product = value.get("product_type") or {}
    product_type = str(product.get("value") or "")
    if not prefixes or any(product_type == prefix or product_type.startswith(prefix) for prefix in prefixes):
        return
    conflicts = value.setdefault("conflicts", [])
    if any(item.get("field") == "product_type" for item in conflicts if isinstance(item, dict)):
        return
    conflicts.append({
        "field": "product_type",
        "source_value": subcategory,
        "visual_value": product_type,
        "reason": "Canonical product type differs from the supplied subcategory.",
    })
