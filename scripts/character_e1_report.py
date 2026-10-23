"""Run deterministic E1 audits and write reviewable Markdown/HTML/JSON artifacts.

Examples:
  PYTHONPATH=src python scripts/character_e1_report.py
  PYTHONPATH=src python scripts/character_e1_report.py --responses my-capture.json --out build/my-e1
  PYTHONPATH=src python scripts/character_e1_report.py --responses new.json --baseline old.json --reviews ratings.json
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zhuangfy.evaluation.character_e1 import (
    audit, compare_reports, load_cases, load_responses, load_reviews, write_reports,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline E1 character evaluation report")
    parser.add_argument("--cases", type=Path,
        default=ROOT / "resources" / "evaluation" / "character_e1_cases.json")
    parser.add_argument("--responses", type=Path,
        default=ROOT / "resources" / "evaluation" / "character_e1_demo_responses.json")
    parser.add_argument("--baseline", type=Path, help="Optional comparison response bundle")
    parser.add_argument("--reviews", type=Path, help="Optional current-version human review scores")
    parser.add_argument("--baseline-reviews", type=Path, help="Optional baseline human review scores")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "character-e1-report")
    parser.add_argument("--require-full", action="store_true")
    parser.add_argument("--fail-on-hard", action="store_true")
    args = parser.parse_args(argv)
    cases = load_cases(args.cases)
    replies, metadata = load_responses(args.responses, cases)
    reviews = load_reviews(args.reviews, cases)
    report = audit(cases, replies, metadata, reviews)
    if args.baseline:
        base_replies, base_metadata = load_responses(args.baseline, cases)
        report["comparison"] = compare_reports(
            report, audit(cases, base_replies, base_metadata,
                          load_reviews(args.baseline_reviews, cases)))
    write_reports(report, cases, args.out)
    info = report["summary"]
    print(f"E1: source={report['source']} variant={report['variant']} "
          f"captured={info['captured']}/{info['cases']} "
          f"hard_failures={info['hard_failures']} human_reviews={info['human_reviewed']}")
    print("Reports:", *(str(args.out / name) for name in
          ("summary.md", "details.json", "report.html", "review-template.json")), sep="\n  ")
    if args.require_full and info["missing"]:
        print("E1 incomplete: required response cases are missing", file=sys.stderr)
        return 2
    if args.fail_on_hard and info["hard_failures"]:
        print("E1 failed declared deterministic hard checks", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
