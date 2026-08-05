# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Parsing JSON out of model responses.

Models wrap JSON in prose, fence it in markdown, and occasionally comment it.
This recovers the object without the caller having to care.
"""

import json
import re
from typing import Optional


def parse_llm_json(
    text: str,
    *,
    extract_braces: bool = False,
    strip_comments: bool = False,
) -> Optional[dict]:
    """Parse a JSON dict from a model response, tolerating common formatting.

    Returns the parsed dict, or None on any failure -- callers decide whether an
    unparseable response is worth a retry.
    """
    text = text.strip()

    for marker in ("```json", "```"):
        if marker in text:
            start = text.find(marker) + len(marker)
            end = text.find("```", start)
            if end > start:
                text = text[start:end].strip()
                break

    if extract_braces:
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            text = text[first_brace : last_brace + 1]

    if strip_comments:
        text = re.sub(r"//.*?(?=\n|$)", "", text)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None
