# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Walk the products that need a human call and record the decisions.

Rebuilds the catalog, finds every row a person has to adjudicate, shows the
evidence for each, and appends what you decide to the decision file. Then
rebuilds again and tells you where the finished catalog is.

Nothing is written until the walk-through finishes, and skipping is always an
option -- a row you skip stays exactly as it is.

Usage:
    PYTHONPATH=src python scripts/review_decisions.py \
        --input-csv  products.csv \
        --enrichment out/run-2 --enrichment out/run-1 \
        --gate-run   out/run-2 \
        --decisions  shared/decisions/products_extended.jsonl \
        --output-dir out/rebuild

    ... --list      show what needs attention and exit, changing nothing
"""

import argparse
import json
from pathlib import Path
from typing import Any

from fashion_catalog.cli.rebuild import rebuild
from fashion_catalog.taxonomy import ATTRIBUTE_VALUES, PRODUCT_ATTRIBUTES

CLASSIFICATIONS = sorted(
    f"{product_type.split('.')[0]}/{product_type.split('.')[-1]}"
    for product_type in PRODUCT_ATTRIBUTES
)

# Reasons a reviewer can act on, and what acting means for each.
ACTIONABLE = {
    "UNRESOLVED_PRODUCT_CLASSIFICATION": "the name and the image identify different products",
    "NAME_CONTRADICTS_CLASSIFICATION": "the name states a different product type than its category",
    "DUPLICATE_NAME_IMAGE": "rows are indistinguishable, so none is canonical",
}

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _hr(char: str = "─") -> str:
    return char * 74


def _prompt(question: str, options: list[tuple[str, str]]) -> str:
    """Ask a multiple-choice question. Returns the chosen key."""
    print(f"\n  {question}")
    for index, (_, label) in enumerate(options, start=1):
        print(f"    {index}. {label}")
    print(f"    s. skip — leave this row as it is")
    while True:
        answer = input("  > ").strip().lower()
        if answer in {"s", "skip", ""}:
            return "skip"
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        print("  Not one of the options.")


def _ask_value(label: str, allowed: list[str] | None) -> str | None:
    """Ask for a free value, optionally constrained to a list."""
    if allowed:
        print(f"  {DIM}allowed: {', '.join(allowed)}{RESET}")
    while True:
        answer = input(f"  {label} (blank to skip): ").strip()
        if not answer:
            return None
        if allowed and answer not in allowed:
            print(f"  {answer!r} is not allowed here.")
            continue
        return answer


def _ask_rationale() -> str | None:
    while True:
        answer = input("  why? (recorded in the ledger): ").strip()
        if answer:
            return answer
        print("  A rationale is required; it is what makes the decision auditable.")


def _needs_attention(
    ledger: list[dict[str, Any]], catalog: dict[int, dict[str, Any]],
) -> tuple[list, list]:
    """Every finding a person can act on, and every one they cannot.

    A held row is the obvious case, but a published product can need a call too:
    a colour that no filter will match, a classification that moved against the
    previous catalog, or two products a shopper cannot tell apart.
    """
    findings, blocked = [], []
    published_names: dict[str, list[int]] = {}
    for entry in ledger:
        if entry["published"]:
            published_names.setdefault(entry["name"], []).append(entry["source_row"])

    for entry in ledger:
        row = entry["source_row"]
        if entry.get("reason") == "REVIEWER_EXCLUDED":
            continue
        if not entry["published"]:
            if entry.get("reason") in ACTIONABLE:
                findings.append({"kind": entry["reason"], "entry": entry,
                                 "detail": entry.get("reason_detail", "")})
            else:
                blocked.append(entry)
            continue

        record = catalog.get(row, {})
        if entry.get("color_flag"):
            findings.append({
                "kind": "COLOR_FLAG", "entry": entry,
                "detail": f"{entry['color_flag']} [{entry.get('color_flag_confidence')} confidence]",
            })
        elif record.get("primary_color") in (None, "", "other"):
            # A filterable field with no usable value: the product appears in no
            # colour filter, exactly as if the field were missing.
            findings.append({
                "kind": "UNFILTERABLE_COLOR", "entry": entry,
                "detail": (f"primary_color is {record.get('primary_color')!r}, so this product "
                           "matches no colour filter."),
            })
        if "classification " in (entry.get("changes") or ""):
            findings.append({
                "kind": "RECLASSIFIED", "entry": entry,
                "detail": entry["changes"].split(";")[0],
            })
        if len(published_names.get(entry["name"], [])) > 1:
            others = [r for r in published_names[entry["name"]] if r != row]
            findings.append({
                "kind": "DUPLICATE_PUBLISHED_NAME", "entry": entry,
                "detail": f"shares its name with row(s) {others}, which shoppers cannot tell apart",
            })
    return findings, blocked


def _write_decision(path: Path, decision: dict[str, Any]) -> None:
    """Add a decision, merging into the row's existing one if there is one.

    A row can need more than one call -- naming a classification can leave the
    product name contradicting it -- but the file holds one decision per row, so
    the second call extends the first rather than duplicating it.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    header, rows = lines[0], [json.loads(line) for line in lines[1:]]
    existing = next((r for r in rows if r["source_row"] == decision["source_row"]), None)
    if existing is None:
        rows.append(decision)
    else:
        existing["resolves"] = sorted(set(existing.get("resolves", [])) | set(decision["resolves"]))
        for key in ("classification", "name", "record_id", "exclude"):
            if decision.get(key) is not None and key in decision:
                existing[key] = decision[key]
        if decision.get("attributes"):
            existing.setdefault("attributes", {}).update(decision["attributes"])
        existing["reviewer"] = decision["reviewer"]
        existing["rationale"] = f"{existing['rationale']} {decision['rationale']}".strip()
    path.write_text(
        "\n".join([header] + [json.dumps(r, ensure_ascii=False) for r in rows]) + "\n",
        encoding="utf-8",
    )


