# AI-Native SDLC — reusable workflow repo

![AI-Native SDLC workflow loop: Plan → Design → Build → Test → Deploy → Maintain](assets/ai-native-sdlc-banner.png)

A ready-to-inherit implementation of Anthropic's [AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook): give your coding agent a goal or idea, and it scaffolds and drives the project through the full lifecycle — planning, design, build, test, deploy, and maintain — with human approval gates at every handoff.

This repo is three things at once:

- A **Codex skill** at `skills/ai-native-sdlc/`, installable into `~/.codex/skills`.
- A **Claude Code skill** — the same folder, installable into `~/.claude/skills`.
- A **Codex plugin** (`.codex-plugin/plugin.json` at the repo root) that bundles the skill, so teams can publish or fork it as their workflow baseline.

**Learn more:** [Phase-by-phase playbook](skills/ai-native-sdlc/references/playbook.md) · [Staged adoption guide](skills/ai-native-sdlc/references/adoption.md) · [Workflow as a directed graph](skills/ai-native-sdlc/references/graph.md) · [Agent org](skills/ai-native-sdlc/references/org.md) · [Worked example](examples/expense-tracker/)

## What this is about

Writing code is no longer the bottleneck — agents produce it in hours. The bottleneck moved to the process around the code: planning, review, deployment, and governance still run at human speed and human scale. This repo reworks the SDLC so those stages keep up with the build. The loop is Plan → Design → Build → Test → Deploy → Maintain; every stage ends by committing a versioned artifact the next stage reads, human judgment concentrates at gates instead of line-by-line review, guardrails run as deterministic hooks rather than habits, and continuous evals replace stage-gate QA. The operating principle, in one sentence: the agent can do everything up to the production gate, but never crosses it.

## Graph engineering

The loop is a directed graph, not a linear pipeline. Plays and artifacts are nodes, gates are human approval points, and triggers are the edges that fire the next stage: an accepted intent fires Design, an approved spec fires plan mode, a merged PR fires the pipeline, and a breached control band writes the next intent. Treating the workflow as a graph makes it automatable, parallelizable (independent branches run in separate worktrees), and auditable (node history is the record). The plays also form a separate adoption graph — start at the leaf plays (capture intent, CLAUDE.md, feedback loop, hooks, plan mode) and build outward. The machine-readable form is `skills/ai-native-sdlc/assets/workflow-graph.example.yaml`; full detail in `skills/ai-native-sdlc/references/graph.md`.

## The workflow

```
Plan → Design → Build → Test → Deploy → Maintain
  ↑                                              │
  └────────────────── back to Plan ←─────────────┘
```

Each phase ends by committing a versioned artifact; the next phase starts by
reading it. The full phase → artifact → gate contract lives in
`skills/ai-native-sdlc/SKILL.md` (single source of truth). The short version:
**Plan** commits `intent.md`, **Design** commits `spec.md`, **Build** commits
`plan.md` then code + tests, **Test** reports eval results, **Deploy** ships an
authorized release, **Maintain** writes its diagnosis back as a new
`intent.md`.

The agent does the generation, verification, and mechanical work. Humans keep the judgment calls: the agent goes all the way to the production gate and never crosses it.

**Framework mapping.** The skill is framework-neutral: Claude Code calls repository memory CLAUDE.md and keeps skills in `.claude/skills/`; Codex calls it AGENTS.md and installs skills into `~/.codex/skills/`. The templates, artifacts, and scaffold script are the same either way.

## Install

### As a Codex skill

```bash
mkdir -p ~/.codex/skills
cp -R skills/ai-native-sdlc ~/.codex/skills/
```

### As a Claude Code skill

```bash
mkdir -p ~/.claude/skills
cp -R skills/ai-native-sdlc ~/.claude/skills/
```

### As a Codex plugin

Clone or copy this repo to `~/plugins/ai-native-sdlc`, then add it to your personal marketplace at `~/.agents/plugins/marketplace.json`:

