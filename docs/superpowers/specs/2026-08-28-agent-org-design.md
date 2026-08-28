# Autonomous Agent Org — design

Date: 2026-08-28
Status: approved (approach 1: scaffold + protocol + deterministic scripts)
Branch: `feat/agent-org`

## Context

The ai-native-sdlc repo ships a workflow that runs Plan → Design → Build →
Test → Deploy → Maintain with versioned artifacts and human approval gates.
What it does not ship is an *org*: a model of who the agents are, who reports
to whom, how agents review each other, and where demand enters the system.

This feature adds an **autonomous agent org** capability: scaffold a named,
role-based set of agents into any project; let them run the existing loop with
peer review and upward reporting; involve the human owner only at the critical
points (intent ambiguity, unresolved disagreement, PR merge, release); and feed
the org from real user demand (GitHub issues, app feedback forms, email).

## Goals

- Scaffold an agent org into a project in one command (`init_org.py`).
- Default roles: CEO (human), CTO, product manager, product engineering agent,
  engineers, reviewer — configurable in `org/org-chart.yaml`.
- Peer review of every artifact (code, plans, specs) by a different agent;
  reviewers accept work only when idle; writers never self-review.
- Multi-channel product intake: GitHub issues, app feedback forms, email — all
  landing as versioned markdown records in `org/intake/`.
- Human involvement exactly where the user wants it: PM first review of
  intents (CEO on ambiguity), CEO on unresolved spec/plan disagreement, and
  CEO approval for PR merge and release.
- A real proof: the expense-tracker example becomes a static web app,
  deployable to Vercel, so the product can be seen live.

## Non-goals (this slice)

- An always-on orchestrator daemon (the status/queue files leave room for it).
- Native email/form connectors (intake folders + record format now).
- Any model runtime: agents run through Codex/Claude per their role cards.

## Org model

```
CEO (human, owner)
 └── CTO (agent) — routes work, dispatches reviews, resolves conflicts
      ├── Product manager (agent) — first review of every intent
      ├── Product engineering agent (agent) — GitHub/forms/email intake → tickets
      └── Engineers (agents, count configurable) — plan and build
           └── Reviewer (agent, count configurable) — peer review when idle
```

Authority and gates:

| Gate | Who decides |
|---|---|
| Intent | Product manager first; CEO on ambiguity |
| Spec / plan | Peer review; CEO after two unresolved disagreement rounds |
| PR merge | Human (CEO) |
| Release | Human (CEO) — production gate + gate ledger record |

## Components

### `scripts/init_org.py`

Scaffolds into any project (`--dry-run`, `--force`):

- `org/org-chart.yaml` — roles, reports-to, authority, gate rules, escalation
- `org/status.yaml` — live agent states (busy/idle) + review queue
- `org/protocol.md` — reporting, peer review, escalation, gates, intake
- `org/roles/*.md` — one card per role (CEO, CTO, PM, product-engineer,
  engineer, reviewer)
- `org/intake/` — `README.md`, `config.json`, and channel folders
  (`github/`, `forms/`, `email/`)
- `org/reviews/README.md` — evidence-backed review record format
- `scripts/sync_issues.py` — GitHub intake helper

### `scripts/sync_issues.py`

- `pull` — fetch open issues via `gh`, write `org/intake/github/<repo>-<n>.md`
  for new issues; idempotent (state file tracks last-seen issue per repo).
- `push` — create a GitHub issue from a consolidated record
  (`--title`, `--body`, `--repo`, `--labels`).
- `--dry-run` everywhere; JSON config/state (stdlib-only, testable without
  PyYAML or network).

### Org templates (in `assets/org/`)

Role cards, org chart, status file, protocol, intake/review READMEs — copied by
`init_org.py`, following the repo convention that templates live in `assets/`
and are never edited to fit one project.

## Data flow

1. Demand arrives: GitHub issue (`sync_issues.py pull`), form record, or email
   record → `org/intake/<channel>/`.
2. Product engineering agent consolidates demand, files a feature ticket
   (`sync_issues.py push` where a public record helps), and drafts `intent.md`
   from the template.
3. Product manager reviews the intent first; ambiguity escalates to CEO.
4. Accepted intent → Design → spec → peer review → plan → peer review →
   Build (code + tests) → code review.
5. PR merge (CEO) → release authorization (CEO, production gate + ledger) →
   live deploy.
6. Unresolved disagreement after two review rounds → CTO → CEO.

## Error handling

- Reviewer busy → review waits in `org/status.yaml` queue; routed when idle.
- GitHub unavailable/rate-limited → script exits non-zero, state not advanced,
  reruns idempotently.
- Duplicate demand → deduped by issue number / record id.
- No human available → gates wait; the loop pauses, never bypasses.

## Testing

- `tests/test_init_org.sh` — dry-run writes nothing; scaffold contains all org
  files; key content markers present; scaffolded script compiles.
- `tests/test_sync_issues.py` — pull writes records and dedupes (mocked `gh`);
  push builds the right command; dry-run writes nothing. No network required.
- `quick_validate.py` extended: org assets promised, org scaffold smoke test,
  new scripts compile. CI (`self-check.yml`) runs the new integration test.

## Proof: expense-tracker demo

`examples/expense-tracker/` becomes a real static app (HTML/CSS/vanilla JS,
localStorage persistence) that deploys to Vercel as-is (`vercel.json`
included). The workflow artifacts already in the folder (intent.md, spec.md,
plan.md, CLAUDE.md, workflow-graph.yaml) remain as the record of how the org
ran the project.

## Adoption

1. `python3 skills/ai-native-sdlc/scripts/init_workflow.py my-project --name "..." --git`
2. `python3 skills/ai-native-sdlc/scripts/init_org.py my-project`
3. Commit both skeletons; agents (Codex/Claude) run the loop per
   `org/protocol.md` and the role cards.
4. Configure `org/intake/config.json`, run `sync_issues.py pull` on a schedule
   or in CI to keep the org fed.

## Open decisions

- Demo host: **Vercel static** (chosen). No backend; localStorage persistence.
- Role set: CEO, CTO, PM, product-engineer, engineer, reviewer (default;
  counts and roles editable in `org-chart.yaml`).
