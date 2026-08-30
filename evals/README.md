# RAG Evals

Deterministic evaluation harness for the RAG pipeline.

## Corpus

`data/01-meridian-fy2025-annual-report.txt` — a synthetic annual report for
"Meridian Freightworks, Inc.", a fictional freight company. Roughly 1,700 words
across 10 sections: CEO letter, segment results, financial tables, headcount,
emissions, governance, risk factors, and outlook.

It is built for retrieval testing, not realism:

- **Facts are spread across sections**, so single-chunk retrieval is not enough.
- **Tables carry prior-year comparatives**, so FY2024 and FY2025 figures sit
  side by side and must be told apart.
- **Deliberate distractors.** Marcus Reyes (SVP Cold Chain) and Marisol Reyes
  (Chief People Officer) are a near-duplicate name pair. Digital Platform's ARR
  ($271.4M) sits near its segment revenue ($256.3M). Driver turnover of 41
  percent collides numerically with the 41 electric tractors.
- **Every derived figure is internally consistent** — segment revenue sums to
  the total, the P&L walks from operating income to EPS, headcount sums, and
  emissions reconcile to the baseline. A model that does the arithmetic right
  will agree with the document.

## Golden set

`golden/meridian-fy2025.jsonl` — 40 questions, one JSON object per line.

| Type | N | What it tests |
|---|---|---|
| `lookup` | 15 | Single-fact retrieval |
| `arithmetic` | 6 | Values that must be computed, not quoted |
| `multi_hop` | 5 | Facts that must be joined across two sections |
| `unanswerable` | 5 | Refusal instead of hallucination |
| `temporal` | 4 | FY2024 vs FY2025 disambiguation |
| `distractor` | 3 | Resisting a near-duplicate wrong answer |
| `synthesis` | 2 | Multi-sentence summarization |

Fields per row:

- `question`, `answer` — the prompt and reference answer.
- `numeric_answer` / `numeric_tolerance` — authoritative when present.
- `answer_keys` — strings that must appear when there is no numeric answer.
  Always verbatim from the corpus, so they double as a grounding check.
- `forbidden_keys` — strings that fail the row if present (distractor traps).
- `must_refuse` — the answer must acknowledge the report does not state this.
- `gold_sections` — section headings the answer should have been retrieved from.
- `note` — the trap the question is setting, where there is one.

Q039 is deliberately half-answerable: the acquisition price is stated, the
close date is not. A correct answer gives the first and declines the second.

## Running

```sh
python evals/run_eval.py evals/predictions/my-run.jsonl --by-type --failures
```

Your pipeline writes one JSON object per line:

```json
{"id": "Q001", "answer": "Total revenue was $2,833.0 million.",
 "retrieved_sections": ["SECTION 3. FINANCIAL HIGHLIGHTS"]}
```

`retrieved_sections` is optional — include it to get retrieval recall and
precision, which separates "retrieved the wrong chunk" from "retrieved the
right chunk and answered badly". That split is the main reason to track it.

Flags: `--by-type` for the breakdown, `--failures` for every failing row with
its answer, `--json` for machine-readable output, `--golden` to point at a
different golden file.

Questions with no prediction count as failures, so a partial run cannot inflate
the score.

## Reference runs

- `predictions/example-perfect.jsonl` — golden answers replayed. Scores 100%.
  Run it after changing the harness to confirm the scorer still passes a
  known-good run.
- `predictions/example-weak.jsonl` — six seeded errors: a wrong-year figure,
  a Reyes name swap, bad arithmetic, and three hallucinated answers to
  unanswerable questions. Scores 85% and should fail exactly Q016, Q020, Q031,
  Q036, Q037, Q040.

## Scoring notes

Scoring is string and number matching, with no LLM judge, so runs are
comparable over time and cost nothing. The tradeoff is that `synthesis`
questions are graded only on keyword presence — treat those two scores as
weak signals, and read the answers.

Refusal detection is a pattern list (`REFUSAL_PATTERNS` in `run_eval.py`). If
your prompt makes the model decline in some other phrasing, add the pattern
there rather than loosening the golden set.
