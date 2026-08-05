# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fashion enrichment against the configured vision-language endpoint."""

import base64
import json
from typing import Any

from openai import OpenAI

from fashion_catalog.config import VLMConfig, locale_info
from fashion_catalog.json_utils import parse_llm_json
from fashion_catalog.taxonomy import ATTRIBUTE_VALUES, PRODUCT_ATTRIBUTES


def enrich_with_omni(
    source: dict[str, Any],
    image_bytes: bytes,
    content_type: str,
    locale: str,
    validation_errors: list[str] | None = None,
    previous_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reason over source text and image together, returning canonical JSON."""
    config = VLMConfig.resolve()
    info = locale_info(locale)
    client = OpenAI(base_url=config.url, api_key=config.api_key, timeout=config.timeout)
    retry_guidance = ""
    if validation_errors:
        retry_guidance = f"""

PREVIOUS RESPONSE:
{json.dumps(previous_result, ensure_ascii=False)}

PREVIOUS RESPONSE VALIDATION ERRORS:
{json.dumps(validation_errors, ensure_ascii=False)}

Repair the previous response and return the complete corrected object. Resolve every validation error, preserve fields unrelated to those errors, and do not invent new facts. The previous response contains the multimodal findings; treat validation errors as output-contract corrections, not as new product evidence.
"""
    task = (
        "Repair the previous multimodal fashion enrichment using the source row and validation feedback."
        if previous_result is not None
        else "Analyze the sold fashion product using the image and source row together."
    )
    prompt = f"""{task}

SOURCE ROW:
{json.dumps(source, ensure_ascii=False)}

ALLOWED PRODUCT TYPES AND ATTRIBUTES:
{json.dumps({key: sorted(value) for key, value in PRODUCT_ATTRIBUTES.items()})}

CONTROLLED ATTRIBUTE VALUES:
{json.dumps({key: sorted(value) for key, value in ATTRIBUTE_VALUES.items()})}

RULES:
- Return exactly one allowed product_type, or status needs_review when identity is unresolved.
- The image is authoritative for visible product type, color, pattern, shape, construction, and directly visible exterior components. Visible functional form/components determine product type; do not let a broad supplied subcategory override them.
- Source text is authoritative for exact composition, care, dimensions, hidden or internal features, and other nonvisual supplied facts. Never create a composition or care conflict from appearance.
- Multiple closure components may coexist on one product. Do not report a closure conflict merely because one visible exterior component differs from a supplied hidden, internal, or functional closure.
- Multiple carrying methods may coexist on one product. The way a product is presented in one image does not disprove a supplied additional handle or strap that may be detached, hidden, or out of frame.
- Absence from the image is not a contradiction for a nonvisual supplied fact.
- A conflict requires clear, directly visible evidence that is mutually exclusive with a source claim about the same attribute and the same product component.
- Do not report a conflict when the image is uncertain, the relevant detail is hidden, or the source and image refer to different components. Use unknown or not_visible when appropriate.
- For a clear visible attribute conflict, select the visual value, set the attribute status to conflicting with image in sources, and report the source value, visual value, and reason in conflicts.
- If a source claim changes the core product identity relative to clear visual evidence, report it as a product_type conflict rather than only as an attribute conflict.
- Garment length is visible only when the hem and enough body context are shown; otherwise return null with status not_visible.
- Use only applicable attributes and controlled values. composition and care may be free text.
- target_audience is the department a product is merchandised under, not a statement about any person. Take the merchant's value when supplied. Otherwise infer only from how the product itself is cut or constructed, and only when that is decisive. Never infer it from the appearance, body, presentation, or perceived gender of a person in the image. Prefer all_genders: use womens or mens only where the construction itself is specific, and all_genders for anything cut to be worn by anyone, which includes most accessories, bags, eyewear and jewellery. Where none of this settles it, return null with status unknown.
- Always return composition and care in attributes. Use accepted with source_text or source_structured when supplied, otherwise return null with status unknown. Never omit these evidence assessments.
- enriched_description may mention composition or care only when the same fact is accepted in attributes from source_text or source_structured. Never introduce a material or care claim from appearance.
- Every sources array may contain only these exact tokens: source_structured, source_text, image, image_ocr. Use image for visible evidence and never use a generic label for the complete row.
- Status is accepted, unknown, not_visible, not_applicable, conflicting, or needs_review.
- An unknown/not_visible value must be null and must not contain invented source evidence.
- Use unknown, not not_visible, when a nonvisual fact such as care is absent from source text.
- Never infer composition, care, dimensions, size, price, availability, audience, performance, or genuine precious materials from appearance.
- Visible color or finish does not contradict supplied material composition; a coated or colored material may look different.
- For genuine material claims, an explicit composition statement in the description outranks promotional material words in the product name. Flag the unsupported name claim and omit it from grounded content.
- unsupported_claims is only for unsupported objective claims such as composition, care, dimensions, performance, or genuine precious materials. Do not flag subjective styling, occasion, versatility, or mood language.
- Omit every item reported in unsupported_claims from enriched_description.
- Do not create occasion, formality, aesthetic, mood, or trend fields.
- Write one natural, standalone enriched_description in {info['language']} for {info['region']}. Combine useful visible details with trustworthy source facts so this single field is suitable for semantic search. For a resolved visible conflict, use the selected visual value naturally and omit the contradicted source claim. Describe the product directly; never mention the image, source, description, visual analysis, evidence, model, review process, or disagreement. Do not write a keyword list.

Return one JSON object only with:
- product_type: value, confidence (0-1), status, sources
- attributes: a JSON OBJECT keyed by exact allowed attribute names; each value is an object with value, confidence (0-1), status, sources. Never return attributes as an array.
- conflicts: JSON array of objects; every object has exactly field, source_value, visual_value, and reason
- unsupported_claims: array of strings
- content: enriched_description
No markdown or additional keys.{retry_guidance}"""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if previous_result is None:
        content.insert(0, {
            "type": "image_url",
            "image_url": {"url": f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"},
        })
    response = client.chat.completions.create(
        model=config.model,
        messages=[{"role": "user", "content": content}],
        temperature=0.0,
        top_p=1,
        max_tokens=8192,
        stream=False,
        response_format={"type": "json_object"},
    )
    text = response.choices[0].message.content or ""
    parsed = parse_llm_json(text, extract_braces=True, strip_comments=True)
    if not isinstance(parsed, dict):
        raise ValueError("Fashion Omni enrichment returned invalid JSON")
    return parsed
