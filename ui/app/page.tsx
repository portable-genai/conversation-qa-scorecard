"use client";

import { useCallback, useEffect, useState } from "react";

// Every request goes to THIS origin. The browser never learns the service's address and never
// holds its credential; the route handler under /api/agent forwards, having discarded whatever
// identity the client tried to assert.
const API = "/api/agent";

// Mirrors the service's seeded local personas. The picker is a DEV convenience: the server
// validates the selection against its own list, so a hand-crafted value cannot invent a persona,
// and the tenant it carries is what the DOMAIN authorises against.
const PERSONAS = ["analyst", "approver", "auditor", "other-tenant"];

// Only "present" passes. Every other status says why it failed, and each is rendered in its own
// tone so a reviewer can tell an absence from an unchecked deadline at a glance.
const FAIL_TONE: Record<string, string> = {
  absent: "bad",
  out_of_order: "bad",
  late: "warn",
  pending: "warn",
  unverifiable: "warn",
  gap: "bad",
};

interface CardSummary {
  name?: string;
  description?: string;
}

interface Contact {
  contact_id: string;
  market: string;
  product: string;
  declared_requirement_ids: string[];
  headline?: string;
}

interface Citation {
  source_id: string;
  title: string;
  snippet?: string;
}

interface Evidence {
  turn_index: number;
  char_start: number;
  char_end: number;
  speaker_id: string;
  role: string;
  text: string;
  start_ms?: number | null;
}

interface Finding {
  requirement_id: string;
  kind: string;
  status: string;
  severity: string;
  detail?: string;
  remediation?: string;
  citation: Citation;
  evidence: Evidence[];
}

interface Signal {
  cue_id: string;
  kind: string;
  label: string;
  severity: string;
  detected: boolean;
  evidence: Evidence[];
}

interface Narration {
  headline: string;
  body: string;
  model?: string;
  grounded?: boolean;
}

interface Scorecard {
  scorecard_id: string;
  contact_id: string;
  market: string;
  pack_id: string;
  pack_version: string;
  disposition: string;
  severity: string;
  requires_human_review: boolean;
  disclosure_score: number;
  adherence_score: number;
  sentiment_score: number;
  redaction_count: number;
  engine_version?: string;
  review_ref?: string;
  findings: Finding[];
  signals: Signal[];
  citations: Citation[];
  narration?: Narration | null;
}

interface Turn {
  index: number;
  speaker_id: string;
  role: string;
  text: string;
  start_ms?: number | null;
}

interface TranscriptView {
  transcript_id: string;
  redaction_count: number;
  turns: Turn[];
}

