# Reviews — evidence-backed peer review records

One markdown file per review, named `org/reviews/<artifact>-<id>.md`:
`intent-<n>.md`, `spec-<sha8>.md`, `plan-<sha8>.md`, `code-<pr-or-sha>.md`.

```markdown
---
artifact: spec
reviewed_at: ISO-8601 UTC
reviewer: reviewer-1
verdict: approved|changes
round: 1
---

## Findings (by severity)
- [HIGH] file/line — what is wrong and why it matters.
- [MED] ...
- [NIT] ... (max 5)

## Evidence
What was run/read to reach the verdict: commands, outputs, files.
```

Reviews feed the workflow: `approved` artifacts advance to the next gate;
`changes` sends the artifact back to the writer. Unresolved disagreement after
two rounds escalates to the CTO, then the CEO.
