# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The shipped sample must stay runnable, or the README's first command lies."""

import csv
from pathlib import Path

from fashion_catalog.batch import run_batch

SAMPLE = Path(__file__).resolve().parents[1] / "sample"


def test_sample_passes_preflight(tmp_path):
    summary = run_batch(
        SAMPLE / "products.csv", SAMPLE / "images", tmp_path / "out", validate_only=True,
    )

    assert summary["total"] == 5
    assert summary["pass"] == 4
    # One row deliberately references an image that does not exist, so the
    # sample demonstrates a reported input failure as well as a clean one.
    assert summary["review"] == 1

    rows = list(csv.DictReader((tmp_path / "out" / "enrichment_review.csv").open()))
    assert [r["attention_reason"] for r in rows] == ["IMAGE_NOT_FOUND"]


def test_sample_csv_matches_the_documented_contract():
    with (SAMPLE / "products.csv").open(newline="", encoding="utf-8") as handle:
        fields = csv.DictReader(handle).fieldnames
    assert fields == ["category", "subcategory", "name", "description", "url", "price", "image"]
