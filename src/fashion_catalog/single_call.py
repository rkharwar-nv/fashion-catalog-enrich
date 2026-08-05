# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Single-call fashion catalog enrichment against a configurable VLM endpoint.

This module replaces the multi-stage VLM + Nemotron pipeline with one
vision-language-model call. A capable VLM reasons over the product image and
the merchant-provided row *together*, so the reconciliation, anti-hallucination,
and localization work that the original pipeline split across five text-LLM
passes collapses into a single, image-grounded request.

The anti-hallucination and merchant-vs-image reconciliation rules are borrowed
directly from the original ``vlm.py`` pipeline (the pre-filter, enhance, and
merge-QA prompts) so output discipline is preserved.

Input shape:
    - image bytes + ``image/*`` content type
    - optional merchant ``product_data`` dict (any fields; reconciled, never
      blindly trusted)
    - ``locale`` for customer-facing text
    - optional ``brand_voice`` instructions

Output shape:
    A flat, typed :data:`FASHION_SCHEMA` record (see ``_empty_record``).

The endpoint is fully configurable through :class:`SingleCallConfig` (or ``FASHION_VLM_*``
environment variables), so the same code runs against a locally hosted VLM or a
remote OpenAI-compatible endpoint.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from openai import OpenAI

from fashion_catalog.json_utils import parse_llm_json

logger = logging.getLogger("catalog_enrichment.fashion_enrich")


# --------------------------------------------------------------------------- #
# Locale
# --------------------------------------------------------------------------- #
# Minimal, self-contained locale map. Extend as needed; unknown locales fall
# back to US English so the tool never hard-fails on an unrecognized code.
LOCALES: dict[str, dict[str, str]] = {
    "en-US": {"language": "English", "region": "the United States"},
    "en-GB": {"language": "English", "region": "the United Kingdom"},
    "fr-FR": {"language": "French", "region": "France"},
    "de-DE": {"language": "German", "region": "Germany"},
    "es-ES": {"language": "Spanish", "region": "Spain"},
    "it-IT": {"language": "Italian", "region": "Italy"},
    "ja-JP": {"language": "Japanese", "region": "Japan"},
}
_DEFAULT_LOCALE_INFO = {"language": "English", "region": "the United States"}


def _locale_info(locale: str) -> dict[str, str]:
    return LOCALES.get(locale, _DEFAULT_LOCALE_INFO)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def _default_extra_body() -> dict[str, Any]:
    """Parse optional endpoint-specific request extras from the environment.

    Every backend chat-completion call in this repository disables model
    "thinking" (see ``tests/test_llm_thinking_config.py``), so that is applied at
    the call site and is not this function's job. What comes back from here is
    merged on top, which lets a NIM deployment add fields or a strict
    OpenAI-compatible endpoint clear the default with
    ``FASHION_VLM_EXTRA_BODY='{"chat_template_kwargs": {}}'``.
    """
    raw = os.getenv("FASHION_VLM_EXTRA_BODY", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"FASHION_VLM_EXTRA_BODY must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("FASHION_VLM_EXTRA_BODY must be a JSON object.")
    return parsed


@dataclass
class SingleCallConfig:
    """Everything needed to reach a VLM endpoint, local or remote."""

    url: str
    model: str
    api_key: str
    timeout: float = 120.0
    temperature: float = 0.1
    top_p: float = 0.9
    max_tokens: int = 4096
    extra_body: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "VLMConfig":
        """Build a config from ``FASHION_VLM_*`` environment variables.

        Falls back to the original repo's ``NGC_API_KEY`` and to ``OPENAI_API_KEY``
        for the credential so existing setups keep working.
        """
        url = os.getenv("FASHION_VLM_URL", "https://integrate.api.nvidia.com/v1")
        model = os.getenv("FASHION_VLM_MODEL")
        if not model:
            raise ValueError(
                "FASHION_VLM_MODEL is not set. Point it at the VLM served by your "
                "endpoint, e.g. 'meta/llama-3.2-90b-vision-instruct'."
            )
        api_key = (
            os.getenv("FASHION_VLM_API_KEY")
            or os.getenv("NGC_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "No API key found. Set FASHION_VLM_API_KEY (or NGC_API_KEY / "
                "OPENAI_API_KEY). Use a placeholder such as 'not-needed' for "
                "local endpoints that do not authenticate."
            )
        return cls(
            url=url,
            model=model,
            api_key=api_key,
            timeout=float(os.getenv("FASHION_VLM_TIMEOUT", "120")),
            temperature=float(os.getenv("FASHION_VLM_TEMPERATURE", "0.1")),
            top_p=float(os.getenv("FASHION_VLM_TOP_P", "0.9")),
            max_tokens=int(os.getenv("FASHION_VLM_MAX_TOKENS", "4096")),
            extra_body=_default_extra_body(),
        )


# --------------------------------------------------------------------------- #
# Output schema
# --------------------------------------------------------------------------- #
# Field -> kind, where kind is "str", "list", "opt_str" (string or None), or
# "dict". This single definition drives both the empty record and normalization.
FASHION_SCHEMA: dict[str, str] = {
    "title": "str",            # customer-facing product name
    "description": "str",      # persuasive product-detail copy
    "product_type": "str",     # garment noun, e.g. "blouse", "chino"
    "category": "opt_str",     # coarse path, e.g. "women/tops"
    "gender": "opt_str",       # women | men | unisex | kids | null
    "colors": "list",          # visible colors
    "materials": "list",       # materials (merchant-provided or visibly obvious)
    "pattern": "opt_str",      # solid | striped | floral | ...
    "fit": "opt_str",          # slim | relaxed | oversized | ...
    "style": "list",           # casual | business-casual | athleisure | ...
    "occasion": "list",        # office | evening | everyday | ...
    "season": "list",          # spring | summer | fall | winter
    "care": "list",            # care instructions (only if provided/printed)
    "tags": "list",            # search keywords
    "confidence": "dict",      # {field: "low"|"medium"|"high"} for guessed fields
    "notes": "list",           # uncertainties / what could not be determined
}


class FashionEnrichmentError(Exception):
    """Raised when the VLM never returns a usable fashion record."""


def _empty_record() -> dict[str, Any]:
    defaults = {"str": "", "opt_str": None, "list": [], "dict": {}}
    return {field_name: defaults[kind] for field_name, kind in FASHION_SCHEMA.items()}


def _as_list(value: Any) -> list[Any]:
    """Coerce a scalar or comma string into a clean list; drop empties."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    else:
        items = [value]
    seen: list[Any] = []
    for item in items:
        if isinstance(item, str):
            item = item.strip()
        if item in (None, "", []):
            continue
        if item not in seen:
            seen.append(item)
    return seen


def _normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a parsed model object into the exact :data:`FASHION_SCHEMA` shape.

    Unknown keys are dropped; missing keys get schema defaults; types are
    coerced so downstream consumers get a predictable record every time.
    """
    record = _empty_record()
    for field_name, kind in FASHION_SCHEMA.items():
        if field_name not in raw:
            continue
        value = raw[field_name]
        if kind == "str":
            record[field_name] = value.strip() if isinstance(value, str) else str(value or "").strip()
        elif kind == "opt_str":
            text = value.strip() if isinstance(value, str) else (str(value).strip() if value not in (None, "") else "")
            record[field_name] = text or None
        elif kind == "list":
            record[field_name] = _as_list(value)
        elif kind == "dict":
            record[field_name] = value if isinstance(value, dict) else {}
    return record


def _is_usable(record: dict[str, Any]) -> bool:
    """A record is usable only if it names the product with a title and type."""
    return bool(record.get("title") and record.get("product_type"))


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
# Anti-hallucination and reconciliation rules distilled from the original
# vlm.py pre-filter / enhance / merge-QA prompts. The key difference: one model
# holds the image throughout, so there is no cross-model "identity regression"
# to repair.
_SYSTEM_PROMPT = (
    "You are a fashion catalog enrichment model. You see a product photo and "
    "read the merchant's row for that product, and you return one clean JSON "
    "record describing the garment. You reason over the image and the merchant "
    "text together and never emit anything but the requested JSON object."
)


def _schema_block() -> str:
    """A compact, self-documenting rendering of the target schema for the prompt."""
    return json.dumps(
        {
            "title": "compelling product name",
            "description": "persuasive product-detail copy, not a literal image inventory",
            "product_type": "garment noun e.g. blouse, chino, midi dress",
            "category": "coarse path e.g. women/tops or null",
            "gender": "women | men | unisex | kids | null",
            "colors": ["visible colors"],
            "materials": ["materials; merchant value wins over visual guess"],
            "pattern": "solid | striped | floral | ... | null",
            "fit": "slim | relaxed | oversized | ... | null",
            "style": ["casual", "business-casual", "..."],
            "occasion": ["office", "everyday", "..."],
            "season": ["spring", "summer", "fall", "winter"],
            "care": ["only if printed on the item or provided by the merchant"],
            "tags": ["search keywords"],
            "confidence": {"materials": "low", "season": "medium"},
            "notes": ["anything you could not determine from image or merchant data"],
        },
        indent=2,
        ensure_ascii=False,
    )


def build_prompt(
    product_data: Optional[dict[str, Any]],
    locale: str = "en-US",
    brand_voice: Optional[str] = None,
) -> str:
    """Assemble the single user prompt that folds every surviving pipeline rule."""
    info = _locale_info(locale)
    merchant_block = (
        f"\nMERCHANT-PROVIDED PRODUCT DATA (may be stale, partial, or wrong):\n"
        f"{json.dumps(product_data, indent=2, ensure_ascii=False)}\n"
        if product_data
        else "\nNo merchant data was provided; describe the garment from the image alone.\n"
    )
    brand_block = (
        f"\nBRAND VOICE: {brand_voice}\n" if brand_voice else ""
    )

    return f"""Enrich this fashion product into a single JSON object.
{merchant_block}{brand_block}
Write all customer-facing text (title, description) in {info['language']} for {info['region']}.

RECONCILIATION RULES (image and merchant data disagree — resolve them):
- The IMAGE is ground truth for everything visible: garment type, colors, pattern, fit, silhouette, and any readable printed/label text.
- If the merchant text conflicts with what you clearly see, the image wins; drop the conflicting merchant term.
- MATERIAL is the exception: composition cannot be verified from a photo, so keep the merchant's material term when provided. Only infer material from the image when the merchant gave none, and mark it "low" in "confidence".
- Absence from the image is NOT a contradiction. Keep compatible non-visible merchant metadata (brand, model/line, SKU, price, fabric) even though you cannot see it.
- Never combine two conflicting product identities. Pick the identity the image supports plus any non-conflicting merchant details.

ANTI-HALLUCINATION RULES:
- Only state facts visible in the image or present in the merchant data. Never invent specs.
- Do NOT infer measurements, weight, size, exact fabric composition, certifications, origin, price, or care instructions unless printed in the image or given by the merchant.
- Do not use size/scale claims (compact, oversized, lightweight) unless scale is visible or the merchant stated it — "fit" describes cut, not a fabricated measurement.
- For any field you had to guess rather than see or read, add it to "confidence" with "low"/"medium"/"high", and record what you could not determine in "notes".
- "description" is persuasive shopper copy, not a narration of raw pixels or OCR strings; generalize visible details into shopper-facing feature language.

Return ONLY the JSON object below, with every key present. Use null or [] where a value does not apply. No markdown, no code fences, no commentary.

SCHEMA:
{_schema_block()}"""


# --------------------------------------------------------------------------- #
# VLM call
# --------------------------------------------------------------------------- #
def _collect_stream_text(completion: Iterable[Any]) -> str:
    """Collect normal streamed content, falling back to reasoning content.

    Borrowed from vlm.py: some reasoning VLMs stream ``reasoning_content`` and
    only later emit ``content``; if a model returns its answer as reasoning we
    still want it.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    for chunk in completion:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if not delta:
            continue
        content = getattr(delta, "content", None)
        reasoning = getattr(delta, "reasoning_content", None)
        if isinstance(content, str) and content:
            content_parts.append(content)
        if isinstance(reasoning, str) and reasoning:
            reasoning_parts.append(reasoning)
    return "".join(content_parts) or "".join(reasoning_parts)


def _request_completion(
    client: OpenAI,
    config: SingleCallConfig,
    image_data_url: str,
    prompt: str,
) -> str:
    completion = client.chat.completions.create(
        model=config.model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                    {"type": "text", "text": prompt},
                ],
            },
        ],
        temperature=config.temperature,
        top_p=config.top_p,
        max_tokens=config.max_tokens,
        stream=True,
        # Thinking is disabled on every backend completion call in this repo;
        # configured extras merge on top so a deployment can add to it or clear
        # it for an endpoint that rejects the field.
        extra_body={"chat_template_kwargs": {"enable_thinking": False}, **config.extra_body},
    )
    return _collect_stream_text(completion).strip()


def enrich_product(
    image_bytes: bytes,
    content_type: str,
    *,
    product_data: Optional[dict[str, Any]] = None,
    locale: str = "en-US",
    brand_voice: Optional[str] = None,
    config: Optional[SingleCallConfig] = None,
    client: Optional[OpenAI] = None,
    retries: int = 1,
) -> dict[str, Any]:
    """Enrich one fashion product with a single VLM call.

    Args:
        image_bytes: Product image bytes.
        content_type: ``image/*`` MIME type of ``image_bytes``.
        product_data: Optional merchant row; reconciled against the image.
        locale: Locale for customer-facing text.
        brand_voice: Optional brand tone/style instructions.
        config: Endpoint configuration; defaults to :meth:`VLMConfig.from_env`.
        client: Optional pre-built OpenAI client (injectable for tests/pooling).
        retries: Extra attempts if the model returns unusable JSON (>= 0).

    Returns:
        A normalized :data:`FASHION_SCHEMA` record with a ``_meta`` block
        recording attempts and locale.

    Raises:
        ValueError: On invalid arguments.
        FashionEnrichmentError: If no attempt yields a usable record.
    """
    if not image_bytes:
        raise ValueError("image_bytes is required")
    if not isinstance(content_type, str) or not content_type.startswith("image/"):
        raise ValueError("content_type must be an image/* MIME type")
    if retries < 0:
        raise ValueError("retries must be zero or greater")

    config = config or SingleCallConfig.from_env()
    owns_client = client is None
    api_client = client or OpenAI(base_url=config.url, api_key=config.api_key, timeout=config.timeout)
    image_data_url = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode()}"
    prompt = build_prompt(product_data, locale, brand_voice)

    attempts = retries + 1
    last_preview = ""
    try:
        for attempt in range(1, attempts + 1):
            text = _request_completion(api_client, config, image_data_url, prompt)
            parsed = parse_llm_json(text, extract_braces=True, strip_comments=True)
            if isinstance(parsed, dict):
                record = _normalize_record(parsed)
                if _is_usable(record):
                    record["_meta"] = {"attempts": attempt, "locale": locale, "model": config.model}
                    logger.info(
                        "Fashion enrichment succeeded on attempt %d: type=%r colors=%s",
                        attempt,
                        record.get("product_type"),
                        record.get("colors"),
                    )
                    return record
            last_preview = text.replace("\n", "\\n")[:300]
            logger.warning(
                "Fashion enrichment attempt %d/%d produced no usable record; preview=%r",
                attempt,
                attempts,
                last_preview,
            )
    finally:
        if owns_client:
            api_client.close()

    raise FashionEnrichmentError(
        f"VLM did not return a usable fashion record after {attempts} attempt(s). "
        f"Last response preview: {last_preview!r}"
    )
