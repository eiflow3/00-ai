"""Detection and classification contract for the PII governance stage.

Written BEFORE the implementation (see tests/GOVERNANCE_TEST_PLAN.md): every
test here is expected to fail until `app.services.governance` exists.  What
these tests pin down is the module's public contract, so the red suite is the
specification:

  * `detector.detect(text)` returns `Finding`s whose offsets index into the
    original text — a finding that cannot be located cannot be redacted.
  * Shape alone is not enough: an SSN inside a longer token, a version number
    shaped like an IP, and a part number shaped like a phone must NOT fire,
    because a governance stage that cries wolf gets turned off.
  * `classifier.classify` decides personal vs business from domain lists,
    role addresses and surrounding words — never from the string alone.

The fixture files (pii_sample.txt / .md) are the golden corpus: one shared
cast of synthetic identities, so the same expected-findings table validates
every format.  All values are reserved test ranges — nothing real can leak.
"""

from pathlib import Path

import pytest

from app.schemas.governance import EntityType, GovernancePolicy, PiiClass
from app.services.governance.pii import classifier, detector

FIXTURES = Path(__file__).parent / "fixtures"

# One entity alone in a short string: the smallest claim the detector makes.
SINGLE_ENTITIES = [
    ("reach me at mc.reyes.demo@gmail.com today", EntityType.EMAIL, "mc.reyes.demo@gmail.com"),
    ("call (202) 555-0180 during office hours", EntityType.PHONE, "(202) 555-0180"),
    ("her mobile is +63 917 555 0123 after 6pm", EntityType.PHONE, "+63 917 555 0123"),
    ("the ID on file is 000-12-3456 as issued", EntityType.SSN, "000-12-3456"),
    ("card 4111 1111 1111 1111 stays on record", EntityType.CREDIT_CARD, "4111 1111 1111 1111"),
    ("submitted from workstation 192.0.2.44 today", EntityType.IP_ADDRESS, "192.0.2.44"),
]

# Strings that look like PII but are not — each one is a real false-positive
# mode seen in business documents, and each must stay silent.
TRAPS = [
    "Purchase order ORD-000-12-3456 shipped from the Pasig warehouse.",
    "The build pipeline reported version 10.4.0.1 at 08:15:32.",
    "Part number 555-0100-A replaces the discontinued 555-0099-A bracket.",
    "Revenue grew 14 percent year over year; headcount reached 455 in 2026.",
]


def policy(**overrides) -> GovernancePolicy:
    """A policy at the defaults unless a test says otherwise."""
    return GovernancePolicy(**overrides)


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize("text,entity,value", SINGLE_ENTITIES,
                         ids=lambda v: v.value if isinstance(v, EntityType) else None)
def test_detects_each_entity_type_alone(text, entity, value):
    findings = detector.detect(text)

    assert [f.entity_type for f in findings] == [entity]
    assert findings[0].text == value


def test_offsets_index_into_the_original_text():
    """A finding that cannot be located cannot be masked."""
    text = ("Maria Clara Reyes lives at 12 Sampaguita Street, Quezon City 1100 "
            "and reads mc.reyes.demo@gmail.com on +63 917 555 0123.")

    for finding in detector.detect(text):
        assert text[finding.start:finding.end] == finding.text


@pytest.mark.parametrize("trap", TRAPS)
def test_traps_do_not_fire(trap):
    """A governance stage that cries wolf gets turned off."""
    assert detector.detect(trap) == []


def test_clean_business_prose_yields_nothing():
    text = ("Margins in the manufacturing segment recovered to pre-2024 levels "
            "after the supply chain reorganisation completed in Q3.")

    assert detector.detect(text) == []


def test_card_numbers_are_luhn_validated():
    """Shape-matching sixteen digits flags every order id in a sales doc."""
    valid = detector.detect("card 4111 1111 1111 1111 on record")
    invalid = detector.detect("card 4111 1111 1111 1112 on record")

    assert [f.entity_type for f in valid] == [EntityType.CREDIT_CARD]
    assert invalid == []


def test_email_inside_a_mailto_url_is_one_finding():
    findings = detector.detect("see mailto:mc.reyes.demo@gmail.com for details")

    assert len(findings) == 1
    assert findings[0].entity_type == EntityType.EMAIL


