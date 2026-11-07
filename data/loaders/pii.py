"""PII handling for NHTSA complaint text (free-form, public record).

The loader already drops identifying columns at parse time (VIN,
operator name, dealer phone/city, consumer city). What remains is
whatever an owner typed into the free-text complaint field (CDESCR).
Before the sample CSVs are committed, that text goes through two
gates in the seed script:

- Phone numbers and email addresses: the row is DROPPED. The text
  is usually a signature block, not the defect description, so the
  row is worth less than the risk of keeping it.
- VINs and street addresses: REDACTED in place, the row is kept.
  A 17-character VIN, a partial VIN named in the text ("VIN #...",
  "last 8 of VIN ..."), and a full-form street address (house number
  + street + city/state/ZIP) are replaced with a marker.

Limits, stated plainly because they decide what the sample can
promise: a partial VIN with no digit in it is not redacted (that
is how "VIN NUMBER" is kept); a street address without a state and
ZIP is not caught, because in vehicle text a bare "123 Main St" is
indistinguishable from phrases like "4 wheel drive" (the loose
pattern matched 52,000 lines, nearly all vehicle phrases). Dealer
addresses are redacted the same as home addresses; the pattern does
not distinguish them, and the dealer name/city columns are already
dropped. Nothing here is a guarantee that no personal data remains
in the text — it is a documented, tested reduction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phone number (xxx-xxx-xxxx, separators optional, area code may be
# parenthesized: "(555) 123-4567" defeated the first version of
# this pattern) or email address.
CONTACT_RE = re.compile(r"\(?\b\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b|[\w.+-]+@[\w-]+\.[\w.]+")

# A 17-character VIN. The VIN alphabet excludes I, O, Q, so a
# 17-character token in that alphabet is a VIN in practice; 482 such
# tokens appear in the complaint text, sampled and confirmed real.
VIN_FULL_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")

# Candidate partial-VIN tokens: 5-17 VIN-alphabet characters. A
# token is only redacted when it carries a digit (which is what
# keeps the phrase "VIN NUMBER" from being redacted) and a VIN
# mention or an "ending in" phrase sits within 30 characters
# before it — so "VIN NUMBER IS JM1FD33" is caught, and a bare
# part number far from any VIN mention is left alone.
VIN_TOKEN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{5,17}\b")
VIN_WORD_RE = re.compile(r"\bvin\b", re.IGNORECASE)
VIN_ENDING_PHRASE_RE = re.compile(
    r"\b(?:ending in|last (?:six|6|eight|8|seven|7|five|5)"
    r" (?:of )?(?:the )?(?:vin|digits?|characters?))\b",
    re.IGNORECASE,
)
VIN_CONTEXT_WINDOW = 30

# Full-form street address: house number + street name + suffix +
# city + 2-letter state + ZIP. The state+ZIP anchor is what makes
# this safe: without it the pattern matches "4 WHEEL DRIVE" and
# "50 FEET WHEN OFF ROAD" tens of thousands of times. Addresses
# without a ZIP are not caught (see the module docstring).
ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+[A-Za-z]+(?:\s+[A-Za-z]+){0,2}\s+"
    r"(?:street|st\.?|avenue|ave\.?|road|rd\.?|boulevard|blvd\.?|lane|ln\.?|"
    r"drive|dr\.?|court|ct\.?|way|place|pl\.?)"
    r"[,.]?\s+[A-Za-z]+(?:\s+[A-Za-z]+)?,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScrubResult:
    """Text after redaction, with per-kind redaction counts."""

    text: str
    vin_redactions: int = 0
    address_redactions: int = 0

    @property
    def redacted(self) -> bool:
        return self.vin_redactions + self.address_redactions > 0


def contains_contact_info(text: str) -> bool:
    """True if the text holds a phone number or email address."""
    return CONTACT_RE.search(text) is not None


def _redact_partial_vins(text: str) -> tuple[str, int]:
    """Redact partial VINs named in the text; returns text and count.

    Token-first, context-checked: every 5-17 character VIN-alphabet
    token with a digit is redacted when a VIN mention or an "ending
    in" phrase sits shortly before it. Full 17-character VINs are
    handled by VIN_FULL_RE before this runs, so this only sees the
    shorter tokens.
    """
    count = 0
    out: list[str] = []
    pos = 0
    for match in VIN_TOKEN_RE.finditer(text):
        token = match.group(0)
        before = text[max(0, match.start() - VIN_CONTEXT_WINDOW) : match.start()]
        named = VIN_WORD_RE.search(before) is not None or (
            VIN_ENDING_PHRASE_RE.search(before) is not None
        )
        if any(char.isdigit() for char in token) and named:
            out.append(text[pos : match.start()])
            out.append("[VIN]")
            pos = match.end()
            count += 1
    out.append(text[pos:])
    return "".join(out), count


def scrub_text(text: str) -> ScrubResult:
    """Redact VINs and street addresses in complaint text, in place."""
    vin_total = 0
    text, count = VIN_FULL_RE.subn("[VIN]", text)
    vin_total += count
    text, count = _redact_partial_vins(text)
    vin_total += count
    text, address_total = ADDRESS_RE.subn("[ADDRESS]", text)
    return ScrubResult(text=text, vin_redactions=vin_total, address_redactions=address_total)
