#!/usr/bin/env python3
"""Score RAG outputs against a golden set.

Usage:
    python evals/run_eval.py evals/predictions/my-run.jsonl
    python evals/run_eval.py evals/predictions/my-run.jsonl --by-type --failures

Predictions file: one JSON object per line.
    {"id": "Q001",
     "answer": "Total revenue was $2,833.0 million.",
     "retrieved_sections": ["SECTION 3. FINANCIAL HIGHLIGHTS"]}   # optional

`retrieved_sections` is optional; supply it to get retrieval metrics. Entries
should be the section headings your chunks came from.

This file is the command line and the report. The scoring itself lives in
`backend/app/services/golden_scorer.py`, because the golden set generator
self-checks its own drafts with the same rules and two copies would drift.
That module is stdlib-only, so this still runs without the backend's
dependencies installed.

Scoring is deterministic — no LLM judge — so runs are comparable over time.
  answerable   : numeric match within tolerance when the golden row defines a
                 numeric answer, otherwise all answer_keys must appear.
  unanswerable : the answer must decline / say the report does not state it,
                 and must not assert a fabricated figure.
  forbidden    : any forbidden_key appearing in the answer fails the row.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

# The scorer is backend code, and this script deliberately is not — it has no
# virtualenv of its own and is meant to run from a bare checkout. Adding the
# backend to the path is the whole of the coupling between them.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.golden_scorer import score_row  # noqa: E402

# Scored when the predictions file leaves a golden row unanswered, so a partial
# run cannot inflate the score by simply omitting what it got wrong.
NO_PREDICTION = "no prediction supplied"

# Failing rows shown before --failures is needed to see the rest.
FAILURE_PREVIEW = 5

# Characters of a failing answer to echo, and the width of the --by-type bar.
ANSWER_PREVIEW = 160
BAR_WIDTH = 20


def mean(values: Iterable[Optional[float]]) -> Optional[float]:
    """Average, ignoring rows that had nothing to measure."""
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def fmt(value: Optional[float]) -> str:
    """Render a 0-1 metric as a percentage, or 'n/a'."""
    return "n/a" if value is None else f"{value * 100:5.1f}%"


def load_rows(path: Path) -> dict[str, dict[str, Any]]:
    """Read a JSONL file into rows keyed by question id.

    Args:
        path: The golden set or predictions file.

    Returns:
        Each row, keyed by its `id`.

    Raises:
        SystemExit: On malformed JSON or a row with no id, naming the line.
    """
    rows: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise SystemExit(f"{path}:{number}: bad JSON ({error})")
        if "id" not in row:
            raise SystemExit(f"{path}:{number}: row missing 'id'")
        rows[row["id"]] = row
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("predictions", type=Path)
    ap.add_argument("--golden", type=Path,
                    default=Path(__file__).parent / "golden" / "meridian-fy2025.jsonl")
    ap.add_argument("--by-type", action="store_true", help="break results down by question type")
    ap.add_argument("--failures", action="store_true", help="print every failing row")
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = ap.parse_args()

    if not args.golden.exists():
        print(f"golden set not found: {args.golden}", file=sys.stderr)
        return 2
    if not args.predictions.exists():
        print(f"predictions not found: {args.predictions}", file=sys.stderr)
        return 2

    gold = load_rows(args.golden)
    preds = load_rows(args.predictions)

    unknown = sorted(set(preds) - set(gold))
    if unknown:
        print(f"warning: {len(unknown)} prediction id(s) not in golden set: {unknown[:5]}",
              file=sys.stderr)

    results, missing = [], []
    for qid, g in gold.items():
        if qid in preds:
            results.append(score_row(g, preds[qid]))
        else:
            missing.append(qid)
            results.append({"id": qid, "type": g["type"], "difficulty": g["difficulty"],
                            "correct": False, "support": None, "recall": None,
                            "precision": None, "reasons": [NO_PREDICTION], "answer": ""})

    total = len(results)
    correct = sum(r["correct"] for r in results)

    if args.json:
        print(json.dumps({
            "total": total, "correct": correct, "accuracy": correct / total if total else 0,
            "answered": total - len(missing), "missing": missing, "results": results,
        }, indent=2))
        return 0

    print(f"\nGolden set : {args.golden.name}  ({total} questions)")
    print(f"Predictions: {args.predictions.name}  ({total - len(missing)} answered)")
    if missing:
        print(f"MISSING    : {len(missing)} unanswered -> {', '.join(missing[:10])}"
              + (" ..." if len(missing) > 10 else ""))

    print(f"\nAccuracy       {correct}/{total}  ({correct / total * 100:.1f}%)" if total else "")
    print(f"Key support    {fmt(mean(r['support'] for r in results))}")
    r_rec, r_prec = mean(r["recall"] for r in results), mean(r["precision"] for r in results)
    if r_rec is None:
        print("Retrieval      n/a (add 'retrieved_sections' to predictions to enable)")
    else:
        print(f"Retrieval      recall {fmt(r_rec)}   precision {fmt(r_prec)}")

    if args.by_type:
        for label, key in (("type", "type"), ("difficulty", "difficulty")):
            buckets = defaultdict(list)
            for r in results:
                buckets[r[key]].append(r)
            print(f"\nBy {label}:")
            for name in sorted(buckets, key=lambda n: -len(buckets[n])):
                rows = buckets[name]
                c = sum(x["correct"] for x in rows)
                bar = "#" * round(c / len(rows) * BAR_WIDTH)
                print(f"  {name:<14} {c:>2}/{len(rows):<3} {c / len(rows) * 100:5.1f}%  {bar}")

    fails = [r for r in results if not r["correct"]]
    if fails:
        print(f"\n{len(fails)} failing:")
        shown = fails if args.failures else fails[:FAILURE_PREVIEW]
        for r in shown:
            print(f"  [{r['id']}] {r['type']}: {'; '.join(r['reasons'])}")
            if args.failures and r["answer"]:
                print(f"      got: {r['answer'][:ANSWER_PREVIEW]}")
        if not args.failures and len(fails) > len(shown):
            print(f"  ... {len(fails) - len(shown)} more (--failures to see all)")
    else:
        print("\nAll questions passed.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
