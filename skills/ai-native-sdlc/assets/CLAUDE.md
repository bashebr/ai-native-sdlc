# CLAUDE.md — repository memory

Keep this file under one page. Add a rule when the same mistake happens twice.

> In Codex projects, the same content lives in AGENTS.md; the role is identical.

## Commands

- Build: `make build` (must finish with "Build succeeded")
- Test: `make test` (all green; never skip or delete a failing test)
- Lint: `make lint` (zero warnings)
- Itest: `make itest` (integration, needs docker)
- Secrets: `python3 scripts/scan_secrets.py --staged` (must report 0 findings)

## Verifying your work

Run build, test, and lint before reporting any task complete, and paste the output.
If a test fails, fix the code, not the test.

## Conventions

<Language/framework conventions, formatting, naming.>

## Architecture

<One-paragraph mental model: main modules and data flow.>

## Things the agent gets wrong

<Each mistake, its fix, and how to check for it.>

## Hooks

<Deterministic hooks and what they enforce.>
