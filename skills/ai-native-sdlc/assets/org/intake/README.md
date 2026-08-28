# Intake — where demand enters the org

Every demand lands here as a markdown record, no matter the channel. The
product engineering agent reads these, consolidates them into feature tickets,
and drafts `intent.md` for PM review.

## Channels

- `github/` — issues pulled by `scripts/sync_issues.py pull`
  (one file per issue, named `<repo>-<number>.md`)
- `forms/` — user feedback from app forms (one file per submission)
- `email/` — feedback and complaints from email (one file per message)

## Record format (all channels)

```markdown
---
source: github|form|email
record_id: unique id (issue number, form id, message id)
received_at: ISO-8601 UTC
author: who reported it (when known)
priority: low|normal|high
---

## Summary
One or two sentences: what the user cannot do today.

## Details
Anything that makes the demand concrete: steps, screenshots, expectations.

## Status
new | triaged | ticket_filed | intent_drafted | done
```

## GitHub config

`config.json` next to this README drives `sync_issues.py pull` (repo list,
label filter, state file). See `scripts/sync_issues.py --help`.
