# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reviewed decisions that let named rows past the publication gate.

The gate eliminates a row whenever the evidence is genuinely ambiguous: the
merchant text disagrees with the image, two rows share an identity, or the
image is missing. Those are the right defaults, but they leave real products
out of the catalog until a human adjudicates them.

A decision file records those adjudications so a run reproduces them instead of
re-litigating them. Each decision names the row it applies to, the elimination
reasons it resolves, and the reviewer who made the call.

Decisions are bound to the SHA-256 of the CSV they were reviewed against. Row
numbers are only meaningful for one exact input file, so a decision file that
does not match the CSV being processed is rejected rather than misapplied.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fashion_catalog.taxonomy import ALL_ATTRIBUTES, ATTRIBUTE_VALUES

DECISIONS_VERSION = "fashion-decisions/0.1"

# Reason codes a reviewer is allowed to resolve. Everything else the gate can
# emit stays a hard stop:
#   IMAGE_NOT_FOUND / IMAGE_UNREADABLE  -- no visual evidence to adjudicate
#   MISSING_REQUIRED_FIELD / INVALID_PRICE -- the input row is simply invalid
#   MODEL_ENRICHMENT_FAILED / ENRICHMENT_NOT_AVAILABLE -- no record was produced,
#       and a reviewer cannot supply enrichment that does not exist
# These must stay in sync with ELIMINATION_EXPLANATIONS in batch.py; the test
# suite asserts every resolvable reason is one the gate can actually emit.
RESOLVABLE_REASONS = frozenset({
    "UNRESOLVED_PRODUCT_CLASSIFICATION",
    "DUPLICATE_NAME_IMAGE",
    "NAME_CONTRADICTS_CLASSIFICATION",
})


class DecisionError(ValueError):
    """Raised when a decision file is malformed or bound to a different CSV."""


@dataclass(frozen=True)
class Decision:
    """One reviewed adjudication for one CSV row."""

    source_row: int
    resolves: tuple[str, ...]
    reviewer: str
    rationale: str
    classification: str | None = None
    record_id: str | None = None
    name: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    exclude: bool = False

    def resolves_reason(self, reason: str) -> bool:
        return reason in self.resolves


@dataclass
class DecisionRegistry:
    """Decisions for one exact input CSV."""

    input_sha256: str
    decisions: dict[int, Decision] = field(default_factory=dict)
    path: Path | None = None
    file_sha256: str | None = None

    def for_row(self, source_row: int) -> Decision | None:
        return self.decisions.get(source_row)

    def verify_input(self, csv_sha256: str) -> None:
        """Reject decisions reviewed against a different CSV."""
        if self.input_sha256 != csv_sha256:
            raise DecisionError(
                f"Decision file was reviewed against CSV {self.input_sha256[:12]}… but this run "
                f"uses {csv_sha256[:12]}…. Row numbers are only valid for one exact input file; "
                "re-review the decisions against the current CSV."
            )

    def __len__(self) -> int:
        return len(self.decisions)


def _require(payload: dict[str, Any], key: str, source_row: Any) -> Any:
    if key not in payload or payload[key] in (None, ""):
        raise DecisionError(f"Decision for row {source_row} is missing required field {key!r}.")
    return payload[key]


def load_decisions(path: Path) -> DecisionRegistry:
    """Read a decision file. The first line is a header binding it to a CSV."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise DecisionError(f"Decision file {path} is empty.")

    header = json.loads(lines[0])
    if header.get("kind") != "decision_header":
        raise DecisionError(
            f"Decision file {path} must start with a {{'kind': 'decision_header'}} line "
            "naming the CSV these decisions were reviewed against."
        )
    version = header.get("decisions_version")
    if version != DECISIONS_VERSION:
        raise DecisionError(
            f"Decision file {path} declares {version!r}, but this code reads {DECISIONS_VERSION!r}."
        )
    input_sha256 = _require(header, "input_sha256", "header")

    registry = DecisionRegistry(
        input_sha256=input_sha256,
        path=path,
        file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    for line in lines[1:]:
        payload = json.loads(line)
        source_row = _require(payload, "source_row", "?")
        if not isinstance(source_row, int):
            raise DecisionError(f"Decision source_row must be an integer, got {source_row!r}.")
        if source_row in registry.decisions:
            raise DecisionError(f"Duplicate decision for row {source_row}.")

        resolves = tuple(_require(payload, "resolves", source_row))
        unknown = sorted(set(resolves) - RESOLVABLE_REASONS)
        if unknown:
            raise DecisionError(
                f"Decision for row {source_row} tries to resolve {unknown}, which a reviewer "
                f"cannot override. Resolvable reasons are {sorted(RESOLVABLE_REASONS)}."
            )
        classification = payload.get("classification")
        if classification is not None and classification.count("/") != 1:
            raise DecisionError(
                f"Decision for row {source_row} has classification {classification!r}; "
                "expected 'category/subcategory'."
            )
        if "NAME_CONTRADICTS_CLASSIFICATION" in resolves and not payload.get("name"):
            raise DecisionError(
                f"Decision for row {source_row} resolves NAME_CONTRADICTS_CLASSIFICATION but "
                "supplies no corrected 'name'. Publishing a product whose name contradicts its "
                "own category leaves the catalog incoherent, so the corrected name is required."
            )
        exclude = bool(payload.get("exclude", False))
        if exclude and (payload.get("classification") or payload.get("name") or payload.get("attributes")):
            raise DecisionError(
                f"Decision for row {source_row} excludes the row and also sets a published value. "
                "Excluding and correcting are different intents; use one or the other."
            )
        attributes = payload.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise DecisionError(f"Decision for row {source_row}: 'attributes' must be an object.")
        for attribute, value in attributes.items():
            if attribute not in ALL_ATTRIBUTES:
                raise DecisionError(
                    f"Decision for row {source_row} overrides unknown attribute {attribute!r}."
                )
            allowed = ATTRIBUTE_VALUES.get(attribute)
            if allowed and value not in allowed:
                raise DecisionError(
                    f"Decision for row {source_row} sets {attribute}={value!r}, which is not one "
                    f"of {sorted(allowed)}."
                )

        registry.decisions[source_row] = Decision(
            source_row=source_row,
            resolves=resolves,
            reviewer=_require(payload, "reviewer", source_row),
            rationale=_require(payload, "rationale", source_row),
            classification=classification,
            record_id=payload.get("record_id"),
            name=payload.get("name"),
            attributes=attributes,
            exclude=exclude,
        )
    return registry


def ledger_entry(decision: Decision, source: dict[str, Any], reasons: list[str], published: bool) -> dict[str, Any]:
    """Describe how a decision was applied, for the run's decision ledger."""
    return {
        "source_row": decision.source_row,
        "name": source.get("name", ""),
        "decisions_version": DECISIONS_VERSION,
        "gate_reasons": sorted(reasons),
        "resolved": sorted(set(reasons) & set(decision.resolves)),
        "unresolved": sorted(set(reasons) - set(decision.resolves)),
        "classification": decision.classification,
        "record_id": decision.record_id,
        "reviewer": decision.reviewer,
        "rationale": decision.rationale,
        "outcome": "published" if published else "still_eliminated",
    }
