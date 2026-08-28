---
name: ai-native-sdlc
version: 0.1.0
description: Run the AI-native SDLC loop — Plan, Design, Build, Test, Deploy, Maintain — with versioned artifacts and human approval gates at every handoff. Use when the user states a goal, idea, feature, or change request and expects the agent to scaffold and drive the project through the full lifecycle instead of jumping straight to code.
---

# AI-Native SDLC

Turn a stated goal into a planned, built, tested, deployed, and monitored project by running the loop from Anthropic's AI-Native SDLC playbook: **Plan → Design → Build → Test → Deploy → Maintain**.

Every phase ends by committing a versioned artifact to git; the next phase starts by reading it. The commit chain is the audit trail: who asked, what the agent produced, and who approved. The agent does the generating, verifying, and mechanical work. Humans keep the judgment calls and the final approvals.

## Frameworks

The workflow is framework-agnostic. Claude Code calls the repository-memory file `CLAUDE.md` and keeps skills in `.claude/skills/`; Codex calls the repository-memory file `AGENTS.md` and installs skills into `~/.codex/skills/`. Wherever this skill says CLAUDE.md, use the repository-memory file your framework recognizes. The same skill folder installs in either environment, and the artifacts (`intent.md`, `spec.md`, `plan.md`, `REVIEW.md`, `bands.yaml`) are framework-neutral.

## Hard rules

1. **Human gates are real gates.** Do not advance without approval: intent accepted → Design; spec approved → Build; plan approved → code; PR merged → Deploy; release authorized → production.
2. **Never cross the production gate.** Agent-generated changes stop at the merge/release boundary. High-risk actions (production config, migrations, releases) require explicit human authorization, enforced by a hook where possible.
3. **Verify before asking for review.** Run build, tests, lint, and screenshots yourself first; fix what fails; only then hand work to a human.
4. **Encode repeated lessons.** The same mistake twice → write the correction into the project's CLAUDE.md, a skill, or a hook.
5. **Evidence in reviews.** Every finding cites file/line and concrete evidence, ordered by severity, with at most 5 nit comments per review.
6. **Plan mode first.** Nothing is implemented without an accepted plan; when implementation departs from the plan, update plan.md in the same commit.
7. **Reviews feed back.** When a review flags a mistake for the second time, the correction goes into CLAUDE.md as part of that review.
8. **Subagents are named, visible, and accountable.** Scaffold subagents only with a functional name, a committed definition, an explained dispatch, and a bounded report with evidence — never a silent background worker that can idle or act on stale context.

## Rule → enforcement matrix

Each hard rule is backed by an advisory layer (makes compliance likely), a
deterministic layer (makes violation nearly impossible), or a review pass
(checked at a gate). Use this when compliance asks "how is this enforced?"

| Hard rule | Advisory (skill/CLAUDE.md) | Deterministic (hook/file) | Checked at |
|---|---|---|---|
| 1. Gates are real | CLAUDE.md conventions | committed artifact chain in git; hooks | gate acceptance commits |
| 2. Never cross the production gate | this skill | `production-gate.sh` + approval/expiry; release approvals recorded in the hash-chained gate ledger (`gate_ledger.py`) | Deploy gate |
| 3. Verify before review | CLAUDE.md "Verifying your work" | single `make`-style verify commands | PR template evidence |
| 4. Encode repeated lessons | CLAUDE.md "Things the agent gets wrong" | protected-path hooks | second-time-mistake rule in reviews |
| 5. Evidence in reviews | REVIEW.md | — | review passes, 5-nit cap |
| 6. Plan mode first | plan.md template | plan-sync pre-commit hook (optional) | plan-vs-diff check in PR review |
| 7. Reviews feed back | CLAUDE.md | — | review comments → CLAUDE.md |
| 8. Subagents named/visible | this hard rule | subagent definitions committed in git | subagent reports in session |

Anything in the deterministic column must always hold — enforce it with code,
not prose.

## Starting from an idea

When the user gives a goal or idea and there is no workflow in place yet:

1. Scaffold the artifact skeleton with `scripts/init_workflow.py <project-dir> --name "<project name>" --framework codex|claude` (codex writes AGENTS.md, claude writes CLAUDE.md), or copy templates from `assets/` into an existing repo.
2. Run **Plan**: interview the user with analyst-style questions — what cannot be done today, who is affected, what success looks like, constraints, what is out of scope — until the idea is concrete.
3. Write `intent/intent.md` from the template, commit it, and ask the product owner to accept or reject. Acceptance triggers Design.

If the user is already inside a later phase (for example, "review this PR" or "diagnose this incident"), start at that phase instead.

## Running the loop

Read `references/playbook.md` for the full phase-by-phase procedure: inputs, steps, outputs, exit gates, and proven prompt patterns for each phase.

At a glance:

| Phase | Reads | Produces | Gate (human approval) |
|---|---|---|---|
| Plan | user's idea | intent.md | accepted → Design |
| Design | intent.md + org standards | spec.md | approved → Build |
| Build | intent.md + spec.md | plan.md → code + tests → PR | plan approved before code; PR merged → Deploy |
| Test | repo + eval suite | eval results, regression evals | config changes that drop pass rate are reviewed |
| Deploy | merged PR + review findings | authorized release | agentic review + explicit release authorization |
| Maintain | production metrics | diagnosis → new intent.md | on-call triage: fix, schedule, or adjust thresholds |

## Graph engineering

