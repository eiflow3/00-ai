# edge_cases.pdf — ground truth

A 6-page synthetic PDF packing every extraction edge case the PDF-ingestion
work must handle. Regenerate with
`uv run --with reportlab --with pillow python make_fixture_pdf.py`.

Every page carries the same running header ("ACME Corp — FY2026 Operations
Review"), a footer line, and "Page N of 6" — extraction should not stitch this
chrome into chunks.

| Page | Edge case | What correct extraction looks like |
| --- | --- | --- |
| 1 | Hyphenated hard line breaks (`infra-`/`structure`, `manu-`/`facturing`); ATX-style numbered headings | Words rejoined: "infrastructure", "manufacturing"; "1. Executive Summary" and "1.1 Outlook" become headings |
| 2 | Wide numeric table (8 cols) + simple captioned table | Both recovered as structured tables, not interleaved word soup; captions "Table 1/2: …" kept |
| 3 | Two-column layout | Left column read fully before right — "APAC…" paragraph never interleaves with "EMEA…" |
| 4 | Image-only page (text exists only as pixels; only header/footer are real text) | Body recovered by OCR: "The Board approves the Manila data centre expansion… USD 48 million… January 2027" |
| 5 | Drawn bar chart with caption; sentence that continues onto page 6 | Bars unreadable (expected); caption "Figure 3: …" kept; trailing sentence not truncated at the page break |
| 6 | Continuation of the p5 sentence, then "6. Risks" | The p5→p6 sentence reads whole; a chunk spanning it carries page_start=5, page_end=6 |

Verified with pypdf: page 4 yields only the 91 chars of header/footer chrome —
its body genuinely requires OCR.

Retrieval probes (each answerable only if its edge case was handled):
- "What was total FY26 revenue?" → 1534.9 (table 1)
- "What budget did the board approve for Manila?" → USD 48 million (OCR page)
- "What does the capacity policy oblige when utilisation exceeds 95 percent?" → spans pages 5–6
- "How many engineering staff in 2026?" → 455 (table 2)
