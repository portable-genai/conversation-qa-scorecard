"""Canonical synthetic contacts, shared by the unit and contract suites.

Every party is obviously fictional and every address is an ``.example`` domain or an RFC 5737 /
RFC 3849 literal. The CONTACTS themselves come from the shipped fixture catalogue (one JSON
file per contact under ``src/conversation_qa_scorecard/transcripts/``), so the tests, the demo,
the eval and the UI all score the same conversations and a change to one is visible in all of
them. This module names the ones the suites refer to by hand.

Parity means the SAME request through every implementation, so the canonical values have one
home rather than being retyped per test.
"""

from __future__ import annotations

#: The verified principal the tests attribute work to (never a client-asserted actor).
ACTOR = "analyst@bank.example"

#: The tenant partition the seeded ``analyst`` / ``approver`` / ``auditor`` personas carry.
TENANT = "demo-bank"

#: The tenant the seeded ``other-tenant`` persona carries, for the cross-tenant denial.
OTHER_TENANT = "other-bank"

#: Fully compliant: every declared obligation met, in order, inside the timing window.
COMPLIANT_CONTACT = "CT-SG-0001"

#: The capital-at-risk warning is never given: a CRITICAL breach that must escalate (rule R8).
BREACH_CONTACT = "CT-SG-0002"

#: Consent taken before the risk and fee disclosures: every step present, order wrong.
OUT_OF_ORDER_CONTACT = "CT-SG-0003"

#: Carries a planted national id, an email, a vulnerability cue and escalation language.
PII_CONTACT = "CT-SG-0004"

#: No word timings at all, so every requirement with a declared window is UNVERIFIABLE.
UNTIMED_CONTACT = "CT-SG-0005"

#: The recording notice arrives four minutes in, outside its configured deadline.
LATE_CONTACT = "CT-SG-0006"

#: Declares an obligation the active pack does not configure: a GAP, never a pass.
GAP_CONTACT = "CT-SG-0007"

#: Belongs to ``OTHER_TENANT``: readable only by that tenant's principals.
OTHER_TENANT_CONTACT = "CT-SG-9001"

#: Australian general-insurance sale with the significant exclusions never explained.
AU_CONTACT = "CT-AU-0001"

#: Japanese solicitation, fully compliant. Proves matching over a character run with no spaces.
JP_CONTACT = "CT-JP-0001"

#: A planted identifier, so a redaction assertion has an independent literal to look for
#: rather than trusting the pattern pack to agree with itself.
PLANTED_NRIC = "S1234567D"

#: The market pack the Singapore contacts are scored against.
SG_PACK_ID = "sg-retail-banking-v1"