```json
{
  "name": "personal",
  "interface": {
    "displayName": "Personal"
  },
  "plugins": [
    {
      "name": "ai-native-sdlc",
      "source": {
        "source": "local",
        "path": "./plugins/ai-native-sdlc"
      },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

For a team, publish the repo and point a marketplace at it instead.

Installing the plugin also makes the bundled skill available, so you don't need to also copy it into `~/.codex/skills` — choose one path.

### Or inherit the repo directly

Fork it, keep the skill and templates, and drop in your organization's standards. The repo models the workflow it ships, so agents working inside it (via the root `AGENTS.md`) follow the same conventions.

## Use it

Start a new project:

```bash
python3 skills/ai-native-sdlc/scripts/init_workflow.py my-project --name "My idea"
```

Then tell your agent:

> $ai-native-sdlc: I want to build an expense tracker. Start with the intent.

The agent interviews you until the idea is concrete, writes `intent/intent.md`, commits it, and asks you to accept. From there it moves through spec → plan → build → test → deploy, stopping at each approval gate, and finally wires up monitoring so the loop can close back into new intents.

Already have a project? Copy the templates into it:

```bash
cp skills/ai-native-sdlc/assets/{intent.md,spec.md,plan.md,CLAUDE.md,REVIEW.md,bands.yaml} .
cp skills/ai-native-sdlc/assets/production-gate.sh hooks/
```

## Autonomous agent org

For teams that want the loop to run with less human steering, the skill can
scaffold an **agent org** into any project — named roles with an org chart,
peer review of every artifact, and multi-channel demand intake:

```bash
python3 skills/ai-native-sdlc/scripts/init_org.py my-project
```

The default org: **CEO (you, human)** → **CTO (agent)** → product manager,
product engineering agent, engineers, and reviewer. Agents run the phases,
review each other's work (reviewers accept only when idle), and report up the
chain; the human CEO is involved at the critical points — intent ambiguity,
unresolved spec/plan disagreement, PR merge, and release. Demand enters
through GitHub issues (`scripts/sync_issues.py pull`), app feedback forms, and
email, all landing as versioned records in `org/intake/` that the product
engineering agent turns into tickets and intents.

See `skills/ai-native-sdlc/references/org.md` for the full protocol.

## Examples

The `examples/` folder contains a worked project — the expense-tracker idea
from this README. It shows what `intent.md`, `spec.md`, `plan.md`, `CLAUDE.md`,
and the workflow graph look like when filled in, and it is also a real static
app (`index.html`, `app.js`, `style.css`) with expenses persisted in the
browser. Open `examples/expense-tracker/index.html` locally, or deploy it to
Vercel (`vercel` from that directory) to see the product live. Use the example
as reference for tone and structure, then scaffold your own blanks with the
script above.

## Customizing for your organization

- **Standards as skills** — encode brand, security, UX, and compliance policies as skills so Design and Build apply them consistently.
- **Hooks as red lines** — protected paths, secrets, and the release gate go in deterministic hooks, not prose. `production-gate.sh` blocks deploys without human authorization; `hook-settings.example.json` shows the wiring; `managed-settings.example.json` is the regulated-enterprise starting point.
- **Evals** — collect 20–50 real tasks with expected outcomes; run them in CI on every config change and after every incident (`agent-evals.yml.example`).
- **Review culture** — `REVIEW.md` sets the passes (bugs, security, compliance), the evidence requirement, and the 5-nit cap.
- **CI/CD and autonomy tiers** — agent triage runs non-interactively in the pipeline; dev is open, production needs a release manager; rollbacks are rehearsed.
- **Monitoring** — `bands.yaml` defines the control bands; 1σ logs, 2σ diagnoses, 3σ proposes a fix or runbook and writes the diagnosis back as a new intent.

See `skills/ai-native-sdlc/references/adoption.md` for the staged rollout order.

## Layout

```text
.
├── .codex-plugin/plugin.json      # Codex plugin manifest (repo root is the plugin)
├── .github/workflows/self-check.yml  # CI for this repo itself (validate + tests)
├── AGENTS.md                      # guidance for agents working in this repo
├── README.md
├── CHANGELOG.md
├── LICENSE
├── SECURITY.md
├── examples/
│   ├── README.md
│   └── expense-tracker/           # worked example + real static app (Vercel-ready)
├── tests/                         # gate, scaffold, band-detector, eval-runner tests
├── docs/superpowers/specs/        # design docs (this feature's spec lives here)
└── skills/
    └── ai-native-sdlc/
        ├── SKILL.md               # skill entrypoint (versioned; rule→enforcement matrix)
        ├── agents/openai.yaml     # UI metadata
        ├── references/
        │   ├── playbook.md        # phase-by-phase procedures
        │   ├── adoption.md        # staged rollout + org customization
        │   ├── graph.md           # the loop as a directed graph
        │   └── org.md             # autonomous agent org (roles, review, intake)
        ├── assets/                # templates copied into target projects
        │   ├── intent.md
        │   ├── spec.md
        │   ├── plan.md
        │   ├── CLAUDE.md
        │   ├── REVIEW.md
        │   ├── bands.yaml
        │   ├── production-gate.sh
        │   ├── org/               # agent org templates (chart, roles, protocol)
        │   ├── evals.example.md
        │   ├── evals.example.json
        │   ├── evals-README.md
        │   ├── workflow-graph.example.yaml
        │   ├── workflow-graph.yaml
        │   ├── incident.md
        │   ├── runbooks/          # rollback-deploy.md, README.md
        │   ├── PULL_REQUEST_TEMPLATE.md
        │   ├── gates-README.md
        │   ├── hook-settings.example.json
        │   ├── agent-evals.yml.example
        │   └── managed-settings.example.json
        └── scripts/
            ├── init_workflow.py   # scaffolds the artifact skeleton
            ├── init_org.py        # scaffolds the autonomous agent org
            ├── sync_issues.py     # GitHub issue intake (pull/push)
            ├── quick_validate.py  # skill/plugin self-check
            ├── run_evals.py       # eval-suite runner (Phase 4)
            ├── detect_bands.py    # control-band detection (Phase 6)
            └── gate_ledger.py     # hash-chained approval ledger (all gates)