The loop is a directed graph, not a linear pipeline: plays and artifacts are nodes, gates are the human approval points, and triggers are the edges that fire the next stage. Treating the workflow as a graph makes it automatable (accepted artifacts fire the next gate), parallelizable (independent branches run in separate worktrees), and auditable (node history is the record). The plays also form a separate adoption graph — start at the leaf plays and build outward. Read `references/graph.md` when designing or automating how phases trigger each other; the machine-readable form ships in `assets/workflow-graph.example.yaml`.

## Autonomous agent org

For teams that want the loop to run with less human steering, the skill can
scaffold an **agent org**: named roles (CEO-human, CTO, product manager,
product engineering agent, engineers, reviewer) with an org chart, a reporting
protocol, peer review of every artifact, and multi-channel demand intake
(GitHub issues, forms, email). Agents run the phases, review each other's work,
and escalate to the human CEO only at the critical gates (intent ambiguity,
unresolved disagreement, PR merge, release). Scaffold it with
`scripts/init_org.py <project-dir>`; full detail in `references/org.md`.

## Templates and assets

The scaffold script copies the core skeleton into a new project (`intent.md`,
`CLAUDE.md`/`AGENTS.md`, `REVIEW.md`, `bands.yaml`, `production-gate.sh`,
`workflow-graph.yaml`, `.gitignore`, `evals/example.md`, `evals/README.md`,
`gates/README.md`, and the tool scripts `gate_ledger.py`/`run_evals.py`/
`detect_bands.py`); copy these manually when extending an existing repo. `spec.md` and `plan.md`
are produced by the workflow itself during Design and Build — copy the blank
forms only when you want them as starting points:

- `assets/intent.md` — intent capture (problem, proposed outcome, affected users/systems, constraints, out of scope, open questions)
- `assets/spec.md` — requirements + design specification with gotchas (produced during Design)
- `assets/plan.md` — build plan (files, order, risks, proof, verification) (produced during Build)
- `assets/CLAUDE.md` — repository-memory starter (commands, verification, conventions, common mistakes)
- `assets/REVIEW.md` — review standards (passes, evidence, severity, 5-nit cap)
- `assets/bands.yaml` — monitoring control bands for Maintain
- `assets/production-gate.sh` — release authorization hook (deploy-action patterns, expiry, read-only allowlist, ledger-backed approvals)
- `assets/gates-README.md` — gate ledger usage (copied into new projects as `gates/README.md`)
- `assets/evals.example.md` — eval case format for Test (reference form)
- `assets/evals.example.json` — canonical JSON eval format consumed by `scripts/run_evals.py`
- `assets/evals-README.md` — how to add evals (copied into new projects as `evals/README.md`)
- `assets/workflow-graph.example.yaml` — the loop as a directed graph (nodes, gates, trigger edges)
- `assets/workflow-graph.yaml` — blank project graph state (copied into new projects)
- `assets/incident.md` — incident record template for Maintain (severity definitions, timeline, eval follow-up)
- `assets/runbooks/rollback-deploy.md` + `assets/runbooks/README.md` — pre-approved action paths that `bands.yaml` 3σ routes may trigger
- `assets/PULL_REQUEST_TEMPLATE.md` — change request mapped to REVIEW.md passes + evidence
- `assets/org/org-chart.yaml` — agent org role model (roles, reports-to, authority, gates; copied by `scripts/init_org.py`)
- `assets/org/status.yaml` — live agent states (busy/idle) + review queue
- `assets/org/roles/*.md` — role cards (CEO, CTO, product manager, product engineering agent, engineer, reviewer)
- `assets/org/protocol.md` — reporting, peer review, escalation, and gate rules for the org
- `assets/org/intake/` + `assets/org/reviews/README.md` — demand intake (github/forms/email) and evidence-backed review record formats

Organization-level examples to wire up during adoption:

- `assets/hook-settings.example.json` — hook wiring for Claude Code
- `assets/agent-evals.yml.example` — CI eval workflow (calls `scripts/run_evals.py`)
- `assets/managed-settings.example.json` — regulated-enterprise managed settings

## Scripts

- `scripts/init_workflow.py` — scaffold the artifact skeleton into a new project (`--dry-run`, `--framework`, `--git`)
- `scripts/init_org.py` — scaffold the autonomous agent org (roles, protocol, intake; `--dry-run`, `--force`)
- `scripts/quick_validate.py` — validate the skill/plugin bundle (self-check; CI runs it)
- `scripts/run_evals.py` — run the eval suite locally or in CI (Phase 4), with `--min-pass-rate` gating
- `scripts/detect_bands.py` — deterministic control-band detection (Phase 6 reference implementation: rolling window, Western Electric rules, drift rule)
- `scripts/gate_ledger.py` — hash-chained approval ledger: every gate decision is a committed, tamper-evident record; the release gate verifies `RELEASE_APPROVAL=ledger:<id>` against it
- `scripts/sync_issues.py` — GitHub issue intake for the product engineering agent (`pull` open issues into `org/intake/github/`, `push` a feature ticket)

## Self-test (after installing)

1. `python3 skills/ai-native-sdlc/scripts/init_workflow.py /tmp/wf-demo --name "Demo" --git`
2. Tell your agent: *run the AI-native SDLC workflow from this intent.*
3. Accept the intent, approve the spec, and confirm the agent stops at every
   gate and never crosses the production gate.

## Customization

Read `references/adoption.md` when tailoring the workflow to an organization or when asked how to roll this out gradually. It covers staged adoption, encoding org standards as skills, wiring hooks, building the eval suite, and keeping existing tools (Jira, Figma, GitHub, Slack) in place.
