# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Where the vision-language model lives, and how to talk to it.

Configuration is deliberately small: this package needs one endpoint, one model
name, and a credential. Everything else about a run -- the taxonomy, the
publication policy, the decisions -- is versioned data, not configuration.

Resolution order for each value: environment variable, then a YAML file if one
is present, then the default.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "https://integrate.api.nvidia.com/v1"
DEFAULT_CONFIG_PATH = Path("config.yaml")

# Locales the enrichment prompt knows how to write for. An unknown locale falls
# back to en-US rather than failing a run.
LOCALES: dict[str, dict[str, str]] = {
    "en-US": {"language": "English", "region": "the United States",
              "context": "American English with US terminology (e.g. 'sweater')"},
    "en-GB": {"language": "English", "region": "the United Kingdom",
              "context": "British English with UK terminology (e.g. 'jumper')"},
    "en-AU": {"language": "English", "region": "Australia", "context": "Australian English"},
    "en-CA": {"language": "English", "region": "Canada", "context": "Canadian English"},
    "es-ES": {"language": "Spanish", "region": "Spain", "context": "Castilian Spanish"},
    "es-MX": {"language": "Spanish", "region": "Mexico", "context": "Mexican Spanish"},
    "fr-FR": {"language": "French", "region": "France", "context": "Metropolitan French"},
    "fr-CA": {"language": "French", "region": "Canada", "context": "Canadian French"},
    "de-DE": {"language": "German", "region": "Germany", "context": "Standard German"},
    "it-IT": {"language": "Italian", "region": "Italy", "context": "Standard Italian"},
    "ja-JP": {"language": "Japanese", "region": "Japan", "context": "Standard Japanese"},
}
FALLBACK_LOCALE = "en-US"

API_KEY_NOT_SET = (
    "No API key found. Set FASHION_VLM_API_KEY, or NGC_API_KEY for an NVIDIA endpoint. "
    "A local endpoint that does not authenticate still needs a placeholder value."
)


def locale_info(locale: str) -> dict[str, str]:
    return LOCALES.get(locale, LOCALES[FALLBACK_LOCALE])


def _from_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:  # pragma: no cover - yaml is an optional convenience
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (loaded.get("vlm") or {}) if isinstance(loaded, dict) else {}


@dataclass(frozen=True)
class VLMConfig:
    """Everything needed to reach the model."""

    url: str
    model: str
    api_key: str
    timeout: float = 120.0

    @classmethod
    def resolve(cls, config_path: Path | None = None) -> "VLMConfig":
        """Build a config from the environment, falling back to a YAML file."""
        from_file = _from_yaml(config_path or DEFAULT_CONFIG_PATH)

        model = os.getenv("FASHION_VLM_MODEL") or from_file.get("model")
        if not model:
            raise ValueError(
                "No model configured. Set FASHION_VLM_MODEL to the model your endpoint "
                "serves, or provide a config.yaml with a vlm.model entry."
            )
        api_key = (
            os.getenv("FASHION_VLM_API_KEY")
            or os.getenv("NGC_API_KEY")
            or from_file.get("api_key")
        )
        if not api_key:
            raise ValueError(API_KEY_NOT_SET)
        return cls(
            url=os.getenv("FASHION_VLM_URL") or from_file.get("url") or DEFAULT_ENDPOINT,
            model=model,
            api_key=api_key,
            timeout=float(os.getenv("FASHION_VLM_TIMEOUT", from_file.get("timeout", 120))),
        )
