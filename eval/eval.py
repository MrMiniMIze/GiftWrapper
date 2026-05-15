"""
eval.py — Gift Whisperer evaluation script
Metric: pass_rate = (# suggestions satisfying ALL constraints) / (total suggestions)
A suggestion passes if:
  1. URL resolves (HTTP 200/3xx)
  2. Price is within budget (LLM-reported, accepted as-is)
  3. No banned keyword appears in name or description (case-insensitive)
  4. Does not duplicate the last gift given

Usage:
  python eval/eval.py                  # runs all test cases
  python eval/eval.py --case TC01      # runs one case
  python eval/eval.py --no-search      # skips URL verification (faster, offline)
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Add parent dir so we can import gift_engine and product_search
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from gift_engine import run_turn, parse_suggestions, build_initial_message
from product_search import _check_url


def extract_price(price_str: str) -> float | None:
    """Parse a price string like '$45' or '$30-50' → float (uses lower bound)."""
    if not price_str:
        return None
    numbers = re.findall(r"[\d,]+\.?\d*", price_str.replace(",", ""))
    if numbers:
        return float(numbers[0])
    return None


def evaluate_suggestion(suggestion: dict, constraints: dict, check_url: bool) -> dict:
    name = suggestion.get("name", "").lower()
    description = suggestion.get("description", "").lower()
    # Exclude the 'why' field from constraint checking: it is reasoning prose
    # that naturally uses constraint words in non-violating contexts
    # (e.g. "enhance the birdwatching experience").
    combined = f"{name} {description}"

    result = {
        "name": suggestion.get("name"),
        "url": suggestion.get("url"),
        "price_estimate": suggestion.get("price_estimate"),
        "passes_budget": True,
        "passes_constraints": True,
        "passes_url": None,
        "violated_keywords": [],
    }

    # Budget check
    price = extract_price(suggestion.get("price_estimate", ""))
    budget = constraints.get("budget")
    if price is not None and budget is not None:
        result["passes_budget"] = price <= budget

    # Keyword constraint check
    banned = constraints.get("banned_keywords", [])
    last_gift = constraints.get("must_not_duplicate_last_gift", "").lower()
    check_terms = [kw.lower() for kw in banned]
    if last_gift:
        check_terms.append(last_gift)

    for term in check_terms:
        # Use word-boundary matching so "ring" doesn't hit "providing/exploring"
        # Multi-word terms fall back to plain substring (e.g. "code girls")
        if not term:
            continue
        if " " in term:
            hit = term in combined
        else:
            hit = bool(re.search(r"\b" + re.escape(term) + r"\b", combined))
        if hit:
            result["violated_keywords"].append(term)
    result["passes_constraints"] = len(result["violated_keywords"]) == 0

    # URL check
    url = suggestion.get("url", "")
    if check_url and url:
        result["passes_url"] = _check_url(url)
    elif not url:
        result["passes_url"] = False
    else:
        result["passes_url"] = None  # skipped

    result["passes_all"] = (
        result["passes_budget"]
        and result["passes_constraints"]
        and (result["passes_url"] is not False)
    )

    return result


def run_case(tc: dict, check_url: bool) -> dict:
    profile = tc["profile"]
    constraints = tc["constraints"]

    messages = [build_initial_message(profile)]

    print(f"\n{'='*60}")
    print(f"Running {tc['id']}: {tc['description']}")
    print(f"{'='*60}")

    try:
        text, _ = run_turn(messages, profile, [])
    except Exception as e:
        print(f"  ERROR during LLM call: {e}")
        return {"id": tc["id"], "error": str(e), "suggestions": [], "pass_rate": 0}

    suggestions = parse_suggestions(text)
    print(f"  Got {len(suggestions)} suggestion(s)")

    results = []
    for s in suggestions:
        r = evaluate_suggestion(s, constraints, check_url)
        results.append(r)
        status = "PASS" if r["passes_all"] else "FAIL"
        url_status = (
            "✓" if r["passes_url"] is True
            else "✗" if r["passes_url"] is False
            else "?"
        )
        print(
            f"  [{status}] {r['name']}"
            f" | budget={'✓' if r['passes_budget'] else '✗'}"
            f" | constraints={'✓' if r['passes_constraints'] else '✗ '+str(r['violated_keywords'])}"
            f" | url={url_status}"
        )

    passing = sum(1 for r in results if r["passes_all"])
    total = len(results)
    rate = passing / total if total > 0 else 0
    print(f"  pass_rate: {passing}/{total} = {rate:.2%}")

    return {
        "id": tc["id"],
        "description": tc["description"],
        "suggestions": results,
        "passing": passing,
        "total": total,
        "pass_rate": rate,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="Run a single test case by ID (e.g. TC01)")
    parser.add_argument("--no-search", action="store_true", help="Skip URL verification")
    parser.add_argument("--output", help="Save JSON results to this file")
    args = parser.parse_args()

    cases_path = Path(__file__).parent / "test_cases.json"
    with open(cases_path) as f:
        all_cases = json.load(f)

    if args.case:
        all_cases = [tc for tc in all_cases if tc["id"] == args.case]
        if not all_cases:
            print(f"No test case with ID {args.case}")
            sys.exit(1)

    check_url = not args.no_search

    all_results = []
    for tc in all_cases:
        r = run_case(tc, check_url)
        all_results.append(r)

    # Aggregate
    total_passing = sum(r.get("passing", 0) for r in all_results)
    total_suggestions = sum(r.get("total", 0) for r in all_results)
    overall_rate = total_passing / total_suggestions if total_suggestions > 0 else 0

    print(f"\n{'='*60}")
    print(f"OVERALL pass_rate: {total_passing}/{total_suggestions} = {overall_rate:.2%}")
    print(f"{'='*60}")

    if args.output:
        output = {
            "overall_pass_rate": overall_rate,
            "total_passing": total_passing,
            "total_suggestions": total_suggestions,
            "cases": all_results,
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
