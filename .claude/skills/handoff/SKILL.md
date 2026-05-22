---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
---

Write a handoff document so a fresh agent can continue the work without searching the codebase.

**Where to save:** `docs/handoffs/YYYY-MM-DD-<2-3-word-slug>.md` using today's date
(e.g. `docs/handoffs/2026-05-15-safari-esc-fix.md`). Create `docs/handoffs/` if it doesn't
exist. Read the file with the Read tool before writing (Write requires a prior Read — if new, read it to get the "file not found" error, then Write).

**What to include:**

```
# Handoff — <topic>

## Branch
<git branch name>

## What was done this session
- bullet list of completed work

## In progress / what comes next
- current state of any open work
- immediate next steps

## Key files changed
- path/to/file — what changed and why

## Open questions / blockers
- anything the next session needs to resolve

## Skills for next session
- /skill-name — why
```

Do not duplicate content in other artifacts (plans, commits, diffs). Reference them by path.

If the user passed arguments, treat them as the next session's focus and tailor the doc.
