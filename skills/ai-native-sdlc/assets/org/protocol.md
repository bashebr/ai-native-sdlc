# Org protocol — how the agents run

The org is a thin operating layer on top of the AI-native SDLC loop. Roles
execute the same phases and gates; this protocol adds reporting, peer review,
and escalation so the loop runs with less human steering.

## Reporting

1. Every agent commits its artifact with evidence.
2. Status changes ride in the same commit as the artifact.
3. Reports flow to the orchestrator, who reports to the CEO.
4. The orchestrator records every turn with `scripts/org_loop.py advance`.

## Communication

Use `scripts/org_message.py` for questions, decisions, handoffs, findings, and
escalations. Messages are committed in `org/messages/`. An open question blocks
the affected turn until answered or escalated. Preferred routes live in
`org/org-chart.yaml`; any known agent may message any other known agent.

## Memory

`org/memory/context.md` is the main context. Durable decisions go in
`org/memory/events.jsonl` with a source. The memory keeper curates it and the
orchestrator digests it after each turn.

## Peer review

Writers never review their own work. The orchestrator assigns an idle reviewer.
The reviewer records findings and a verdict. Two unresolved rounds escalate to
the orchestrator, then the CEO for spec/plan conflicts.

## Bounded loop

`scripts/org_loop.py` dispatches one role per turn with defaults
`max_turns: 6`, `max_no_progress: 2`, `max_review_rounds: 2`. It stops as
`solved`, `gate_required`, `failed`, `stalled`, or `exhausted`. It never
extends its own budget.

## Gates

| Gate | Who decides |
|---|---|
| Intent | Product manager first; CEO on escalation |
| Spec / plan | Peer review; CEO after two unresolved rounds |
| `pr_merge` | Human (CEO) |
| Release | Human (CEO) — production gate + ledger record |

## Escalation

- Intent ambiguity → PM → CEO.
- Spec/plan disagreement → writer ↔ reviewer (max 2 rounds) → orchestrator →
  CEO.
- Anything that would cross a gate without human approval → stop and report.

## Intake

- GitHub issues: `scripts/sync_issues.py pull` → `org/intake/github/<repo>-<n>.md`
- Forms/email: `scripts/intake.py add --source form|email …` writes
  `org/intake/forms/<record>.md` / `org/intake/email/<record>.md`.

The product engineering agent consolidates intake records, files tickets
(`scripts/sync_issues.py push`), and drafts intents for PM review.
