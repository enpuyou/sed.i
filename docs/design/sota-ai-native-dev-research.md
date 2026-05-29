---
type: design
status: active
last_updated: 2026-05-28
consumer: both
---

# SOTA AI-Native Development Workflow Research

**Date**: 2026-05-28
**Branch context**: enhancement/sota-stack

---

## 1. Karpathy's Coding Principles

Karpathy coined "vibe coding" in February 2025 but quickly distinguished it from what he calls **agentic engineering** — the professional-grade discipline. His guidelines were formalized as a CLAUDE.md-compatible skill (`karpathy-guidelines`) that became a top GitHub trending item.

### The Four Principles

**Principle 1 — Think Before Coding**
Tagline: "Don't assume. Don't hide confusion. Surface tradeoffs."
Rules:
- State assumptions explicitly; ask rather than guess when uncertain
- Present multiple interpretations rather than choosing silently
- Push back when a simpler approach exists
- Stop and name confusion rather than proceeding into it

Classic failure mode: user says "export the users to a file" → LLM silently picks CSV, arbitrary path, arbitrary fields. With this principle applied: ask three targeted questions (format? which users? which fields?) before writing a line.

**Principle 2 — Simplicity First**
Tagline: "Minimum code that solves the problem. Nothing speculative."
Rules:
- No features beyond what was asked
- No abstractions for single-use code
- No "flexibility" or "configurability" that wasn't requested
- No error handling for impossible scenarios
- If 200 lines could be 50, simplify

**Principle 3 — Surgical Changes**
Tagline: "Touch only what you must. Clean up only your own mess."
Rules:
- Don't improve unrelated code, comments, or formatting
- Avoid refactoring working code
- Match existing style
- Mention pre-existing dead code but don't delete it
- Remove only imports/variables made unused by YOUR changes

**Principle 4 — Goal-Driven Execution**
Tagline: "Define success criteria. Loop until verified."
Rules:
- Transform tasks into verifiable goals with tests
- State a brief plan with steps and verification checks
- Don't stop until verification passes

### Karpathy's Broader Agentic Engineering Framework

From his Sequoia Ascent 2026 talk:
- "Traditional software automates what you can specify. LLMs and RL automate what you can **verify**."
- LLMs spike in capability where tasks are verifiable AND emphasized in training — outside that distribution, they become unreliable
- "You're still responsible for your software just as before. The agents don't absorb your liability. They're interns — remarkable interns, but interns."
- "You can outsource your thinking, but you can't outsource your understanding."
- Human judgment remains essential for: domain-specific correctness (the Stripe/Google email anti-pattern), security boundaries, architectural shape, aesthetics, and "what question matters"
- Work with agents to "design a detailed spec, maybe basically the docs" before implementation — direct strategy, delegate execution
- Key hiring test for the agentic era: "build a substantial project with agents, deploy it, make it secure, then have adversarial agents try to break it"

**Caveat on the Four Principles**: "These guidelines bias toward caution over speed. For trivial tasks, use judgment." They are cost-benefit optimized for non-trivial work.

---

## 2. Anthropic's Official CLAUDE.md Recommendations

Source: `code.claude.com/docs/en/best-practices`

### What to Include vs. Exclude

| Include | Exclude |
|---------|---------|
| Bash commands Claude can't guess | Anything Claude can figure out by reading code |
| Code style rules that differ from defaults | Standard language conventions Claude already knows |
| Testing instructions and preferred test runners | Detailed API documentation (link instead) |
| Repository etiquette (branch naming, PR conventions) | Information that changes frequently |
| Architectural decisions specific to your project | Long explanations or tutorials |
| Developer environment quirks (required env vars) | File-by-file descriptions of the codebase |
| Common gotchas or non-obvious behaviors | Self-evident practices like "write clean code" |

### Key Formatting Rules

- No required format, but keep it **short and human-readable**
- Test for every line: "Would removing this cause Claude to make mistakes?" If not, cut it
- Use `IMPORTANT` or `YOU MUST` to signal priority — this improves adherence because it matches how the model weights signals
- Write as direct commands, not suggestions: "never use inline mocks — use src/test/factories/*" not "we generally try to avoid inline mocks"
- Check it into git; treat it like code — review when things go wrong, prune regularly, test changes by observing behavior shifts

