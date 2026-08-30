#!/usr/bin/env python3
"""Score RAG outputs against the Meridian FY2025 golden set.

Usage:
    python evals/run_eval.py evals/predictions/my-run.jsonl
    python evals/run_eval.py evals/predictions/my-run.jsonl --by-type --failures

Predictions file: one JSON object per line.
    {"id": "Q001",
     "answer": "Total revenue was $2,833.0 million.",
     "retrieved_sections": ["SECTION 3. FINANCIAL HIGHLIGHTS"]}   # optional

`retrieved_sections` is optional; supply it to get retrieval metrics. Entries
should be the section headings your chunks came from.

Scoring is deterministic — no LLM judge — so runs are comparable over time.
  answerable   : numeric match within tolerance when the golden row defines a
                 numeric answer, otherwise all answer_keys must appear.
  unanswerable : the answer must decline / say the report does not state it,
                 and must not assert a fabricated figure.
  forbidden    : any forbidden_key appearing in the answer fails the row.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REFUSAL_PATTERNS = [
    r"\bnot stated\b", r"\bnot (?:be )?(?:specified|disclosed|provided|reported|included|given)\b",
    r"\bdoes not (?:state|say|specify|disclose|provide|report|contain|include|break)\b",
    r"\bdoesn't (?:state|say|specify|disclose|provide|report|contain|include|break)\b",
    r"\bno (?:information|breakdown|figure|data|mention|disclosure)\b",
    r"\bcannot (?:be )?(?:determined|answered|found)\b", r"\bcan't be (?:determined|answered|found)\b",
    r"\bnot available\b", r"\bis not in the (?:report|document|context)\b",
    r"\bunable to (?:answer|determine|find)\b", r"\bhad not closed\b", r"\bnot yet closed\b",
    r"\bonly provides guidance\b", r"\bguidance,? not actual\b",
]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def numbers_in(text: str):
    """Pull numeric tokens, tolerating thousands separators, $, %, and x suffixes."""
    out = []
    for tok in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text or ""):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def looks_like_refusal(answer: str) -> bool:
    low = norm(answer)
    return any(re.search(p, low) for p in REFUSAL_PATTERNS)


def score_row(gold: dict, pred: dict) -> dict:
    answer = pred.get("answer", "") or ""
    low = norm(answer)
    reasons = []

    for fk in gold.get("forbidden_keys", []):
        if norm(fk) in low:
            reasons.append(f"contains forbidden key '{fk}'")

    if gold.get("must_refuse"):
        refused = looks_like_refusal(answer)
        if not refused:
            reasons.append("did not acknowledge the report does not state this")
        # A partially answerable row still needs its stated half present.
        for k in gold.get("answer_keys", []):
            if norm(k) not in low:
                reasons.append(f"missing stated fact '{k}'")
        correct = not reasons
    elif gold.get("numeric_answer") is not None:
        target = float(gold["numeric_answer"])
        tol = float(gold.get("numeric_tolerance", 0.05))
        found = numbers_in(answer)
        hit = any(abs(n - target) <= tol for n in found)
        if not hit:
            reasons.append(f"expected {target} (+/-{tol}), answer had {found[:8] or 'no numbers'}")
        correct = hit and not reasons
    else:
        missing = [k for k in gold.get("answer_keys", []) if norm(k) not in low]
        if missing:
            reasons.append(f"missing keys: {missing}")
        correct = not missing and not reasons

    # Support signal: did the answer cite the underlying figures at all?
    keys = gold.get("answer_keys", [])
    support = (sum(norm(k) in low for k in keys) / len(keys)) if keys else None

    # Retrieval metrics, only when the prediction reports what it retrieved.
    gold_secs = gold.get("gold_sections", [])
    retrieved = pred.get("retrieved_sections")
    recall = precision = None
    if retrieved is not None and gold_secs:
        rset = {norm(s) for s in retrieved}
        gset = {norm(s) for s in gold_secs}
        hits = len(gset & rset)
        recall = hits / len(gset)
        precision = hits / len(rset) if rset else 0.0

    return {
        "id": gold["id"], "type": gold["type"], "difficulty": gold["difficulty"],
        "correct": bool(correct), "support": support,
        "recall": recall, "precision": precision,
        "reasons": reasons, "answer": answer,
    }


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def fmt(v):
    return "n/a" if v is None else f"{v * 100:5.1f}%"


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

    gold = {}
    for line in args.golden.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            gold[r["id"]] = r

    preds = {}
    for n, line in enumerate(args.predictions.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"{args.predictions}:{n}: bad JSON ({e})", file=sys.stderr)
            return 2
        if "id" not in r:
            print(f"{args.predictions}:{n}: row missing 'id'", file=sys.stderr)
            return 2
        preds[r["id"]] = r

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
                            "precision": None, "reasons": ["no prediction supplied"],
                            "answer": ""})

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
                bar = "#" * round(c / len(rows) * 20)
                print(f"  {name:<14} {c:>2}/{len(rows):<3} {c / len(rows) * 100:5.1f}%  {bar}")

    fails = [r for r in results if not r["correct"]]
    if fails:
        print(f"\n{len(fails)} failing:")
        shown = fails if args.failures else fails[:5]
        for r in shown:
            print(f"  [{r['id']}] {r['type']}: {'; '.join(r['reasons'])}")
            if args.failures and r["answer"]:
                print(f"      got: {r['answer'][:160]}")
        if not args.failures and len(fails) > len(shown):
            print(f"  ... {len(fails) - len(shown)} more (--failures to see all)")
    else:
        print("\nAll questions passed.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