def test_phone_split_across_an_extracted_line_break():
    """PDF-derived text breaks lines mid-entity; the entity is still one."""
    findings = detector.detect("reach her on +1 (202)\n555-0143 during office hours")

    assert [f.entity_type for f in findings] == [EntityType.PHONE]


def test_phone_split_across_a_page_marker_is_found_once():
    """Derived markdown carries `<!-- page N -->` between pages; an entity
    straddling the marker is the page-span case from the PDF fixture."""
    text = "call the desk line +1 (202)\n<!-- page 3 -->\n555-0143 between 9 and 5"

    findings = detector.detect(text)

    assert len([f for f in findings if f.entity_type == EntityType.PHONE]) == 1


# ----------------------------------------------------------- classification


def classified(text: str, **policy_overrides):
    """Detect then classify, returning findings keyed by their raw text."""
    findings = classifier.classify(text, detector.detect(text), policy(**policy_overrides))
    return {f.text: f for f in findings}


def test_free_mail_domain_is_personal():
    findings = classified("her email is mc.reyes.demo@gmail.com today")

    assert findings["mc.reyes.demo@gmail.com"].classification == PiiClass.PERSONAL


def test_role_address_is_business():
    findings = classified("contact hr@acmecorp.example for questions")

    assert findings["hr@acmecorp.example"].classification == PiiClass.BUSINESS


def test_named_corporate_email_is_ambiguous():
    """The genuinely undecidable class: policy, not the classifier, owns it."""
    findings = classified("approved by juan.delacruz@acmecorp.example on Friday")

    assert findings["juan.delacruz@acmecorp.example"].classification == PiiClass.AMBIGUOUS


def test_own_domain_allowlist_downgrades_the_finding():
    findings = classified(
        "approved by juan.delacruz@acmecorp.example on Friday",
        own_domains=["acmecorp.example"],
    )

    assert findings["juan.delacruz@acmecorp.example"].classification == PiiClass.BUSINESS


def test_context_words_shift_the_class():
    personal = classified("my personal mobile is (202) 555-0143, call anytime")
    business = classified("our main office line is (202) 555-0180, call anytime")

    assert personal["(202) 555-0143"].classification == PiiClass.PERSONAL
    assert business["(202) 555-0180"].classification == PiiClass.BUSINESS


def test_documentation_ip_is_infra_not_personal():
    findings = classified("submitted from workstation 192.0.2.44 on the VPN")

    assert findings["192.0.2.44"].classification == PiiClass.INFRA


def test_every_finding_carries_a_confidence():
    text = "email mc.reyes.demo@gmail.com or call (202) 555-0180"

    for finding in classifier.classify(text, detector.detect(text), policy()):
        assert 0.0 < finding.confidence <= 1.0


# -------------------------------------------------------- fixture goldens

# The shared cast: every fixture format must surface at least these.
EXPECTED_CAST = {
    (EntityType.EMAIL, "mc.reyes.demo@gmail.com"),
    (EntityType.EMAIL, "hr@acmecorp.example"),
    (EntityType.EMAIL, "juan.delacruz@acmecorp.example"),
    (EntityType.PHONE, "+63 917 555 0123"),
    (EntityType.SSN, "000-12-3456"),
    (EntityType.CREDIT_CARD, "4111 1111 1111 1111"),
    (EntityType.IP_ADDRESS, "192.0.2.44"),
}

# And none of the traps, in any format.
FORBIDDEN = {"ORD-000-12-3456", "10.4.0.1", "555-0100-A"}


@pytest.mark.parametrize("fixture", ["pii_sample.txt", "pii_sample.md"])
def test_fixture_yields_the_expected_cast_and_no_traps(fixture):
    text = (FIXTURES / fixture).read_text()

    found = {(f.entity_type, f.text) for f in detector.detect(text)}
    found_values = {value for _, value in found}

    assert EXPECTED_CAST <= found, f"missing: {EXPECTED_CAST - found}"
    assert not (FORBIDDEN & found_values), f"traps fired: {FORBIDDEN & found_values}"


def test_markdown_structures_are_scanned():
    """PII hides in the table, the mailto link and the fenced log block —
    a detector that only reads prose paragraphs misses all three."""
    text = (FIXTURES / "pii_sample.md").read_text()
    values = {f.text for f in detector.detect(text)}

    assert "juan.delacruz@acmecorp.example" in values  # table cell
    assert "mc.reyes.demo@gmail.com" in values         # mailto link + code fence
    assert "192.0.2.44" in values                      # fenced log block