### Diagnostic Signs Your CLAUDE.md is Broken

- **Claude keeps doing something you told it not to**: file is too long, rule is getting lost
- **Claude asks questions already answered in the file**: phrasing is ambiguous
- **Bloated CLAUDE.md**: Claude ignores half of it because important rules disappear in noise

### Multi-file Structure

CLAUDE.md supports `@path/to/import` syntax for modular loading:

```markdown
See @README.md for project overview.

# Additional Instructions
- Git workflow: @docs/git-instructions.md
- Personal overrides: @~/.claude/my-project-instructions.md
```

Loading hierarchy: `~/.claude/CLAUDE.md` → parent directories → project root → `.claude/CLAUDE.md` → child directories (loaded on demand when Claude reads files in those dirs).

### Skills vs. CLAUDE.md

CLAUDE.md is for content that applies **every session**. Domain knowledge or workflows relevant only sometimes belong in Skills (`.claude/skills/SKILL.md`) — Claude loads them on demand without bloating every conversation.

### Compaction Preservation

You can embed compaction instructions in CLAUDE.md itself:
`"When compacting, always preserve the full list of modified files and any test commands"`

---

## 3. How Anthropic's Internal Teams Actually Use It

Source: Anthropic's published blog post "How Anthropic Teams Use Claude Code"

**Data Science / Infrastructure team:**
- Feed entire codebases to Claude Code for onboarding new team members
- CLAUDE.md identifies relevant files and explains data pipeline dependencies
- Replaced traditional data catalog tools with Claude-assisted navigation
- End-of-session practice: ask Claude to summarize work and suggest improvements → feed that back into CLAUDE.md (continuous improvement loop)

