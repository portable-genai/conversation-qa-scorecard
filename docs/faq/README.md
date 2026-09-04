# FAQ index

Answers to the questions different teams ask when evaluating, adopting or reviewing this
repository as a common base for post-contact conversation QA. Each page is written for one
audience; skim the one that matches your role.

| FAQ | For | Answers |
|---|---|---|
| [security-faq.md](security-faq.md) | AppSec and security review | server-side identity, the tenant boundary, what reaches a model, secrets, supply chain, the audit chain, what is in and out of scope |
| [portability-faq.md](portability-faq.md) | architecture, cloud, exit planning | the three profiles, the no-lock-in claim, the on-premises and sovereign exit, data export |
| [features-faq.md](features-faq.md) | product, QA and compliance operations | what the service decides, what is deterministic and what is not, and the boundary with sibling systems |
| [adoption-faq.md](adoption-faq.md) | engineering leads forking the repo | the rename, upstream fixes, the score pack as the real extension point, versioning |
| [compliance-faq.md](compliance-faq.md) | compliance, conduct, privacy, model risk | regulatory posture, evidence, maker-checker, residency, model-risk evidence |

These pages deliberately do NOT re-document capabilities owned by sibling systems in the
catalog. Where a concern belongs to another system (the guardrail gateway `agent-guardrail-gateway`, the human-review
console `human-review-console`, the AI-quality gate `model-quality-gate`, the observability and WORM audit sink `agent-observability`, the agent
registry `agent-registry`, the contact-centre copilot E1), the FAQ points at it and explains the boundary
rather than duplicating it. See [features-faq.md](features-faq.md) for the full map.

Authority order for anything these pages disagree with: `SPEC.md`, then `ARCHITECTURE.md`, then
`COMPLIANCE.md`, then `README.md`. These pages restate; they do not decide.
