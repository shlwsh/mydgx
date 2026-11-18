#!/usr/bin/env python3
"""Inventory source numerals and check their mapped uses in a brief."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


DIGIT = r"[0-9０-９]"
SIGN = r"[+＋\-−－±]"
NUMBER = re.compile(
    rf"(?:(?<![0-9０-９.．]){SIGN})?{DIGIT}+"
    rf"(?:[,，]{DIGIT}{{3}}(?!{DIGIT}))*(?:[.．]{DIGIT}+)*"
    rf"(?:[eE]{SIGN}?{DIGIT}+)?(?:\\?[%％‰])?"
    r"|[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+|[₀₁₂₃₄₅₆₇₈₉₊₋]+"
)
TYPOGRAPHY = str.maketrans(
    "０１２３４５６７８９．，＋−－％", "0123456789.,+--%"
)

# Optional numeric-review fields. Absent fields remain allowed.
EVIDENCE_TYPES = {
    "raw_observation",
    "derived_estimate",
    "model_example",
    "proxy_observation",
    "configuration",
    "bibliographic",
}
CHECK_STATUSES = {
    "consistent",
    "scope_difference",
    "conflict",
    "pending_trace",
    "literal_only",
    "一致",
    "口径差异",
    "冲突",
    "待追溯",
    "仅确认字面",
}


def normalized(literal):
    return literal.translate(TYPOGRAPHY).replace(r"\%", "%")


def read_source(path):
    data = path.read_bytes()
    return data.decode("utf-8-sig").splitlines(), hashlib.sha256(data).hexdigest()


def scan(lines):
    return {
        (line_number, match.start() + 1): match.group()
        for line_number, line in enumerate(lines, 1)
        for match in NUMBER.finditer(line)
    }


def initialize(source, ledger, scope):
    lines, digest = read_source(source)
    items = [
        {
            "id": f"N{index:04}",
            "line": line,
            "column": column,
            "text": text,
            "locator": "",
            "meaning": "",
            "claim": "",
            "evidence_type": "",
            "check_status": "",
            "basis": "",
            "use": None,
        }
        for index, ((line, column), text) in enumerate(scan(lines).items(), 1)
    ]
    with ledger.open("x", encoding="utf-8") as stream:
        json.dump(
            {"version": 1, "source_name": source.name, "source_sha256": digest,
             "scope": scope, "items": items},
            stream, ensure_ascii=False, indent=2,
        )
        stream.write("\n")
    print(f"Recorded {len(items)} numeric candidates in {ledger}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def positive_integer(value):
    return type(value) is int and value > 0


def check_inventory(items, lines):
    expected = scan(lines)
    missing = set(expected)
    by_id = {}
    occupied = set()
    for row in items:
        identifier = row.get("id")
        require(nonempty(identifier) and identifier not in by_id,
                f"Missing or duplicate item ID: {identifier}")
        by_id[identifier] = row
        line, column, literal = row.get("line"), row.get("column"), row.get("text")
        require(positive_integer(line) and line <= len(lines)
                and positive_integer(column) and nonempty(literal),
                f"{identifier}: invalid source location or text")
        start = column - 1
        require(lines[line - 1][start:start + len(literal)] == literal,
                f"{identifier}: source text differs at {line}:{column}")
        key = (line, column)
        if key in expected:
            require(expected[key] == literal, f"{identifier}: record the entire numeric token")
            missing.discard(key)
        span = {(line, col) for col in range(column, column + len(literal))}
        require(not (span & occupied), f"{identifier}: source spans overlap")
        occupied.update(span)
        for field in ("locator", "meaning", "claim"):
            require(nonempty(row.get(field)), f"{identifier}: missing {field}")
        # Optional T76-03 review fields: if any is set, all three must be valid.
        et = row.get("evidence_type")
        cs = row.get("check_status")
        basis = row.get("basis")
        review_touched = any(nonempty(x) if isinstance(x, str) else x for x in (et, cs, basis))
        if review_touched:
            require(nonempty(et) and et in EVIDENCE_TYPES,
                    f"{identifier}: evidence_type must be one of {sorted(EVIDENCE_TYPES)}")
            require(nonempty(cs) and cs in CHECK_STATUSES,
                    f"{identifier}: check_status invalid ({cs!r})")
            require(nonempty(basis), f"{identifier}: basis required when review fields are used")
    require(not missing, "Missing source tokens: " + ", ".join(
        f"{line}:{column} {expected[line, column]!r}" for line, column in sorted(missing)
    ))
    return by_id


def summarize_review(items):
    """Count review statuses for speed-read digests (T76-03)."""
    counts = {}
    flagged = []
    for row in items:
        if "alias_of" in row:
            continue
        status = row.get("check_status") or ""
        if not nonempty(status):
            continue
        counts[status] = counts.get(status, 0) + 1
        if status in {"conflict", "冲突", "pending_trace", "待追溯", "scope_difference", "口径差异"}:
            flagged.append(
                {
                    "id": row.get("id"),
                    "text": row.get("text"),
                    "claim": row.get("claim"),
                    "check_status": status,
                    "basis": row.get("basis"),
                }
            )
    return {"counts": counts, "flagged": flagged}


def brief_position(row, brief, tokens):
    identifier = row["id"]
    use = row.get("use")
    require(isinstance(use, dict), f"{identifier}: unmapped brief use")
    quote, literal = use.get("quote"), use.get("text")
    require(nonempty(quote) and nonempty(literal), f"{identifier}: missing use quote or text")
    start = brief.find(quote)
    require(start >= 0 and brief.find(quote, start + 1) < 0,
            f"{identifier}: brief quote missing or ambiguous")

    original = row["text"]
    automatic = NUMBER.fullmatch(original) is not None
    if normalized(original) != normalized(literal):
        require(not automatic and nonempty(use.get("equivalence")),
                f"{identifier}: numeric value changed from {original!r} to {literal!r}")

    pattern = re.escape(literal)
    if re.fullmatch(r"[A-Za-z]+(?:[ -][A-Za-z]+)*", literal):
        pattern = rf"(?<![A-Za-z]){pattern}(?![A-Za-z])"
    positions = [
        (start + match.start(), start + match.end())
        for match in re.finditer(pattern, quote)
    ]
    if NUMBER.fullmatch(literal):
        positions = [span for span in positions if span in tokens]
    ordinal = use.get("occurrence")
    if ordinal is None:
        require(len(positions) == 1, f"{identifier}: brief value missing or needs occurrence")
        return positions[0]
    require(positive_integer(ordinal) and ordinal <= len(positions),
            f"{identifier}: invalid brief occurrence")
    return positions[ordinal - 1]


def check_uses(by_id, brief):
    tokens = {match.span() for match in NUMBER.finditer(brief)}
    occupied = set()
    primary_count = 0
    for identifier, row in by_id.items():
        if "alias_of" in row:
            target = row["alias_of"]
            require(nonempty(target), f"{identifier}: missing alias target")
            primary = by_id.get(target)
            require(primary is not None and "alias_of" not in primary,
                    f"{identifier}: alias must point directly to a primary item")
            require(row.get("use") is None
                    and normalized(row["text"]) == normalized(primary["text"])
                    and all(row[field] == primary[field] for field in ("meaning", "claim")),
                    f"{identifier}: alias must repeat the same value, meaning and claim")
            continue
        start, end = brief_position(row, brief, tokens)
        span = set(range(start, end))
        require(not (span & occupied), f"{identifier}: another item already uses this brief span")
        occupied.update(span)
        primary_count += 1
    return primary_count


def check(source, ledger, brief):
    lines, digest = read_source(source)
    data = json.loads(ledger.read_text(encoding="utf-8"))
    require(isinstance(data, dict) and data.get("version") == 1,
            "Unsupported ledger version")
    require(nonempty(data.get("source_name")) and nonempty(data.get("scope")),
            "Missing source name or coverage scope")
    require(data.get("source_sha256") == digest, "Source text has changed since inventory")
    items = data.get("items")
    require(isinstance(items, list) and all(isinstance(row, dict) for row in items),
            "Expected a list of ledger items")
    by_id = check_inventory(items, lines)
    brief_text = brief.read_text(encoding="utf-8")
    # Mask hidden text without moving offsets used to reconcile the brief.
    brief_text = re.sub(r"<!--.*?-->", lambda match: " " * len(match.group()),
                        brief_text, flags=re.S)
    primary_count = check_uses(by_id, brief_text)
    review = summarize_review(items)
    print(f"PASS: {len(items)} source occurrences, {primary_count} distinct brief uses, "
          f"{len(items) - primary_count} verified repeat occurrences")
    if review["counts"]:
        print("REVIEW:", json.dumps(review["counts"], ensure_ascii=False))
        if review["flagged"]:
            print(f"FLAGGED: {len(review['flagged'])} items need reader attention")
    return review


def review_cmd(ledger):
    data = json.loads(ledger.read_text(encoding="utf-8"))
    require(isinstance(data, dict) and data.get("version") == 1,
            "Unsupported ledger version")
    items = data.get("items")
    require(isinstance(items, list), "Expected a list of ledger items")
    summary = summarize_review(items)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not any(
        s in {"conflict", "冲突"} for s in summary["counts"]
    ) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a new JSON ledger from UTF-8 source text")
    verify = commands.add_parser("check", help="verify the completed ledger against source and brief")
    review = commands.add_parser("review", help="summarize evidence_type/check_status/basis flags")
    for command in (init, verify):
        command.add_argument("source", type=Path)
        command.add_argument("ledger", type=Path)
    init.add_argument("--scope", default="Entire supplied source text",
                      help="identify the manuscript version and the inventoried scope")
    verify.add_argument("brief", type=Path, help="UTF-8 final brief text or Markdown")
    review.add_argument("ledger", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.source, args.ledger, args.scope)
        elif args.command == "review":
            return review_cmd(args.ledger)
        else:
            check(args.source, args.ledger, args.brief)
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