**Security Engineering:**
- TDD workflow: pseudocode → Claude implementation → periodic human review
- Feed stack traces and docs to Claude for production incident diagnosis
- Reported: **3x faster issue resolution**
- Heaviest users of custom slash commands (50% of all slash command implementations in Anthropic's monorepo)

**Product Engineering:**
- Ask Claude to identify which files need examination before touching code
- "First stop" for all programming tasks
- Auto-accept mode for peripheral features (prototype to ~80%, then review)
- Synchronous mode (hand-hold every step) for core business logic

**Cross-team pattern:**
- Running 5-10 Claude instances simultaneously with parallel workstreams
- Shared memory files that accumulate with every commit
- Model choice: Opus 4.5 with thinking enabled for everything (Boris Cherny's team) — superior comprehension leads to fewer mistakes and less rework

---

## 4. Research on Instruction Effectiveness

### The "Lost in the Middle" Problem

Original paper: arXiv:2307.03172 (Stanford/UC Berkeley/Samaya AI, 2023). Key findings:
- LLMs show a **U-shaped performance curve** for long contexts
- Performance is highest when relevant information is at the **beginning or end** of context
- Performance degrades by **more than 30%** when critical information shifts to the middle
- Effect worsens as context windows grow larger (more tokens to distribute attention across)
- Recent finding (2025): as inputs *approach* the context limit, primacy bias drops and the lost-in-the-middle effect disappears — different behavior at the ceiling

### Instruction Capacity

- Frontier LLMs can reliably follow approximately **150-200 instructions** total
- Claude Code's system prompt already consumes approximately 50 of those slots
- Practical ceiling for CLAUDE.md: keep under 300 lines before signal degrades into noise
- HumanLayer's own CLAUDE.md: under 60 lines

### Implications for CLAUDE.md Design

- Lead with the most critical rules (primacy effect)
- End with anything that must not be forgotten (recency effect)
- Middle of the file is the deadzone — use it for reference content, not hard constraints
- Fewer, stronger rules outperform many weak ones
- Hooks (deterministic) > CLAUDE.md instructions (advisory) for must-always behaviors

---

## 5. Common Patterns in Well-Maintained CLAUDE.md Files

Based on Anthropic docs, HumanLayer analysis, ArthurClune examples, and community repos:

### Universal Categories (appear in nearly every effective CLAUDE.md)

1. **How to verify work** — test commands, lint commands, build commands. "Run `npm test` after every change." This is the single highest-leverage line.
2. **Project-specific Bash commands** — things Claude cannot guess from reading code
3. **Non-default conventions** — style choices that differ from community defaults
4. **Environment gotchas** — required env vars, local setup quirks, known broken states

### Common Categories (appear in most)

5. **Architecture overview** — 3-5 sentences on system structure, not a file tree
6. **Workflow rules** — branch naming, commit discipline, PR requirements
7. **What to avoid** — specific anti-patterns the team has burned on (e.g., "never use inline mocks")
8. **File map** — a table linking "I'm working on X" → "look at Y file" for the 5-10 most common tasks

### Patterns to Avoid

- File-by-file directory listings (Claude reads the code; it doesn't need a manifest)
- Restating language conventions Claude already knows ("write clean, readable code")
- API documentation embedded in the file (link to it instead)
- Anything that changes frequently (Claude's context is stale before the session ends)
- Motivational language or tone guidance ("be thoughtful", "take your time")

### Progressive Disclosure Pattern (HumanLayer recommendation)

Keep root CLAUDE.md under 60 lines. Use pointers to dedicated docs:
```
docs/
  building_the_project.md
  running_tests.md
  code_conventions.md
```

CLAUDE.md then contains: "For build instructions see @docs/building_the_project.md"

This keeps every session's base context minimal while making depth available on demand.

### The "Linter Rule": Never Use CLAUDE.md for What a Linter Can Enforce

HumanLayer's principle: "Never send an LLM to do a linter's job." Code style rules that a linter can enforce deterministically should be in the linter config, not CLAUDE.md. CLAUDE.md is for judgment-laden constraints that no static tool can catch.

---

## Key Actionable Findings Summary

1. **Karpathy's four rules are the most concrete practitioner checklist available**: Think first, simplify, make surgical changes, verify completion. Drop them into CLAUDE.md as-is for any project using Claude Code.

2. **The primacy/recency effect is real and consequential**: Put hard constraints at the top and bottom of CLAUDE.md. The middle is the deadzone.

3. **Under 300 lines is the functional limit** before Claude starts losing signal. Under 100 lines is aspirational best practice. HumanLayer's real-world example: 60 lines.

4. **"IMPORTANT" and "YOU MUST" measurably improve adherence** — not cargo cult formatting, it's how the model weights priority signals.

5. **The highest-ROI single line in any CLAUDE.md**: a working verification command ("run `make test` after changes"). This closes the feedback loop that enables autonomous iteration.

6. **Hooks beat instructions for invariants**. If a rule must hold with zero exceptions, it belongs in a Stop hook or pre-commit hook, not CLAUDE.md.

7. **Anthropic's internal teams treat CLAUDE.md as living documentation**: updated at the end of every session based on what Claude asked about or got wrong.

8. **The AGENTS.md standard is converging across tools** (Claude Code, Cursor, Codex CLI, Gemini CLI each read their own file, but AGENTS.md is the Linux Foundation steward format with 60,000+ OSS projects). Writing for portability matters if you're using multiple tools.

---

## Sources

- [Anthropic: Best practices for Claude Code](https://code.claude.com/docs/en/best-practices)
- [Karpathy guidelines SKILL.md](https://github.com/multica-ai/andrej-karpathy-skills/blob/main/skills/karpathy-guidelines/SKILL.md)
- [Karpathy Sequoia Ascent 2026 talk notes](https://karpathy.bearblog.dev/sequoia-ascent-2026/)
- [The Four Principles — DeepWiki](https://deepwiki.com/forrestchang/andrej-karpathy-skills/3-the-four-principles)
- [How Anthropic teams use Claude Code](https://claude.com/blog/how-anthropic-teams-use-claude-code)
- [HumanLayer: Writing a good CLAUDE.md](https://www.humanlayer.dev/blog/writing-a-good-claude-md)
- [Lost in the Middle (arXiv:2307.03172)](https://arxiv.org/html/2601.03269)
- [DEV Community: The Lost in the Middle Problem](https://dev.to/thousand_miles_ai/the-lost-in-the-middle-problem-why-llms-ignore-the-middle-of-your-context-window-3al2)
- [CLAUDE.md, AGENTS.md & Copilot Instructions guide](https://www.deployhq.com/blog/ai-coding-config-files-guide)
- [ArthurClune/claude-md-examples](https://github.com/ArthurClune/claude-md-examples)