function clock(ms?: number | null): string {
  if (ms === null || ms === undefined) return "no timing";
  const total = Math.round(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return minutes + ":" + String(seconds).padStart(2, "0");
}

// Split one turn's text at the spans that were cited on it, so the reviewer sees the words the
// engine actually matched rather than a verdict beside a paragraph. Offsets come from the same
// redacted text this page renders, which is why they line up.
function segments(turn: Turn, spans: Evidence[]) {
  const ordered = spans
    .filter((span) => span.turn_index === turn.index)
    .sort((a, b) => a.char_start - b.char_start);
  const parts: { text: string; hit: boolean }[] = [];
  let cursor = 0;
  for (const span of ordered) {
    if (span.char_start < cursor) continue;
    if (span.char_start > cursor) {
      parts.push({ text: turn.text.slice(cursor, span.char_start), hit: false });
    }
    parts.push({ text: turn.text.slice(span.char_start, span.char_end), hit: true });
    cursor = span.char_end;
  }
  parts.push({ text: turn.text.slice(cursor), hit: false });
  return parts.filter((part) => part.text.length > 0);
}

export default function Home() {
  const [persona, setPersona] = useState(PERSONAS[0]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactId, setContactId] = useState("");
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [transcript, setTranscript] = useState<TranscriptView | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [card, setCard] = useState<CardSummary | null>(null);

  // The service names itself, so this UI carries no hardcoded product name to go stale.
  useEffect(() => {
    let live = true;
    fetch(API + "/.well-known/agent-card.json", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (live) setCard(body as CardSummary | null);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  // The contact list is filtered SERVER-SIDE on the verified tenant, so switching persona
  // switches which contacts exist at all. That is the tenant boundary, visible.
  const loadContacts = useCallback(async () => {
    setScorecard(null);
    setTranscript(null);
    setError("");
    try {
      const response = await fetch(API + "/v1/contacts", {
        headers: { "X-Dev-Persona": persona },
        cache: "no-store",
      });
      const body = (await response.json()) as Contact[];
      const listed = Array.isArray(body) ? body : [];
      setContacts(listed);
      setContactId(listed.length > 0 ? listed[0].contact_id : "");
    } catch (caught) {
      setError(String(caught));
    }
  }, [persona]);

  useEffect(() => {
    void loadContacts();
  }, [loadContacts]);

  async function score(event: React.FormEvent) {
    event.preventDefault();
    if (!contactId) return;
    setBusy(true);
    setError("");
    setScorecard(null);
    setTranscript(null);
    try {
      const headers = { "Content-Type": "application/json", "X-Dev-Persona": persona };
      const scored = await fetch(API + "/v1/scorecards", {
        method: "POST",
        headers,
        body: JSON.stringify({ contact_id: contactId }),
      });
      const body = await scored.json();
      if (!scored.ok) {
        setError(String(body.detail ?? scored.statusText));
        return;
      }
      setScorecard(body as Scorecard);
      const shown = await fetch(API + "/v1/contacts/" + contactId + "/transcript", {
        headers: { "X-Dev-Persona": persona },
        cache: "no-store",
      });
      if (shown.ok) setTranscript((await shown.json()) as TranscriptView);
    } catch (caught) {
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  }

  const chosen = contacts.find((contact) => contact.contact_id === contactId);
  const failing = (scorecard?.findings ?? []).filter((finding) => finding.status !== "present");
  const passing = (scorecard?.findings ?? []).filter((finding) => finding.status === "present");
  const allSpans = (scorecard?.findings ?? []).flatMap((finding) => finding.evidence);
  const cues = (scorecard?.signals ?? []).filter((signal) => signal.detected);

  return (
    <main>
      <h1>{card?.name ?? "Conversation QA scorecard"}</h1>
      <p className="sub">
        {card?.description ??
          "Every finding, score and disposition below is computed by deterministic code and cites the turn and characters it came from."}
      </p>

      <form onSubmit={score}>
        <fieldset>
          <legend>Who you are</legend>
          <label>
            Seeded dev persona (local profile only; the server resolves identity, not this field)
            <select value={persona} onChange={(event) => setPersona(event.target.value)}>
              {PERSONAS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </fieldset>

        <fieldset>
          <legend>The contact</legend>
          <label>
            Contact (only this tenant&apos;s contacts are listed, filtered server-side)
            <select value={contactId} onChange={(event) => setContactId(event.target.value)}>
              {contacts.map((contact) => (
                <option key={contact.contact_id} value={contact.contact_id}>
                  {contact.contact_id} ({contact.market})
                </option>
              ))}
            </select>
          </label>
          {chosen?.headline ? <p className="hint">{chosen.headline}</p> : null}
          {chosen ? (
            <p className="hint">
              Declares {chosen.declared_requirement_ids.length} obligations:{" "}
              {chosen.declared_requirement_ids.join(", ")}
            </p>
          ) : null}
          <button type="submit" disabled={busy || !contactId}>
            {busy ? "Scoring" : "Score this contact"}
          </button>
        </fieldset>
      </form>

      {error ? <pre className="result error">{error}</pre> : null}

      {scorecard ? (
        <>
          <section className={"verdict " + (scorecard.requires_human_review ? "bad" : "ok")}>
            <h2>{scorecard.disposition.replace(/_/g, " ")}</h2>
            <p className="sub">
              {scorecard.contact_id} / {scorecard.market} / pack {scorecard.pack_id}@
              {scorecard.pack_version} / engine {scorecard.engine_version}
            </p>
            <div className="figures">
              <div>
                <span className="figure">{scorecard.disclosure_score.toFixed(2)}</span>
                <span className="figure-label">disclosure coverage</span>
              </div>
              <div>
                <span className="figure">{scorecard.adherence_score.toFixed(2)}</span>
                <span className="figure-label">script adherence</span>
              </div>
              <div>
                <span className="figure">{scorecard.sentiment_score}</span>
                <span className="figure-label">sentiment</span>
              </div>
              <div>
                <span className="figure">{scorecard.severity}</span>
                <span className="figure-label">worst severity</span>
              </div>
              <div>
                <span className="figure">{scorecard.redaction_count}</span>
                <span className="figure-label">identifiers masked</span>
              </div>
            </div>
            <p className="hint">
              {scorecard.requires_human_review
                ? "Routed to human review: " + (scorecard.review_ref || "NOT ROUTED")
                : "No review needed. Nothing was manufactured for a clean contact."}
            </p>
          </section>

          <section className="panel">
            <h3>Unmet obligations</h3>
            {failing.length === 0 ? (
              <p className="hint">None. Every declared obligation was met.</p>
            ) : (
              <ul className="findings">
                {failing.map((finding) => (
                  <li key={finding.requirement_id} className={FAIL_TONE[finding.status] ?? "bad"}>
                    <span className="rid">{finding.requirement_id}</span>
                    <span className="status">{finding.status.replace(/_/g, " ")}</span>
                    <span className="sev">{finding.severity}</span>
                    <p className="detail">{finding.detail}</p>
                    {finding.remediation ? (
                      <p className="hint">Remediation: {finding.remediation}</p>
                    ) : null}
                    <p className="hint">Cites {finding.citation.title}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="panel">
            <h3>Met obligations, with the words that met them</h3>
            <ul className="findings">
              {passing.map((finding) => (
                <li key={finding.requirement_id} className="ok">
                  <span className="rid">{finding.requirement_id}</span>
                  <span className="status">present</span>
                  {finding.evidence.map((span, index) => (
                    <p className="detail" key={index}>
                      turn {span.turn_index} [{span.char_start}:{span.char_end}] at{" "}
                      {clock(span.start_ms)} : <q>{span.text}</q>
                    </p>
                  ))}
                </li>
              ))}
            </ul>
          </section>

          {cues.length > 0 ? (
            <section className="panel">
              <h3>Deterministic cues</h3>
              <ul className="findings">
                {cues.map((signal) => (
                  <li key={signal.cue_id} className={signal.kind === "vulnerability" ? "warn" : ""}>
                    <span className="rid">{signal.label}</span>
                    <span className="status">{signal.kind}</span>
                    <span className="sev">{signal.severity}</span>
                    {signal.evidence.map((span, index) => (
                      <p className="detail" key={index}>
                        turn {span.turn_index} : <q>{span.text}</q>
                      </p>
                    ))}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {transcript ? (
            <section className="panel">
              <h3>Turn timeline (redacted, with the cited spans highlighted)</h3>
              <ol className="timeline">
                {transcript.turns.map((turn) => (
                  <li key={turn.index} className={turn.role}>
                    <span className="meta">
                      {turn.index} / {turn.role} / {clock(turn.start_ms)}
                    </span>
                    <p>
                      {segments(turn, allSpans).map((part, index) =>
                        part.hit ? (
                          <mark key={index}>{part.text}</mark>
                        ) : (
                          <span key={index}>{part.text}</span>
                        ),
                      )}
                    </p>
                  </li>
                ))}
              </ol>
              <p className="hint">
                {transcript.redaction_count} identifier(s) masked before the engine ran, so these
                offsets are the ones the findings cite.
              </p>
            </section>
          ) : null}

          <section className="panel">
            <h3>Instruments cited</h3>
            <ul className="citations">
              {scorecard.citations.map((citation) => (
                <li key={citation.source_id}>
                  <strong>{citation.source_id}</strong> {citation.title}
                </li>
              ))}
            </ul>
          </section>

          {scorecard.narration ? (
            <section className="panel">
              <h3>Narration</h3>
              <p>{scorecard.narration.body}</p>
              <p className="hint">
                Drafted by {scorecard.narration.model || "unknown"}. It may only restate figures
                the engine published; a draft that invents one is discarded.
              </p>
            </section>
          ) : null}
        </>
      ) : null}

      <footer>
        Synthetic, obviously fictional data only. Identity is resolved server-side and the
        client-asserted actor is discarded; see ui/README.md for the embedding contract.
      </footer>
    </main>
  );
}
