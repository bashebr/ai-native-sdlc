# Changelog

All notable changes to this repository are documented here. Versions are
semver; keep `plugin.json` and the `version` field in SKILL.md in sync
(`scripts/quick_validate.py` enforces this).

## [Unreleased]

### Added

- Deterministic workflow state runtime (`scripts/workflow_state.py`): `status`
  reports pipeline state and drift warnings, `advance` closes a graph node only
  through a matching chain-verified ledger record, and `check` validates graph
  schema/ledger consistency (`--strict` also rejects uncommitted
  advance-ready records). Scaffolded by `init_workflow.py`; `done_status` added
  to graph templates and the expense-tracker example.
- Deterministic plan-sync enforcement (`scripts/check_plan_sync.py`): PR/CI
  (`--base/--head`) and pre-commit (`--hook`) modes verify implementation
  changes against an approved `plan.md` "Files that change" manifest, with
  glob matching, process-file exclusions, and optional ledger-gate
  verification delegated to `gate_ledger.py`. Scaffolded by `init_workflow.py`.
- Agent-org operations tooling: `scripts/org_status.py` (validated busy/idle
  and review-queue transitions: assign, submit, escalate) and
  `scripts/intake.py` (form/email record ingestion and queue listing).
  Scaffolded by `init_org.py`.
- Autonomous agent org: `scripts/init_org.py` scaffolds named roles (CEO-human,
  CTO, product manager, product engineering agent, engineers, reviewer) with
  an org chart, status tracking, peer review, escalation, and multi-channel
  demand intake; templates in `assets/org/`, detail in `references/org.md`.
- `scripts/sync_issues.py` — GitHub issue intake for the product engineering
  agent: `pull` open issues into `org/intake/github/` (idempotent, state-tracked)
  and `push` feature tickets.
- Expense-tracker static demo app (`examples/expense-tracker/`): a working
  HTML/CSS/vanilla-JS app with localStorage persistence, deployable to Vercel.
- `scripts/gate_ledger.py` — hash-chained, version-controlled approval ledger:
  every gate decision is a tamper-evident record (`record`/`list`/`verify`);
  `verify --require-committed` and `--graph … --require-gates` completeness
  checks; the release gate now accepts `RELEASE_APPROVAL=ledger:<id>` and
  verifies the record before allowing a deploy.
- `assets/gates-README.md` — gate ledger usage (scaffolded as `gates/README.md`).
- `tests/test_gate_ledger.py` — chain integrity, tamper detection, expiry,
  wrong-decision, require-committed, and graph-completeness cases; ledger
  integration cases in `tests/test_gate.sh` (uncommitted/valid/unknown/
  tampered).
- `scripts/quick_validate.py` — self-contained skill/plugin validator; the
  AGENTS.md validation step now has a real implementation.
- `scripts/run_evals.py` — eval-suite runner for Phase 4 (local + CI) with the
  canonical JSON eval format and `--min-pass-rate` gating.
- `scripts/detect_bands.py` — deterministic control-band detector reference
  implementation (rolling-30d mean/σ, Western Electric rules, drift rule),
  unit-tested; consumes `bands.yaml`.
- Templates: `assets/incident.md`, `assets/runbooks/rollback-deploy.md`,
  `assets/runbooks/README.md`, `assets/PULL_REQUEST_TEMPLATE.md`,
  `assets/evals.example.json`, `assets/evals-README.md`,
  `assets/workflow-graph.yaml` (project graph state), `assets/.gitignore`.
- Tests: `tests/test_gate.sh`, `tests/test_init.sh`,
  `tests/test_detect_bands.py`, `tests/test_run_evals.py`.
- `.github/workflows/self-check.yml` — CI for the repo itself (shell syntax,
  unit tests, integration tests, skill/plugin validation).
- `SECURITY.md`; `version` field in SKILL.md frontmatter; compliance matrix in
  SKILL.md mapping each hard rule to its enforcement layer.
- `scripts/scan_secrets.py` — deterministic, network-free secret/credential
  scanner (paths, staged diff, or PR diff) with high-signal provider patterns,
  inline suppression (`# nosecret`), a `.secretsignore` allowlist, optional
  entropy checks, and redacted output. Shipped with `assets/pre-commit.example`
  (git pre-commit hook) and `assets/secrets-ignore.example`; scaffolded by
  `init_workflow.py`; dogfooded in `.github/workflows/self-check.yml`.

### Security

- `assets/production-gate.sh` — fixed two release-gate bypasses. The
  read-only allowlist was evaluated first, as an unanchored substring over the
  whole command string, so a production deploy was allowed whenever the
  command contained a read-only word
  (`kubectl apply -f production.yaml && echo done`,
  `helm upgrade prod-app 2>&1 | grep -v skip`) or the substring `cat`
  (`production/catalogue.yaml`). The gate now splits the command into shell
  segments, judges each on its own, and anchors read-only tokens to command
  position; a production deploy in any segment is gated.
- `scripts/gate_ledger.py` + `assets/production-gate.sh` — ledger approvals are
  now gate-scoped: `verify` gained `--require-gate`, and the release hook
  requires a `release_authorization` record (override with `RELEASE_GATE`).
  Previously any approved record (e.g. an intent acceptance) authorized a
  production deploy. Regression coverage added to `tests/test_gate.sh` and
  `tests/test_gate_ledger.py`.

### Changed

- `assets/plan.md` — "Files that change" is now a bullet list of exact paths or
  globs so `check_plan_sync.py` can treat the plan as a machine-readable
  manifest.
- `assets/workflow-graph.yaml`, `assets/workflow-graph.example.yaml`, and
  `examples/expense-tracker/workflow-graph.yaml` — nodes now carry
  `done_status` for the workflow state runtime.
- `assets/production-gate.sh` — replaced naive `deploy`+`production` word
  matching with deploy-action patterns (`DEPLOY_PATTERNS`), production-context
  matching, and a read-only allowlist (`READ_ONLY_PATTERNS`). Previously the
  gate was both bypassable (`terraform apply -target=module.production`,
  `helm upgrade prod-app`) and over-blocking (`cat docs/production-deploy.md`).
  Added `RELEASE_APPROVAL_EXPIRY` (ISO-8601 or epoch, fail-closed) and
  jq-independent JSON parsing.
- `scripts/init_workflow.py` — scaffolds `workflow-graph.yaml`, `.gitignore`,
  and `evals/README.md`; adds `--dry-run`, `--git`, and `--name` validation;
  fixed the codex-mode existence check (CLAUDE.md vs AGENTS.md).
- `assets/agent-evals.yml.example` — now calls `run_evals.py`; previously it
  referenced a `check.sh` that the repo never shipped.
- `README.md`, `AGENTS.md` — validation instructions point at real artifacts;
  the loop table now has a single source of truth (SKILL.md).

## [0.1.0] — 2026-08-21

Initial release: skill + plugin bundle implementing the AI-native SDLC loop
(Plan → Design → Build → Test → Deploy → Maintain) with versioned artifacts
and human approval gates.
