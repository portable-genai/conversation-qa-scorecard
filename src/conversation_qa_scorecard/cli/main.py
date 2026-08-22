"""Minimal stdlib CLI: score a contact, list contacts, or read a stored scorecard.

argparse only, no extra dependencies, and every command goes through the same domain service
the API uses, so rule R8 routing and the tenant boundary are the same code on this surface.
"""

from __future__ import annotations

import argparse
import json
import sys

from hex_service_kit.logging import configure_logging
from hex_service_kit.serialization import to_jsonable

from ..config import build_container
from ..contacts import contact_catalogue, find_contact, headline_for
from ..domain.kernel import utcnow
from ..service import build_service, pack_for_contact

_DEFAULT_ACTOR = "cli-user@bank.example"
_DEFAULT_TENANT = "demo-bank"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="conversation_qa_scorecard")
    sub = parser.add_subparsers(dest="command", required=True)

    score_cmd = sub.add_parser("score", help="Score one contact and print its scorecard.")
    score_cmd.add_argument("contact_id")
    score_cmd.add_argument("--actor", default=_DEFAULT_ACTOR)
    score_cmd.add_argument(
        "--tenant",
        default=_DEFAULT_TENANT,
        help="The tenant this CLI run acts for. A real surface takes it from a verified "
        "principal; on the CLI it is asserted, which is why the CLI is a local tool.",
    )
    score_cmd.add_argument("--json", action="store_true", help="Print the whole scorecard.")

    sub.add_parser("contacts", help="List the contacts the bound catalogue can score.")

    read_cmd = sub.add_parser("read", help="Read one stored scorecard by id.")
    read_cmd.add_argument("scorecard_id")
    read_cmd.add_argument("--tenant", default=_DEFAULT_TENANT)

    args = parser.parse_args(argv)
    container = build_container()
    # Idempotent: a process that is both an API app and a CLI configures once.
    configure_logging(container.settings.profile, service="conversation-qa-scorecard")

    if args.command == "contacts":
        for contact in contact_catalogue(container):
            headline = headline_for(container, contact.contact_id)
            print(f"{contact.contact_id}  {contact.market.value}  {contact.tenant}")
            if headline:
                print(f"    {headline}")
        return 0

    if args.command == "score":
        contact = find_contact(container, args.contact_id)
        scorecard = build_service(container).score_contact(
            contact,
            pack_for_contact(container, contact),
            actor=args.actor,
            tenant=args.tenant,
            as_of=utcnow(),
        )
        if args.json:
            print(json.dumps(to_jsonable(scorecard), indent=2, sort_keys=True))
            return 0
        _print(scorecard)
        return 0

    if args.command == "read":
        scorecard = build_service(container).fetch(args.scorecard_id, tenant=args.tenant)
        _print(scorecard)
        return 0

    return 2  # pragma: no cover - argparse requires a subcommand


def _print(scorecard: object) -> None:
    """Print the engine's own figures. Nothing here recomputes anything."""
    disposition = getattr(scorecard, "disposition", None)
    print(f"{getattr(scorecard, 'contact_id', '')}: {getattr(disposition, 'value', '')}")
    print(f"  scorecard: {getattr(scorecard, 'scorecard_id', '')}")
    print(f"  disclosure: {getattr(scorecard, 'disclosure_score', 0.0):.2f}")
    print(f"  adherence : {getattr(scorecard, 'adherence_score', 0.0):.2f}")
    for finding in getattr(scorecard, "findings", ()):
        mark = "PASS" if finding.satisfied else "FAIL"
        print(f"  [{mark}] {finding.requirement_id:<28} {finding.status.value}")
        for span in finding.evidence:
            print(f"          turn {span.turn_index} [{span.char_start}:{span.char_end}]")
    print(f"  requires_human_review: {getattr(scorecard, 'requires_human_review', False)}")
    review_ref = getattr(scorecard, "review_ref", "")
    if review_ref:
        # Rule R8 on the CLI path too: the routing happened in the domain service, and this
        # prints WHERE it went. A surface that only printed the flag would be a second place
        # for an escalation to stop.
        print(f"  routed to human review: {review_ref}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