```

## Contributing

Contributions are welcome — this repo practices what it ships. Open an issue
first for larger ideas (new phases, changed artifacts, redesigns); for fixes
and small improvements, fork, branch, and open a pull request.

### Getting started

1. Fork the repo and clone your fork.
2. Create a branch: `git checkout -b my-change`.
3. Make your change, then run the self-check suite from the repo root:

   ```bash
   python3 skills/ai-native-sdlc/scripts/quick_validate.py skills/ai-native-sdlc
   bash tests/test_gate.sh
   bash tests/test_init.sh
   python3 -m unittest discover -s tests -v
   ```

   All checks must pass before opening the PR — CI runs the same checks on
   every pull request (`.github/workflows/self-check.yml`).
4. Add a `CHANGELOG.md` entry under `[Unreleased]` for user-visible changes.
5. Commit with a clear message and open the pull request.

### Conventions

- Templates in `skills/ai-native-sdlc/assets/` are copied into target
  projects — never edit them to fit one project; change
  `scripts/init_workflow.py` when the scaffold layout changes.
- Keep `SKILL.md` short; put phase detail in `references/`.
- Keep artifacts generic — organization specifics belong in the adopter's own
  skills and hooks, never in shared templates.
- Keep `plugin.json` and the SKILL.md frontmatter `version` in sync (semver);
  `quick_validate.py` enforces this.
- Follow the existing commit style: `feat:`, `fix:`, `docs:`, `chore:`, `test:`.

### Reporting vulnerabilities

Security-sensitive bugs (release-gate bypasses, hook failures,
managed-settings weaknesses) should not be filed as public issues. See
`SECURITY.md` for how to report them privately.

By contributing, you agree your contributions are licensed under the MIT
license (see `LICENSE`).

## Attribution and license

Based on [The AI-Native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook) by Anthropic's Applied AI team (2026). MIT licensed — see `LICENSE`.
