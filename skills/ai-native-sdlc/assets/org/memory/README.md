# Org memory

`context.md` is the org's compact main context. `events.jsonl` is an
append-only log of decisions, facts, constraints, and questions. Every event
must cite a source (artifact, commit, or message id).

```bash
python3 scripts/org_memory.py add --kind decision --actor architect \
  --text "Use an append-only ledger" --source spec.md
python3 scripts/org_memory.py digest
python3 scripts/org_memory.py check
```

Keep `context.md` under 16 KB and 200 lines. Never store secrets or raw
transcripts. The memory keeper curates the `## Curated notes` section; `digest`
owns only the machine block.
