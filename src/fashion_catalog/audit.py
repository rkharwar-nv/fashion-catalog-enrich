# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic input validation for fashion batch rows."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import mimetypes

from PIL import Image


@dataclass(frozen=True)
class AuditResult:
    score: int
    disposition: str
    issues: tuple[str, ...]
    image_path: Path | None
    content_type: str | None


def audit_row(row: dict[str, Any], images_dir: Path) -> AuditResult:
    issues: list[str] = []
    score = 0

    if str(row.get("name") or "").strip() and str(row.get("description") or "").strip():
        score += 25
    else:
        issues.append("MISSING_REQUIRED_FIELD")

    if str(row.get("category") or "").strip() and str(row.get("subcategory") or "").strip():
        score += 15

    try:
        if float(row.get("price")) < 0:
            raise ValueError
        score += 20
    except (TypeError, ValueError):
        issues.append("INVALID_PRICE")

    image_name = Path(str(row.get("image") or "")).name
    image_path = images_dir / image_name if image_name else None
    content_type = None
    if image_path and image_path.is_file():
        try:
            with Image.open(image_path) as image:
                image.verify()
                image_format = image.format
            content_type = Image.MIME.get(image_format) or mimetypes.guess_type(image_path.name)[0]
            if content_type and content_type.startswith("image/"):
                score += 40
            else:
                issues.append("IMAGE_UNREADABLE")
        except Exception:
            issues.append("IMAGE_UNREADABLE")
    else:
        issues.append("IMAGE_NOT_FOUND")

    if "MISSING_REQUIRED_FIELD" in issues or "INVALID_PRICE" in issues or "IMAGE_UNREADABLE" in issues:
        disposition = "FAIL"
    elif issues:
        disposition = "REVIEW"
    else:
        disposition = "PASS"
    return AuditResult(score, disposition, tuple(issues), image_path, content_type)