def _key(finding: dict[str, Any]) -> tuple[int, str]:
    """Identify a problem, not just a row: one row can have several."""
    return finding["entry"]["source_row"], finding["kind"]


def _show(finding: dict[str, Any]) -> None:
    entry = finding["entry"]
    print(f"\n{_hr()}")
    print(f"{BOLD}row {entry['source_row']} · {entry['name']}{RESET}  {DIM}{finding['kind']}{RESET}")
    print(f"  merchant category : {entry.get('source_category') or '—'}")
    if entry.get("visual_classification"):
        print(f"  image says        : {entry['visual_classification']}")
    if entry.get("name_signal"):
        print(f"  name implies      : {entry['name_signal']}")
    if finding.get("detail"):
        print(f"  {DIM}{finding['detail']}{RESET}")


def _decide(finding: dict[str, Any], reviewer: str) -> dict[str, Any] | None:
    """Ask what to do about one finding. Returns a decision payload or None."""
    entry = finding["entry"]
    kind, row = finding["kind"], entry["source_row"]
    colors = sorted(ATTRIBUTE_VALUES["primary_color"])

    if kind in {"COLOR_FLAG", "UNFILTERABLE_COLOR"}:
        question = ("The name states a colour the record denies."
                    if kind == "COLOR_FLAG" else
                    "No colour filter will match this product.")
        choice = _prompt(question, [
            ("color", "set primary_color"),
            ("exclude", "remove this product from the catalog"),
        ])
        if choice == "color":
            value = _ask_value("primary_color", colors)
            if value is None:
                return None
            return {"source_row": row, "resolves": [], "attributes": {"primary_color": value},
                    "reviewer": reviewer, "rationale": _ask_rationale()}

    elif kind in {"UNRESOLVED_PRODUCT_CLASSIFICATION", "RECLASSIFIED"}:
        question = ("Which classification is right?" if kind == "UNRESOLVED_PRODUCT_CLASSIFICATION"
                    else "This classification changed against the previous catalog. Keep it?")
        choice = _prompt(question, [
            ("classify", "name the classification to publish under"),
            ("exclude", "remove this product from the catalog"),
        ])
        if choice == "classify":
            value = _ask_value("category/subcategory", CLASSIFICATIONS)
            if value is None:
                return None
            resolves = (["UNRESOLVED_PRODUCT_CLASSIFICATION"]
                        if kind == "UNRESOLVED_PRODUCT_CLASSIFICATION" else [])
            return {"source_row": row, "resolves": resolves, "classification": value,
                    "reviewer": reviewer, "rationale": _ask_rationale()}

    elif kind == "NAME_CONTRADICTS_CLASSIFICATION":
        choice = _prompt("The name contradicts the category it was filed under.", [
            ("rename", "supply a corrected product name"),
            ("exclude", "remove this product from the catalog"),
        ])
        if choice == "rename":
            value = _ask_value("corrected name", None)
            if value is None:
                return None
            return {"source_row": row, "resolves": ["NAME_CONTRADICTS_CLASSIFICATION"],
                    "name": value, "reviewer": reviewer, "rationale": _ask_rationale()}

    elif kind == "DUPLICATE_PUBLISHED_NAME":
        choice = _prompt("Two published products share this name.", [
            ("rename", "give this one a distinct name"),
            ("exclude", "remove this one and keep the other"),
        ])
        if choice == "rename":
            value = _ask_value("distinct name", None)
            if value is None:
                return None
            return {"source_row": row, "resolves": [], "name": value,
                    "reviewer": reviewer, "rationale": _ask_rationale()}

    elif kind == "DUPLICATE_NAME_IMAGE":
        choice = _prompt("These rows cannot be told apart.", [
            ("exclude", "remove this row and keep the other"),
        ])
    else:
        return None

    if choice == "exclude":
        return {"source_row": row, "resolves": [], "exclude": True,
                "reviewer": reviewer, "rationale": _ask_rationale()}
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--enrichment", type=Path, required=True, action="append")
    parser.add_argument("--gate-run", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reviewer", help="Recorded against each decision; defaults to $USER")
    parser.add_argument("--list", action="store_true", help="Report only; change nothing")
    args = parser.parse_args()

    summary = rebuild(args.input_csv, args.enrichment, args.gate_run,
                      args.decisions, args.output_dir, args.baseline)
    def _load() -> tuple[list, list]:
        ledger = [json.loads(line) for line in
                  (args.output_dir / "rebuild_ledger.jsonl").read_text().splitlines()
                  if line.strip()]
        catalog = {json.loads(line)["source_row"]: json.loads(line) for line in
                   (args.output_dir / "enriched_products.jsonl").read_text().splitlines()
                   if line.strip()}
        return _needs_attention(ledger, catalog)

    findings, blocked = _load()

    print(f"\n{_hr('═')}")
    print(f"{BOLD}{summary['published']} of {summary['source_rows']} products published{RESET}")
    print(f"{_hr('═')}")
    if blocked:
        print(f"\n{len(blocked)} cannot be resolved by a decision:")
        for entry in blocked:
            print(f"  row {entry['source_row']:>4} {entry['name'][:38]:40} {entry['reason']}")
            print(f"       {DIM}{entry.get('reason_detail', '')[:150]}{RESET}")
    items = findings
    if not items:
        print("\nNothing needs a decision.")
    else:
        print(f"\n{len(items)} need{'s' if len(items) == 1 else ''} your call"
              f"{': ' if args.list else ''}")

    if args.list:
        for finding in items:
            entry = finding["entry"]
            print(f"  row {entry['source_row']:>4} {entry['name'][:34]:36} "
                  f"{finding['kind']:32} {finding['detail'][:60]}")
        print(f"\nCatalog as it stands: {args.output_dir / 'enriched_products.jsonl'}")
        return

    reviewer = args.reviewer or __import__("os").getenv("USER") or "unknown"
    total = 0
    # Keyed by row *and* problem: resolving a classification can leave the name
    # contradicting it, which is a second, different decision for the same row.
    seen: set[tuple[int, str]] = set()
    # Resolving one problem can surface another -- publishing a contested
    # classification can leave the name contradicting it -- so keep going until
    # nothing new appears rather than stopping with the row still held.
    while items:
        recorded = 0
        for finding in items:
            _show(finding)
            decision = _decide(finding, reviewer)
            seen.add(_key(finding))
            if decision:
                _write_decision(args.decisions, decision)
                recorded += 1
                print(f"  {DIM}recorded{RESET}")
            else:
                print(f"  {DIM}skipped{RESET}")
        total += recorded
        if not recorded:
            break

        summary = rebuild(args.input_csv, args.enrichment, args.gate_run,
                          args.decisions, args.output_dir, args.baseline)
        findings, blocked = _load()
        items = [f for f in findings if _key(f) not in seen]
        if items:
            print(f"\n{DIM}Resolving those surfaced {len(items)} more:{RESET}")

    if not total:
        print(f"\nNo decisions recorded. Catalog unchanged: "
              f"{args.output_dir / 'enriched_products.jsonl'}")
        return
    print(f"\n{total} decision(s) written to {args.decisions}")

    summary = rebuild(args.input_csv, args.enrichment, args.gate_run,
                      args.decisions, args.output_dir, args.baseline)
    print(f"\n{_hr('═')}")
    print(f"{BOLD}Catalog: {args.output_dir / 'enriched_products.jsonl'}{RESET}")
    print(f"  {summary['published']} of {summary['source_rows']} products published")
    if summary["excluded_by_reason"]:
        print(f"  held: {summary['excluded_by_reason']}")
    print(f"  what changed and why: {args.output_dir / 'reconciliation.csv'}")
    print(f"  what did not publish: {args.output_dir / 'dropped_products.csv'}")
    print(_hr('═'))


if __name__ == "__main__":
    main()
