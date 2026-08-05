# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for single-call fashion enrichment against a configurable VLM."""

import json
from types import SimpleNamespace

import pytest

from fashion_catalog.single_call import (
    FASHION_SCHEMA,
    FashionEnrichmentError,
    SingleCallConfig,
    build_prompt,
    enrich_product,
)

IMAGE_BYTES = b"\xff\xd8\xff\xe0fake-jpeg"


def _config(**overrides) -> SingleCallConfig:
    base = dict(url="http://vlm:8000/v1", model="test/vlm", api_key="not-needed")
    base.update(overrides)
    return SingleCallConfig(**base)


def _record(**overrides) -> dict:
    record = {
        "title": "Ivory Silk Blouse",
        "description": "A softly draping ivory blouse.",
        "product_type": "blouse",
        "colors": ["ivory"],
    }
    record.update(overrides)
    return record


class _StubClient:
    """Minimal stand-in for the OpenAI client that records what it was sent."""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._responses.pop(0) if self._responses else ""
        return [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])
        ]

    def close(self):
        self.closed = True


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def test_from_env_requires_a_model(monkeypatch):
    monkeypatch.delenv("FASHION_VLM_MODEL", raising=False)
    monkeypatch.setenv("FASHION_VLM_API_KEY", "key")
    with pytest.raises(ValueError, match="FASHION_VLM_MODEL"):
        SingleCallConfig.from_env()


def test_from_env_requires_a_credential(monkeypatch):
    monkeypatch.setenv("FASHION_VLM_MODEL", "test/vlm")
    for name in ("FASHION_VLM_API_KEY", "NGC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="No API key"):
        SingleCallConfig.from_env()


def test_from_env_falls_back_to_ngc_key(monkeypatch):
    monkeypatch.setenv("FASHION_VLM_MODEL", "test/vlm")
    monkeypatch.delenv("FASHION_VLM_API_KEY", raising=False)
    monkeypatch.setenv("NGC_API_KEY", "ngc-key")
    monkeypatch.delenv("FASHION_VLM_EXTRA_BODY", raising=False)
    assert SingleCallConfig.from_env().api_key == "ngc-key"


def test_malformed_extra_body_is_rejected(monkeypatch):
    monkeypatch.setenv("FASHION_VLM_MODEL", "test/vlm")
    monkeypatch.setenv("FASHION_VLM_API_KEY", "key")
    monkeypatch.setenv("FASHION_VLM_EXTRA_BODY", "not json")
    with pytest.raises(ValueError, match="valid JSON"):
        SingleCallConfig.from_env()


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def test_prompt_carries_merchant_data_and_locale():
    prompt = build_prompt({"name": "Silk Blouse", "material": "silk"}, locale="fr-FR")
    assert "Silk Blouse" in prompt
    assert "French" in prompt and "France" in prompt


def test_prompt_handles_absent_merchant_data():
    prompt = build_prompt(None)
    assert "No merchant data was provided" in prompt


def test_unknown_locale_falls_back_to_us_english():
    assert "English" in build_prompt(None, locale="zz-ZZ")


def test_brand_voice_is_included_when_given():
    assert "playful and irreverent" in build_prompt(None, brand_voice="playful and irreverent")


# --------------------------------------------------------------------------- #
# Request shape
# --------------------------------------------------------------------------- #
def test_request_disables_thinking_and_merges_configured_extras():
    client = _StubClient(json.dumps(_record()))
    enrich_product(
        IMAGE_BYTES, "image/jpeg", client=client,
        config=_config(extra_body={"nvext": {"guided_json": True}}),
    )

    extra_body = client.calls[0]["extra_body"]
    assert extra_body["chat_template_kwargs"] == {"enable_thinking": False}
    assert extra_body["nvext"] == {"guided_json": True}


def test_configured_extras_can_clear_the_default():
    """An endpoint that rejects the field must be able to opt out."""
    client = _StubClient(json.dumps(_record()))
    enrich_product(
        IMAGE_BYTES, "image/jpeg", client=client,
        config=_config(extra_body={"chat_template_kwargs": {}}),
    )
    assert client.calls[0]["extra_body"]["chat_template_kwargs"] == {}


def test_image_is_sent_as_a_data_url():
    client = _StubClient(json.dumps(_record()))
    enrich_product(IMAGE_BYTES, "image/png", client=client, config=_config())

    content = client.calls[0]["messages"][1]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_injected_client_is_not_closed_by_the_caller():
    client = _StubClient(json.dumps(_record()))
    enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config())
    assert client.closed is False


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #
def test_record_matches_the_schema_exactly():
    client = _StubClient(json.dumps(_record(unexpected="dropped")))
    record = enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config())

    assert set(record) == set(FASHION_SCHEMA) | {"_meta"}
    assert "unexpected" not in record
    # Absent fields get their schema defaults rather than going missing.
    assert record["materials"] == []
    assert record["pattern"] is None
    assert record["confidence"] == {}


def test_scalar_and_comma_strings_are_coerced_to_lists():
    client = _StubClient(json.dumps(_record(colors="ivory, ecru , ivory", tags="silk")))
    record = enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config())

    # Whitespace trimmed, duplicates dropped, order preserved.
    assert record["colors"] == ["ivory", "ecru"]
    assert record["tags"] == ["silk"]


def test_json_wrapped_in_prose_is_still_parsed():
    client = _StubClient("Here you go:\n```json\n" + json.dumps(_record()) + "\n```")
    record = enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config())
    assert record["title"] == "Ivory Silk Blouse"


def test_meta_records_attempts_and_model():
    client = _StubClient("not json", json.dumps(_record()))
    record = enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config(), retries=1)

    assert record["_meta"] == {"attempts": 2, "locale": "en-US", "model": "test/vlm"}


def test_reasoning_only_stream_is_still_collected(monkeypatch):
    """Some reasoning VLMs stream the answer as reasoning_content."""
    payload = json.dumps(_record())

    class _ReasoningClient(_StubClient):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            return [SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, reasoning_content=payload)
            )])]

    client = _ReasoningClient()
    record = enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config())
    assert record["title"] == "Ivory Silk Blouse"


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
def test_a_record_without_a_product_type_is_not_usable():
    client = _StubClient(json.dumps({"title": "Something", "description": "..."}))
    with pytest.raises(FashionEnrichmentError, match="1 attempt"):
        enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config(), retries=0)


def test_retries_are_exhausted_before_failing():
    client = _StubClient("no", "still no", "nope")
    with pytest.raises(FashionEnrichmentError, match="3 attempt"):
        enrich_product(IMAGE_BYTES, "image/jpeg", client=client, config=_config(), retries=2)
    assert len(client.calls) == 3


@pytest.mark.parametrize("kwargs,match", [
    ({"image_bytes": b"", "content_type": "image/jpeg"}, "image_bytes"),
    ({"image_bytes": IMAGE_BYTES, "content_type": "application/pdf"}, "image/"),
])
def test_invalid_arguments_are_rejected(kwargs, match):
    with pytest.raises(ValueError, match=match):
        enrich_product(**kwargs, client=_StubClient(), config=_config())


def test_negative_retries_are_rejected():
    with pytest.raises(ValueError, match="retries"):
        enrich_product(
            IMAGE_BYTES, "image/jpeg", client=_StubClient(), config=_config(), retries=-1,
        )
