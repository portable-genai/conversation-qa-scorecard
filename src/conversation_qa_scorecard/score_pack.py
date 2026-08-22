"""Loader for the conversation-QA score packs (configuration into domain values).

Lives OUTSIDE ``domain/`` because it reads YAML from disk, and the domain core stays pure
stdlib with no I/O. It turns the reference pack shipped at
``conversation_qa_scorecard/scorepacks/reference_packs.yaml`` (or an adopter's own file,
selected by ``score_packs.pack_path`` in ``config/settings.yaml``) into immutable
:class:`ScorePack` values the engine takes as a parameter.

Why a pack file rather than constants in the engine: which wording a market mandates, which
paraphrases a QA team accepts, in what order the steps must come, how soon, what an unmet one
costs, and which words evidence vulnerability are all POLICY, not algorithm (practice B4).
Tuning them, or adding a market, must be a configuration change a compliance officer can read
and diff, on a per-market review schedule, without a release of this service.

Loading is FAIL-CLOSED, and every refusal below has a scorecard behind it:

* an unreadable, non-mapping or empty file raises: a scorecard produced from a half-parsed
  pack reports an unconfigured obligation as a pass;
* an unknown market, kind, severity, role, locale or cue family raises rather than defaulting;
* a requirement citing an undefined citation id, or citing none at all, raises: a finding with
  no instrument behind it is not a compliance finding;
* a requirement naming a step the pack does not define raises, because a step with no wording
  can never be matched and would score as a silent permanent absence;
* a requirement with no ROLE raises. A requirement that accepts any speaker is satisfied by
  the customer reading the risk warning back, which is not a disclosure;
* a requirement with no steps, a step with no wordings, or a cue family with no phrases
  raises: each of them is a requirement that can never be met, or a cue that can never fire,
  hiding inside a pack that looks configured;
* a duplicate requirement id inside one pack raises, because two results under one id make the
  scorecard ambiguous, and an ambiguous compliance record is not a record.

The locale is validated against the shared speech kernel at load time, so a typo in a locale
tag is a boot failure rather than a pack that quietly matches nothing.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, NoReturn

import yaml
from speech_lexicon_kit import ChannelRole, UnsupportedLocaleError, canonical_locale

from .config import Settings
from .domain.errors import ScorePackError
from .domain.kernel import Citation, Severity
from .domain.models import (
    CueSet,
    Market,
    PhraseVariant,
    Requirement,
    RequirementKind,
    ScorePack,
    ScriptStep,
    SignalKind,
)

DEFAULT_PACK_PATH = Path(__file__).resolve().parent / "scorepacks" / "reference_packs.yaml"

#: The roles a requirement may bind to. ``UNKNOWN`` is deliberately absent: binding a
#: disclosure to the speaker nobody could identify is not a disclosure obligation.
_ALLOWED_ROLES = (ChannelRole.AGENT, ChannelRole.CUSTOMER, ChannelRole.SYSTEM)


def _fail(message: str) -> NoReturn:
    """Every refusal in this module goes through here, so none of them can be a warning."""
    raise ScorePackError(f"score pack: {message}")


def _enum(cls: Any, value: Any, what: str) -> Any:
    try:
        return cls(str(value))
    except ValueError:
        permitted = ", ".join(sorted(str(member.value) for member in cls))
        _fail(f"unknown {what} {value!r}; permitted: {permitted}")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _citation(raw: dict[str, Any], citation_id: str) -> Citation:
    return Citation(
        source_id=citation_id,
        title=_text(raw.get("title", "")),
        snippet=_text(raw.get("snippet", "")),
    )


def _citations(doc: dict[str, Any]) -> dict[str, Citation]:
    raw = doc.get("citations") or {}
    if not isinstance(raw, dict) or not raw:
        _fail("no 'citations' block; every requirement must name the instrument it comes from")
    out: dict[str, Citation] = {}
    for citation_id, spec in raw.items():
        if not isinstance(spec, dict) or not _text(spec.get("title", "")):
            _fail(f"citation {citation_id!r} has no title (a citation must name its instrument)")
        out[str(citation_id)] = _citation(spec, str(citation_id))
    return out


def _phrase_steps(pack_id: str, block: Any) -> dict[str, ScriptStep]:
    """The named steps of one pack: the mandated wording plus its approved paraphrases."""
    if not isinstance(block, dict) or not block:
        _fail(f"{pack_id}: 'phrases' must be a non-empty mapping of step id -> wordings")
    steps: dict[str, ScriptStep] = {}
    for entry_id, spec in block.items():
        name = str(entry_id)
        if not isinstance(spec, dict):
            _fail(f"{pack_id}/{name}: a step must be a mapping")
        required = _text(spec.get("required_phrase", ""))
        if not required:
            _fail(f"{pack_id}/{name}: 'required_phrase' is the mandated wording and is missing")
        variants = [PhraseVariant(phrase_id=f"{name}-required", text=required, required=True)]
        raw_paraphrases = spec.get("paraphrases") or []
        if not isinstance(raw_paraphrases, list):
            _fail(f"{pack_id}/{name}: 'paraphrases' must be a list of accepted wordings")
        for index, paraphrase in enumerate(raw_paraphrases):
            text = _text(paraphrase)
            if not text:
                _fail(f"{pack_id}/{name}: an empty paraphrase would match every turn")
            variants.append(PhraseVariant(phrase_id=f"{name}-alt{index}", text=text))
        steps[name] = ScriptStep(
            entry_id=name, variants=tuple(variants), label=_text(spec.get("label", ""))
        )
    return steps


def _requirements(
    pack_id: str,
    block: Any,
    steps: dict[str, ScriptStep],
    citations: dict[str, Citation],
) -> tuple[Requirement, ...]:
    if not isinstance(block, list) or not block:
        _fail(f"{pack_id}: 'requirements' must be a non-empty list")
    out: list[Requirement] = []
    seen: set[str] = set()
    for spec in block:
        if not isinstance(spec, dict):
            _fail(f"{pack_id}: each requirement must be a mapping")
        requirement_id = str(spec.get("id", "")).strip()
        if not requirement_id:
            _fail(f"{pack_id}: a requirement has no id")
        if requirement_id in seen:
            _fail(f"{pack_id}: duplicate requirement id {requirement_id!r}")
        seen.add(requirement_id)
        step_ids = spec.get("steps") or []
        if not isinstance(step_ids, list) or not step_ids:
            _fail(f"{requirement_id}: 'steps' must be a non-empty list of step ids")
        resolved: list[ScriptStep] = []
        for step_id in step_ids:
            step = steps.get(str(step_id))
            if step is None:
                _fail(
                    f"{requirement_id}: step {step_id!r} is not defined in this pack's "
                    "'phrases' block, so it could never be matched"
                )
            else:
                resolved.append(step)
        citation_id = str(spec.get("citation", "") or "")
        if citation_id not in citations:
            _fail(
                f"{requirement_id}: citation {citation_id!r} is not defined in the pack; every "
                "requirement must state the named instrument it comes from"
            )
        role_name = str(spec.get("role", "") or "")
        if not role_name:
            _fail(
                f"{requirement_id}: 'role' is required. A requirement with no role is satisfied "
                "by the customer reading the wording back, which is not a disclosure."
            )
        role = _enum(ChannelRole, role_name, "channel role")
        if role not in _ALLOWED_ROLES:
            permitted = ", ".join(r.value for r in _ALLOWED_ROLES)
            _fail(f"{requirement_id}: role {role_name!r} may not be bound; use one of {permitted}")
        out.append(
            Requirement(
                requirement_id=requirement_id,
                kind=_enum(RequirementKind, spec.get("kind", ""), "requirement kind"),
                steps=tuple(resolved),
                severity=_enum(Severity, spec.get("severity", "high"), "severity"),
                citation=citations[citation_id],
                role=role,
                deadline_ms=_optional_ms(requirement_id, spec.get("deadline_ms"), "deadline_ms"),
                within_ms=_optional_ms(requirement_id, spec.get("within_ms"), "within_ms"),
                description=_text(spec.get("description", "")),
                remediation=_text(spec.get("remediation", "")),
            )
        )
    return tuple(out)


def _optional_ms(requirement_id: str, value: Any, field: str) -> int | None:
    if value is None:
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        _fail(f"{requirement_id}: {field} must be a whole number of milliseconds")
    if milliseconds <= 0:
        _fail(f"{requirement_id}: {field} must be positive; a window of {milliseconds} never opens")
    return milliseconds


def _cues(pack_id: str, block: Any, citations: dict[str, Citation]) -> tuple[CueSet, ...]:
    if not isinstance(block, dict) or not block:
        _fail(f"{pack_id}: 'cues' must be a non-empty mapping of cue id -> cue family")
    out: list[CueSet] = []
    for cue_id, spec in block.items():
        name = str(cue_id)
        if not isinstance(spec, dict):
            _fail(f"{pack_id}/{name}: a cue family must be a mapping")
        phrases = spec.get("phrases") or []
        if not isinstance(phrases, list) or not phrases:
            _fail(
                f"{pack_id}/{name}: 'phrases' must be a non-empty list; a cue with none never fires"
            )
        cleaned = tuple(_text(phrase) for phrase in phrases)
        if any(not phrase for phrase in cleaned):
            _fail(f"{pack_id}/{name}: an empty cue phrase would match every turn")
        citation_id = str(spec.get("citation", "") or "")
        if citation_id and citation_id not in citations:
            _fail(f"{pack_id}/{name}: unknown citation id {citation_id!r}")
        polarity = int(spec.get("polarity", 0) or 0)
        if polarity not in (-1, 0, 1):
            _fail(f"{pack_id}/{name}: 'polarity' must be -1, 0 or 1; got {polarity}")
        out.append(
            CueSet(
                cue_id=name,
                kind=_enum(SignalKind, spec.get("kind", ""), "signal kind"),
                label=_text(spec.get("label", "")) or name,
                severity=_enum(Severity, spec.get("severity", "medium"), "severity"),
                phrases=cleaned,
                polarity=polarity,
                citation=citations.get(citation_id) if citation_id else None,
            )
        )
    return tuple(sorted(out, key=lambda cue: cue.cue_id))


def _locale(pack_id: str, raw: Any) -> str:
    try:
        return canonical_locale(str(raw or ""))
    except UnsupportedLocaleError as exc:
        _fail(f"{pack_id}: {exc}")


def load_packs(path: str | Path | None = None) -> dict[str, ScorePack]:
    """Load and validate every pack in ``path`` (default: the shipped reference pack)."""
    pack_path = Path(path) if path else DEFAULT_PACK_PATH
    if not pack_path.exists():
        _fail(f"pack file {pack_path} does not exist; the scorecard engine cannot run without it")
    try:
        doc = yaml.safe_load(pack_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScorePackError(f"score pack: {pack_path} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        _fail(f"{pack_path} must contain a mapping")

    version = str(doc.get("version", "")).strip()
    if not version:
        _fail("the pack file declares no 'version'; a scorecard must record what scored it")
    citations = _citations(doc)
    raw_packs = doc.get("packs") or {}
    if not isinstance(raw_packs, dict) or not raw_packs:
        _fail("no 'packs' block; there is nothing to score against")

    packs: dict[str, ScorePack] = {}
    for pack_id, spec in raw_packs.items():
        name = str(pack_id)
        if not isinstance(spec, dict):
            _fail(f"pack {name!r} must be a mapping")
        steps = _phrase_steps(name, spec.get("phrases"))
        packs[name] = ScorePack(
            pack_id=name,
            version=version,
            market=_enum(Market, spec.get("market", ""), "market"),
            locale=_locale(name, spec.get("locale")),
            product=str(spec.get("product", "") or ""),
            requirements=_requirements(name, spec.get("requirements"), steps, citations),
            cues=_cues(name, spec.get("cues"), citations),
            description=_text(spec.get("description", "")),
        )
    return packs


@lru_cache(maxsize=4)
def _cached_packs(resolved: str) -> dict[str, ScorePack]:
    return load_packs(resolved)


def packs_for(settings: Settings) -> dict[str, ScorePack]:
    """Every pack the active deployment configures (adopter override, else the reference)."""
    configured = (settings.score_pack_path or "").strip()
    return _cached_packs(configured or str(DEFAULT_PACK_PATH))


def pack_for_market(settings: Settings, market: Market, product: str = "") -> ScorePack:
    """The pack that scores ``market`` (optionally narrowed to one product line).

    Fail-closed: a market with no configured pack RAISES rather than falling back to another
    market's pack. Scoring a Japanese solicitation against Singapore's requirements would
    produce a confident, cited and completely wrong scorecard.
    """
    candidates = [
        pack
        for pack in packs_for(settings).values()
        if pack.market is market and (not product or pack.product == product)
    ]
    if not candidates:
        detail = f"{market.value}" + (f"/{product}" if product else "")
        _fail(
            f"no pack is configured for {detail}. Scoring it against another market's pack "
            "would produce a confident and wrong scorecard, so the assessment refuses instead."
        )
    chosen = sorted(candidates, key=lambda pack: pack.pack_id)[0]
    sibling = (settings.sibling_disclosure_pack_path or "").strip()
    return with_sibling_disclosures(chosen, sibling) if sibling else chosen


# --------------------------------------------------------------------------------------- #
# The sibling disclosure pack (E1), read rather than re-authored
# --------------------------------------------------------------------------------------- #
#: The document kind the contact-centre copilot (E1, `contact-centre-conversations`) publishes for a
#: market's disclosure obligations. Its file says the shape is shared with this repo on purpose,
#: and honouring that is the point: the copilot reminds an agent that a window is open and this
#: scorecard grades whether the reminder worked, so a market's requirement must be reviewed ONCE.
SIBLING_DISCLOSURE_KIND = "disclosure"

#: What a sibling disclosure becomes here. E1's shape carries the wording, the paraphrases, the
#: window, the severity and the speaker, which is everything a single-step DISCLOSURE requirement
#: needs. It carries no ORDERING (a live copilot has nothing to order yet) and no CITATION, which
#: is why a sibling pack is a SUPPLEMENT and never a replacement: the ordered script segments and
#: the named regulator instruments stay in this repo's own pack.
_SIBLING_KIND = RequirementKind.DISCLOSURE


def _sibling_citation(document: dict[str, Any], pack_id: str) -> Citation:
    """Provenance for an obligation that came from the shared pack rather than an instrument.

    Deliberately weaker than this repo's own citations, and deliberately honest about it. E1's
    shape names no regulator publication, so the citation records what is actually known: which
    reviewed artifact, maintained under which regulator, this obligation came from. A finding
    that said "MAS Guidelines FSG-G04" here would be inventing a source.
    """
    jurisdiction = str(document.get("jurisdiction") or "").strip()
    market = str(document.get("market") or "").strip()
    label = jurisdiction or market or "the operator"
    return Citation(
        source_id=f"pack:{pack_id}",
        title=f"Shared disclosure pack {pack_id}, maintained under {label}",
        snippet=(
            "Obligation taken from the disclosure pack the contact-centre copilot reminds from, "
            "so the wording is reviewed once and cannot drift between the two systems. It names "
            "no regulator publication; add one in this repo's own pack to cite an instrument."
        ),
    )


def load_sibling_disclosures(path: str | Path) -> tuple[Requirement, ...]:
    """Read a sibling ``kind: disclosure`` pack into this engine's requirements.

    Fail-closed in the same places the native loader is: a wrong document kind, a disclosure
    with no id, no mandated wording, no role or a non-positive window all refuse, because a
    supplement that half-loaded would add obligations nobody can meet.
    """
    pack_path = Path(path)
    if not pack_path.exists():
        _fail(f"sibling disclosure pack {pack_path} does not exist")
    try:
        document = yaml.safe_load(pack_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScorePackError(f"score pack: {pack_path} is not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        _fail(f"{pack_path} must contain a mapping")
    kind = str(document.get("kind") or "")
    if kind != SIBLING_DISCLOSURE_KIND:
        _fail(
            f"{pack_path} declares kind {kind!r}; this reader accepts only "
            f"{SIBLING_DISCLOSURE_KIND!r} packs, and reading another kind by guessing at its "
            "fields is how two systems stop agreeing about a market"
        )
    pack_id = str(document.get("pack_id") or "").strip()
    if not pack_id:
        _fail(f"{pack_path} declares no pack_id, so a scorecard could not record what scored it")
    entries = document.get("disclosures") or []
    if not isinstance(entries, list) or not entries:
        _fail(f"{pack_id}: no 'disclosures'; an empty supplement adds nothing and hides that")

    citation = _sibling_citation(document, pack_id)
    out: list[Requirement] = []
    seen: set[str] = set()
    for spec in entries:
        if not isinstance(spec, dict):
            _fail(f"{pack_id}: each disclosure must be a mapping")
        entry_id = str(spec.get("disclosure_id", "")).strip()
        if not entry_id:
            _fail(f"{pack_id}: a disclosure has no disclosure_id")
        if entry_id in seen:
            _fail(f"{pack_id}: duplicate disclosure_id {entry_id!r}")
        seen.add(entry_id)
        required = _text(spec.get("required_phrase", ""))
        if not required:
            _fail(f"{pack_id}/{entry_id}: 'required_phrase' is the mandated wording and is missing")
        role_name = str(spec.get("role", "") or "")
        if not role_name:
            _fail(
                f"{pack_id}/{entry_id}: 'role' is required. A disclosure with no role is "
                "satisfied by the customer reading the wording back."
            )
        role = _enum(ChannelRole, role_name, "channel role")
        if role not in _ALLOWED_ROLES:
            _fail(f"{pack_id}/{entry_id}: role {role_name!r} may not be bound")
        variants = [PhraseVariant(phrase_id=f"{entry_id}-required", text=required, required=True)]
        raw_paraphrases = spec.get("paraphrases") or []
        if not isinstance(raw_paraphrases, list):
            _fail(f"{pack_id}/{entry_id}: 'paraphrases' must be a list of accepted wordings")
        for index, paraphrase in enumerate(raw_paraphrases):
            text = _text(paraphrase)
            if not text:
                _fail(f"{pack_id}/{entry_id}: an empty paraphrase would match every turn")
            variants.append(PhraseVariant(phrase_id=f"{entry_id}-alt{index}", text=text))
        requirement_id = f"{pack_id}:{entry_id}"
        out.append(
            Requirement(
                requirement_id=requirement_id,
                kind=_SIBLING_KIND,
                steps=(
                    ScriptStep(
                        entry_id=entry_id,
                        variants=tuple(variants),
                        label=_text(spec.get("reminder", "")),
                    ),
                ),
                severity=_enum(Severity, spec.get("severity", "high"), "severity"),
                citation=citation,
                role=role,
                # E1's `within_ms` is measured from a trigger EVENT during a live contact. This
                # scorecard sees a finished conversation and no event stream, so it is read as a
                # deadline from the start of the call, which is the strictest honest reading:
                # a disclosure due 45 seconds after an event that happened later cannot have been
                # due earlier than 45 seconds in.
                deadline_ms=_optional_ms(requirement_id, spec.get("within_ms"), "within_ms"),
                description=_text(spec.get("reminder", "")),
                remediation=(
                    "Re-give this disclosure to the customer in writing and record the date. "
                    "The wording comes from the shared disclosure pack the copilot reminds from."
                ),
            )
        )
    return tuple(out)


def with_sibling_disclosures(pack: ScorePack, path: str | Path) -> ScorePack:
    """Return ``pack`` extended with a sibling pack's disclosures, refusing any id collision.

    A collision means the same obligation is configured twice, in two artifacts, which is the
    exact drift the shared shape exists to prevent. It refuses rather than picking a winner.
    """
    additions = load_sibling_disclosures(path)
    existing = {requirement.requirement_id for requirement in pack.requirements}
    clashes = sorted(r.requirement_id for r in additions if r.requirement_id in existing)
    if clashes:
        _fail(
            f"{pack.pack_id}: the sibling pack redefines {', '.join(clashes)}. One obligation "
            "configured in two artifacts is the drift the shared shape exists to prevent."
        )
    merged = tuple(sorted(pack.requirements + additions, key=lambda r: r.requirement_id))
    return replace(pack, requirements=merged)
