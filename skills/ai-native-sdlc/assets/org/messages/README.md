# Agent messages

One markdown file per message, named `<from>-<sequence>.md`. Messages are
committed with the artifact they concern. Use `scripts/org_message.py`:

```bash
python3 scripts/org_message.py send --from senior-developer --to architect \
  --kind question --subject "Cache boundary" --artifact spec.md \
  --body-file /tmp/question.md
python3 scripts/org_message.py inbox --agent architect
python3 scripts/org_message.py reply --id senior-developer-0001 \
  --from architect --body-file /tmp/answer.md
python3 scripts/org_message.py check
```

An open `requires_response` message blocks the recipient's turn. A `finding`
with `blocking: true` must have severity `high` or `critical` and cannot be
closed without evidence.
