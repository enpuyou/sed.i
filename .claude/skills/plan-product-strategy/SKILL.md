````skill
---
name: plan-product-strategy
description: "Market-aware product strategy plan artifact: deep current-state audit, competitor patterns, gap model, phased roadmap, KPIs."
user-invokable: true
argument-hint: "<strategy topic, e.g. ingestion quality or mixed-media rendering>"
---

# /plan-product-strategy — Product Strategy Planning Artifact

Create a structured strategy artifact for product direction decisions where the
goal is not just implementation planning, but market/context comparison and a
realistic roadmap grounded in current system constraints.

## Project conventions

!`cat .claude/skills/_shared/conventions.md`

**Invoke**: `/plan-product-strategy <topic>`

Examples:
- `/plan-product-strategy ingestion reliability and rendering quality`
- `/plan-product-strategy mixed-media queue UX`
- `/plan-product-strategy knowledge artifact outputs`

---

## Phase 0 · Prior art and baseline

Before strategy design, gather baseline context:

1. Read existing plan docs in `docs/plans/` related to the topic.
2. Read relevant sections in `ARCHITECTURE.md`.
3. Read implementation files that actually power the current behavior.
4. Identify existing constraints (infra, model choices, UX conventions).

Output: a concise "Current state" section based on real code, not assumptions.

---

## Phase 1 · Problem framing with user-facing scenarios

Define:

1. **Core question(s)** being answered.
2. **What users are trying to do** (real workflows, not abstractions).
3. **Failure modes today** (what breaks or feels weak).
4. **Desired end-state** in practical terms.

For each key concept, include:
- What it means technically.
- What it means for a real user workflow.
- How current sed.i maps to it.
- What benefit sed.i gets if improved.

---

## Phase 2 · Competitive and ecosystem patterns

Research similar products/tools and extract actionable patterns:

1. What others appear to do well.
2. Why those choices likely work.
3. Which patterns fit sed.i's architecture.
4. Which patterns should NOT be copied (and why).

Important:
- Do not produce hand-wavy competitor summaries.
- Convert each observed pattern into concrete implications for sed.i.

---

## Phase 3 · Gap model and differentiation

Build an explicit gap model:

1. Capability matrix: current vs target.
2. Confidence level per area (high/medium/low) with reasons.
3. Missing instrumentation (where we cannot truthfully quantify today).

Then define differentiation:

- Where sed.i is already stronger.
- Where sed.i is behind.
- Where sed.i can become uniquely better with focused investment.

---

## Phase 4 · Roadmap design (realistic, phased, measurable)

Create a phased roadmap with practical sequencing:

### Phase template
```
### Phase N (date range): <name>
Goal:
Build:
User impact:
Dependencies:
Risks:
Success criteria:
```

Roadmap requirements:
- Foundation before optimization.
- Minimal irreversible decisions early.
- Each phase should unlock visible user value.
- Include instrumentation early (before bold coverage claims).

---

## Phase 5 · Metrics, risks, and operating model

Include:

1. **Metrics** (quality, reliability, utility, engagement).
2. **Risk register** with mitigation.
3. **Decision gates** (what must be true before advancing phases).
4. **Open questions** needing user/stakeholder input.

---

## Phase 6 · Write artifact to docs/plans

Create:
- `docs/plans/<topic>-plan.md`

Recommended structure:
```markdown
# <Plan title>
Status: Draft v1
Date: YYYY-MM-DD
Scope: ...

## 1) Why this document exists
## 2) Current state (sed.i today)
## 3) Deep problem framing (with real user scenarios)
## 4) External patterns and lessons
## 5) Gap model + differentiation
## 6) Phased roadmap
## 7) Metrics and decision gates
## 8) Risks and mitigations
## 9) Positioning / strategic outcome
```

---

## Quality bar checklist

Before finalizing the artifact, ensure it is:

- Evidence-based from current code/docs.
- Explicit about uncertainty (no invented numbers).
- Specific about what to build and in what order.
- Useful for both product and engineering decisions.
- Clear enough to hand directly into implementation planning.

---

## What this skill does NOT do

- It does not write implementation code.
- It does not run external business interviews.
- It does not estimate exact timelines beyond rough phase windows.
- It does not replace `/plan` for feature-level engineering execution.

---

## Cross-references

- Use `/plan` after this artifact to convert a chosen phase into concrete implementation work.
- Use `/pre-commit-dev` during execution checkpoints.
- Use `/finalize` before merge and `/retro` after merge.

````
