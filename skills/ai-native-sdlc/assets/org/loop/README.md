# Bounded agent loop

One turn is one dispatch to one role. `next` is deterministic; `advance` is the
only state mutator. Defaults: `max_turns: 6`, `max_no_progress: 2`,
`max_review_rounds: 2`.

```bash
python3 scripts/org_loop.py init --work-item intent/intent.md
python3 scripts/org_loop.py next --json
python3 scripts/org_loop.py advance --role senior-developer --action implement \
  --result progress --artifact src/app.py --evidence .loop/tests.log
python3 scripts/org_loop.py check
```

Stop reasons: `solved`, `gate_required`, `failed`, `stalled`, `exhausted`.
`solved` requires passing acceptance commands, reviewer approval, and no
blocking security finding. Every completed turn requires evidence. The loop
never records an approval or crosses a human gate.

The optional local driver runs one bounded iteration at a time:

```bash
python3 scripts/org_loop.py run --agent-cmd "<agent command>" --max-turns 6
```

The supplied cap cannot exceed the configured `max_turns` budget.
