---
type: design
status: active
last_updated: YYYY-MM-DD
consumer: human
---

# Design: <Feature Name>

Written for someone who wants complete mental ownership of this feature — not an API
reference or user guide, but the document a tech lead would write to let you explain
the system confidently, make sound adjacent decisions, and understand the tradeoffs.

---

## Problem being solved

What user pain or capability gap does this address? What was the experience before?
Write from the user's perspective, not the engineer's.

## User experience

Walk through the feature from the user's perspective. What do they do, see, feel?
No code. No internal names. Plain language only.

## Architecture overview

High-level: what components exist, how they talk to each other, where data flows.
Enough to understand the system without reading the code. Use prose or ASCII diagrams.

```
User → [component] → [API] → [service] → [DB]
                                ↑
                         [async task]
```

## Key design decisions

For each significant decision:

### Decision: <name>
**Why this approach**: ...
**What was traded away**: ...
**ADR reference**: `docs/decisions/NNNN-<name>.md` (if one exists)

## Technical deep dive

The concepts worth internalizing. Data models, algorithms, protocols, patterns.
Explain the *why* of the technical approach at a level that builds intuition.
This is the section that makes you a better architect.

### Data model
What does the data look like? Key fields, relationships, invariants.

### Algorithm / approach
How does it work mechanically? What are the key steps?

### Performance characteristics
Where are the limits? What degrades under load?

## What this does NOT do

Explicit scope boundaries. Deliberate non-goals and why they were excluded.

## Limits and known failure modes

What breaks under what conditions? Known edge cases that are accepted but not handled.
Performance cliffs. Known bugs or shortcuts.

## Extension points

If someone wanted to extend this in the future, where would they plug in?
What would change, what would stay the same?

## Glossary

New terms or concepts this feature introduces to the system.
