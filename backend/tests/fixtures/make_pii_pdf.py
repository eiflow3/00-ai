"""Generate a 3-page PDF fixture of SYNTHETIC PII for governance tests.

p1  memo prose with personal PII: name, free-mail email, mobile, home
    address, date of birth, SSN-shaped ID, test card number
p2  contact table mixing personal / corporate / role emails, plus the
    published business address and a phone number that BREAKS across the
    p2->p3 page boundary (page-span detection test)
p3  the continuation of that phone number, an IP address, and the
    "NOT PII" trap lines that must not fire

Every value is fictional and drawn from reserved test ranges: 555-01xx
phones, a 000-prefix SSN shape, 192.0.2.0/24 documentation IPs, and the
Visa test card number. Pages carry the same running header/footer chrome
as edge_cases.pdf so header/footer stripping is exercised too.
"""

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUT = "pii_sample.pdf"
W, H = LETTER
PAGES = 3

HEADER = "ACME Corp — HR Relocation File (synthetic test data)"
FOOTER = "ACME Corp, 500 Harbor Boulevard, Suite 900, Pasig City — hr@acmecorp.example"


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
    y = lines(c, 0.75 * inch, H - 1.1 * inch, ["1. Relocation Request"],
              font=("Helvetica-Bold", 16), leading=24)
    lines(c, 0.75 * inch, y - 6, [
        "Maria Clara Reyes has requested relocation to the Manila office",
        "effective January 2027. Her personal email is",
        "mc.reyes.demo@gmail.com and her mobile number is +63 917 555 0123.",
        "She currently lives at 12 Sampaguita Street, Barangay San Isidro,",
        "Quezon City 1100.",
        "",
        "Her date of birth is 14 March 1991 and the government ID on file",
        "is 000-12-3456. Payroll should keep the card 4111 1111 1111 1111",
        "on record until the transfer completes, then remove it.",
        "",
        "Her manager, Juan dela Cruz (juan.delacruz@acmecorp.example),",
        "approved the request on 21 August 2026.",
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

    lines(c, 0.75 * inch, H - 1.1 * inch, ["2. Contact Sheet"],
          font=("Helvetica-Bold", 16))
    c.setFont("Helvetica-Bold", 10)
    c.drawString(0.75 * inch, H - 1.6 * inch, "Table 1: Points of contact")
    y = grid(0.75 * inch, H - 1.75 * inch, [130, 220, 120],
             [["Person", "Email", "Phone"],
              ["Maria Clara Reyes", "mc.reyes.demo@gmail.com", "+63 917 555 0123"],
              ["Juan dela Cruz", "juan.delacruz@acmecorp.example", "(202) 555-0181"],
              ["HR service desk", "hr@acmecorp.example", "(202) 555-0180"]])
    lines(c, 0.75 * inch, y - 30, [
        "The headquarters address printed in the footer is published on the",
        "company website and appears on every invoice.",
    ])
    # Phone number that must continue onto page 3 — the page-span test.
    lines(c, 0.75 * inch, y - 80, [
        "During the transition Maria can also be reached on the temporary",
        "desk line +1 (202)",
    ])


def page3(c):
    lines(c, 0.75 * inch, H - 1.1 * inch, [
        "555-0143 between 9am and 5pm Manila time.",
    ])
    y = lines(c, 0.75 * inch, H - 1.6 * inch, ["3. Access Log"],
              font=("Helvetica-Bold", 16), leading=24)
    y = lines(c, 0.75 * inch, y - 4, [
        "The relocation form was submitted from workstation 192.0.2.44",
        "over the corporate VPN.",
    ])
    y = lines(c, 0.75 * inch, y - 20, ["4. Not PII — detector traps"],
              font=("Helvetica-Bold", 13), leading=20)
    lines(c, 0.75 * inch, y - 4, [
        "Purchase order ORD-000-12-3456 shipped from the Pasig warehouse.",
        "The build pipeline reported version 10.4.0.1 at 08:15:32.",
        "Part number 555-0100-A replaces the discontinued 555-0099-A.",
        "Revenue grew 14 percent year over year; headcount reached 455.",
    ])


c = canvas.Canvas(OUT, pagesize=LETTER)
for n, draw in enumerate([page1, page2, page3], start=1):
    draw(c)
    chrome(c, n)
    c.showPage()
c.save()
print(f"wrote {OUT}")
