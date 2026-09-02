"""Generate a 6-page PDF fixture that packs every extraction edge case:

p1  headings + hyphenated line-break prose (single column)
p2  two tables: a wide numeric one and a captioned simple one
p3  two-column layout (reading-order trap)
p4  image-only page: text exists ONLY as pixels (OCR required)
p5  a drawn bar-chart figure with a caption
p6  a sentence that continues across the p5->p6 page break (page-span test)

Every page carries a running header, footer rule, and "Page N of 6" number,
so header/footer stripping is exercised on all pages.
"""

import io

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUT = "edge_cases.pdf"
W, H = LETTER
PAGES = 6

HEADER = "ACME Corp — FY2026 Operations Review"
FOOTER = "Confidential — internal distribution only"


def chrome(c: canvas.Canvas, page: int) -> None:
    """Running header/footer + page number, identical on every page."""
    c.setFont("Helvetica", 8)
    c.drawString(0.75 * inch, H - 0.5 * inch, HEADER)
    c.line(0.75 * inch, H - 0.58 * inch, W - 0.75 * inch, H - 0.58 * inch)
    c.drawString(0.75 * inch, 0.5 * inch, FOOTER)
    c.drawRightString(W - 0.75 * inch, 0.5 * inch, f"Page {page} of {PAGES}")


def lines(c, x, y, rows, font=("Helvetica", 11), leading=15):
    c.setFont(*font)
    for row in rows:
        c.drawString(x, y, row)
        y -= leading
    return y


def page1(c):
    y = lines(c, 0.75 * inch, H - 1.1 * inch, ["1. Executive Summary"],
              font=("Helvetica-Bold", 16), leading=24)
    # Hyphenated hard line breaks: "infra-structure" and "manu-facturing"
    y = lines(c, 0.75 * inch, y - 6, [
        "Revenue grew 14 percent year over year, driven primarily by infra-",
        "structure services in the APAC region. Margins in the manu-",
        "facturing segment recovered to pre-2024 levels after the supply",
        "chain reorganisation completed in the third quarter.",
    ])
    y = lines(c, 0.75 * inch, y - 14, ["1.1 Outlook"],
              font=("Helvetica-Bold", 13), leading=20)
    lines(c, 0.75 * inch, y - 4, [
        "We expect single-digit growth in 2027, with capital expenditure",
        "concentrated in the Singapore and Manila data centre expansions.",
    ])


def page2(c):
    def grid(x, y, col_w, rows, font=("Helvetica", 9)):
        row_h = 16
        c.setFont(*font)
        for r, row in enumerate(rows):
            cy = y - r * row_h
            cx = x
            for w, cell in zip(col_w, row):
                c.rect(cx, cy - row_h, w, row_h)
                c.drawString(cx + 3, cy - row_h + 4, cell)
                cx += w
        return y - len(rows) * row_h

    lines(c, 0.75 * inch, H - 1.1 * inch, ["2. Financial Detail"],
          font=("Helvetica-Bold", 16))
    # Wide numeric table — the "embeds as word soup" case.
    c.setFont("Helvetica-Bold", 10)
    c.drawString(0.75 * inch, H - 1.6 * inch, "Table 1: Revenue by region (USD millions)")
    wide = [
        ["Region", "Q1", "Q2", "Q3", "Q4", "FY26", "FY25", "Δ%"],
        ["APAC", "112.4", "118.9", "131.2", "140.7", "503.2", "421.0", "+19.5"],
        ["EMEA", "98.1", "95.4", "99.8", "104.2", "397.5", "388.9", "+2.2"],
        ["Americas", "154.9", "149.2", "158.8", "171.3", "634.2", "601.4", "+5.5"],
        ["Total", "365.4", "363.5", "389.8", "416.2", "1534.9", "1411.3", "+8.8"],
    ]
    y = grid(0.75 * inch, H - 1.75 * inch, [70, 55, 55, 55, 55, 60, 60, 50], wide)
    # Simple captioned table.
    c.setFont("Helvetica-Bold", 10)
    c.drawString(0.75 * inch, y - 30, "Table 2: Headcount by function")
    grid(0.75 * inch, y - 45, [140, 80, 80],
         [["Function", "2025", "2026"],
          ["Engineering", "412", "455"],
          ["Sales", "198", "214"],
          ["Operations", "167", "160"]])


