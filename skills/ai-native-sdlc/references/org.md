# The autonomous agent org

The org is an operating layer on the AI-native SDLC loop. It defines who the
agents are, how they communicate, what they remember, and how a bounded loop
advances work. The CEO remains the human merge/release gate.

## Org model

Default roles, scaffolded by `scripts/init_org.py` and editable in
`org/org-chart.yaml`:

```text
CEO (human, owner)
 └── orchestrator (agent)
      ├── product-manager
      ├── architect
      ├── senior-developer
      ├── security-researcher
      ├── reviewer
      └── memory-keeper
```

The orchestrator replaces the CTO. It routes work and runs the loop, but never
implements, reviews, merges, or releases. The optional `product-engineer` role
is disabled by default; enable it when a project wants a dedicated intake and
ticket-drafting agent.

Role cards live in `org/roles/*.md`; state lives in `org/status.yaml`. An agent
marks itself busy when it accepts work and idle when it finishes. Reviewers
accept a review only when idle.

```bash
python3 scripts/org_status.py status --json
python3 scripts/org_status.py agent senior-developer busy --assignment plan.md
python3 scripts/org_status.py agent senior-developer idle --last-report "tests pass"
python3 scripts/org_status.py review assign --id review-3 --artifact plan.md \
  --writer senior-developer --reviewer reviewer
python3 scripts/org_status.py review submit --id review-3 \
  --verdict changes --evidence "https://github.com/org/repo/reviews/3"
python3 scripts/org_status.py review escalate --id review-4
```

## Communication

Messages are versioned markdown files in `org/messages/`, named
`<from>-<sequence>.md`. `scripts/org_message.py` validates the parties against
`org/org-chart.yaml` and supports:

```bash
python3 scripts/org_message.py send --from senior-developer --to architect \
  --kind question --subject "Cache boundary" --artifact spec.md \
  --body-file /tmp/question.md
python3 scripts/org_message.py inbox --agent architect
python3 scripts/org_message.py reply --id senior-developer-0001 \
  --from architect --body-file /tmp/answer.md
python3 scripts/org_message.py check
```

Kinds: `question`, `answer`, `decision`, `handoff`, `review`, `finding`,
`escalation`, `status`. An open `requires_response` message blocks the
recipient's turn. After `max_no_progress` unanswered attempts the loop stops at
the CEO gate instead of retrying forever.

Preferred routes:

- senior-developer → architect / product-manager / security-researcher
- reviewer → author / security-researcher
- any role → orchestrator
- any role → memory-keeper

A `finding` is blocking when it has severity `high` or `critical`, or
`blocking: true`; it needs evidence before it can be closed.

## Memory

`org/memory/context.md` is the compact main context. `org/memory/events.jsonl`
is append-only and every event cites a source.

```bash
python3 scripts/org_memory.py add --kind decision --actor architect \
  --text "Use an append-only ledger" --source spec.md
python3 scripts/org_memory.py digest
python3 scripts/org_memory.py check
```

The machine block covers goal, loop state, agents, open messages, recent
decisions, constraints, artifacts, next action, and the last digest source.
Curated notes outside the block are preserved. The budget is 16 KB / 200 lines.

## Bounded loop

`scripts/org_loop.py` is the deterministic work loop. One turn is one dispatch
to one role. Defaults: `max_turns: 6`, `max_no_progress: 2`,
`max_review_rounds: 2`.

```bash
python3 scripts/org_loop.py init --work-item intent/intent.md
python3 scripts/org_loop.py next --json
python3 scripts/org_loop.py advance --role senior-developer --action implement \
  --result progress --artifact src/app.py --evidence .loop/tests.log
python3 scripts/org_loop.py check
```

`next` is pure routing; `advance` is the only state mutator. Stop reasons are
`solved`, `gate_required`, `failed`, `stalled`, and `exhausted`. `solved`
requires passing acceptance commands, reviewer approval, and no blocking
security finding. The loop never records an approval or crosses a human gate.

## Peer review and gates

Writers never review their own work. The orchestrator assigns an idle reviewer;
the reviewer records severity-ranked findings with evidence and a verdict. Two
unresolved rounds escalate to the orchestrator, then the CEO for spec/plan
conflicts.

| Gate | Who decides |
|---|---|
| Intent | Product manager first; CEO on escalation |
| Spec / plan | Peer review; CEO after two unresolved rounds |
| PR merge | Human (CEO) |
| Release | Human (CEO) — production gate + ledger record |

The org is autonomous within the loop. Nothing skips a gate when no human is
available; the loop pauses.

## Intake

Demand arrives as versioned markdown records in `org/intake/`:

- `github/` — `scripts/sync_issues.py pull`
- `forms/` and `email/` — `scripts/intake.py add|list`

The PM reviews every intent before Design. The optional product-engineer
consolidates records, files tickets, and drafts intents.

## Escalation

- Intent ambiguity → PM → CEO.
- Spec/plan disagreement → writer ↔ reviewer (max 2 rounds) → orchestrator → CEO.
- Unanswered consultation after `max_no_progress` attempts → CEO gate.
- Anything that would cross a gate without human approval → stop and report.

## Adoption

```bash
python3 skills/ai-native-sdlc/scripts/init_workflow.py my-project --name "My idea"
python3 skills/ai-native-sdlc/scripts/init_org.py my-project
cd my-project
git add -A && git commit -m "scaffold orchestrator-led agent org"
python3 scripts/org_loop.py init --work-item intent/intent.md
python3 scripts/org_message.py check
python3 scripts/org_memory.py digest
python3 scripts/org_memory.py check
python3 scripts/org_loop.py next --json
```

Copy `assets/org-loop.yml.example` to `.github/workflows/org-loop.yml` after
configuring the `ORG_AGENT_CMD` secret and the repository permissions. The
workflow dispatches one turn per run and opens a PR; it never merges or
releases.

## Framework mapping

The org is framework-neutral. Codex projects map role cards to subagent
configuration; Claude Code projects map them to `.claude/agents/*.md`. The
artifacts, protocol, messages, memory, and scripts are the same either way.