def page3(c):
    lines(c, 0.75 * inch, H - 1.1 * inch, ["3. Regional Commentary"],
          font=("Helvetica-Bold", 16))
    left = [
        "APAC: The Singapore hub",
        "reached full capacity in",
        "October. A second facility",
        "in Manila is under contract",
        "and fits out through 2027.",
        "Customer churn fell to 3.1",
        "percent, the lowest since",
        "the segment was formed.",
    ]
    right = [
        "EMEA: Energy costs eased",
        "but currency headwinds",
        "removed roughly two points",
        "of growth. The Frankfurt",
        "consolidation closed two",
        "legacy sites and moved 41",
        "staff to the new campus",
        "without attrition.",
    ]
    lines(c, 0.75 * inch, H - 1.6 * inch, left, font=("Helvetica", 10), leading=14)
    lines(c, 4.35 * inch, H - 1.6 * inch, right, font=("Helvetica", 10), leading=14)


def page4(c):
    """Text that exists only as pixels — a simulated scan. No text layer."""
    img = Image.new("RGB", (1700, 2200), "#f7f4ee")  # slightly off-white, scan-like
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 64)
        body = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 44)
    except OSError:
        big = body = ImageFont.load_default()
    d.text((150, 260), "4. Signed Board Resolution (scanned)", font=big, fill="#222")
    text = (
        "The Board approves the Manila data centre expansion\n"
        "with a budget of USD 48 million, to be drawn down in\n"
        "three tranches beginning January 2027. Approved and\n"
        "signed at the meeting of 12 August 2026."
    )
    d.multiline_text((150, 450), text, font=body, fill="#333", spacing=22)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    from reportlab.lib.utils import ImageReader
    c.drawImage(ImageReader(buf), 0.6 * inch, 1.0 * inch,
                width=W - 1.2 * inch, height=H - 2.0 * inch)


def page5(c):
    lines(c, 0.75 * inch, H - 1.1 * inch, ["5. Capacity Utilisation"],
          font=("Helvetica-Bold", 16))
    # A drawn bar chart: content the extractor cannot read, caption it can.
    base_y, x = 4.5 * inch, 1.2 * inch
    for label, frac in [("SIN", 0.98), ("MNL", 0.35), ("FRA", 0.74), ("DAL", 0.81)]:
        bar_h = 2.2 * inch * frac
        c.rect(x, base_y, 0.6 * inch, bar_h, fill=1)
        c.setFont("Helvetica", 9)
        c.drawCentredString(x + 0.3 * inch, base_y - 14, label)
        x += 1.1 * inch
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(1.2 * inch, base_y - 40,
                 "Figure 3: Data centre utilisation at year end (per cent of rated capacity)")
    # Sentence that must continue onto page 6 — the page-span test.
    lines(c, 0.75 * inch, base_y - 90, [
        "Utilisation in Singapore ran above the 95 percent planning threshold",
        "for seven consecutive months, which under the capacity policy obliges",
    ], font=("Helvetica", 11), leading=15)


def page6(c):
    lines(c, 0.75 * inch, H - 1.1 * inch, [
        "the operator to commission additional capacity within four quarters;",
        "the Manila facility satisfies that obligation when it enters service.",
    ], font=("Helvetica", 11), leading=15)
    y = lines(c, 0.75 * inch, H - 1.7 * inch, ["6. Risks"],
              font=("Helvetica-Bold", 16), leading=24)
    lines(c, 0.75 * inch, y - 4, [
        "Foreign exchange remains the largest single risk to reported growth.",
        "A ten percent strengthening of the dollar removes approximately",
        "USD 31 million of annualised revenue at current contract mix.",
    ])


c = canvas.Canvas(OUT, pagesize=LETTER)
for n, draw in enumerate([page1, page2, page3, page4, page5, page6], start=1):
    draw(c)
    chrome(c, n)
    c.showPage()
c.save()
print(f"wrote {OUT}")
