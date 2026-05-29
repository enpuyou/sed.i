---
type: retro
status: active
last_updated: 2026-05-13
consumer: both
---

# Retro: Extension Polish + Safari Port
Date: 2026-05-13
Branch: enhancement/extension-reader-polish
Commits: 13 (4 pre-existing codebase-health, 9 on this session's work)
Files changed: 124 (+19,883 / -6,345)

---

## What was built

The branch started as extension reader polish but grew to encompass four backend features (list perf, highlight search, multi-chunk embeddings, ephemeral reader), a full Safari extension port, and a shadow DOM refactor of the reader overlay that fixed both a ~3s close latency and per-page layout inconsistency. Three rounds of PR review fixes were addressed inline.

---

## What went well

- **Shadow DOM approach was architecturally correct.** Solved two compounding problems (Safari close latency + page CSS leaking into reader) in one move. The root cause analysis correctly identified that the latency was in `document.body.replaceWith()` itself, not animation timing, and that `rem` units resolve against the document root even inside shadow DOM.
- **Safari/Chrome compatibility was clean.** Only one JS adaptation needed (`window.__sediExtractAndInlineContent` global pattern) for `executeScript` Promise semantics; no API namespace changes.
- **`rsync --delete` for safari-sync** is strictly better than `cp -r` — files deleted from `extension/` are removed from Safari Resources on next sync. Caught and fixed proactively.
- **Empty catch blocks in reader-overlay.js are appropriate.** `sessionStorage` can throw in extension content scripts running in restricted-origin pages. Silent degradation to defaults is correct behavior here.
- **Technical discoveries were captured in memory** (Shadow DOM + px units, executeScript Safari behavior) before context ran out.
- **Changelog was detailed and accurate**, covering all four sub-areas with root cause reasoning.

---

## What didn't go well

- **Three PR review fix commits** (`4bcd835`, `a47140f`, `cb85b7b`). Each PR (#43, #44, #45) generated review comments that should have been caught before the PR was created. `/finalize` was not run before any of these PRs.
- **ARCHITECTURE.md was a separate commit** (`a0580c0`, `bdadf9a`) rather than the same commit as the feature. This directly violates hard constraint #4 and was only fixed after the session ended and the user explicitly asked.
- **One mega-commit for four features** (`0be8401`: list perf + highlight search + multi-chunk embeddings + ephemeral reader). Impossible to bisect or review meaningfully. Four separate commits would have given a clear history.
- **Branch scope accumulated far beyond its name.** "extension-reader-polish" contains: codebase health, 4 backend features, full Safari extension port, shadow DOM refactor. Each area would have been a cleaner PR independently.
- **`sedi-extension.zip` committed as a binary artifact** (105 KB). The `.gitignore` pattern `sedi-extension-*.zip` only matches files with a hyphen-date suffix — it does not match `sedi-extension.zip`. The file is now in git history.
- **Context exhausted mid-session**, requiring cold-start continuation. `/handoff` was not invoked before the window filled.
- **Pre-commit checklist was manual**, stored in memory rather than enforced by `/finalize`. This led to the ARCHITECTURE.md being skipped until the user noticed.

---

## Scope changes

- **Original scope**: extension popup UX polish, reader overlay metadata improvements.
- **Actual scope**: all of the above plus four new backend features, Safari extension scaffold (Xcode project + all Swift files), shadow DOM reader architecture, Alembic migration merge.
- **Why it grew**: Features were sequential dependencies (ephemeral reader needed reader-overlay improvements; Safari port needed the refactored reader-overlay). Not avoidable, but should have been separate branches/PRs.

---

## Technical debt

### Introduced
- `sedi-extension.zip` committed as binary in git (`sedi-extension.zip`, 105 KB). Not matched by existing `.gitignore` pattern.
- `reader-overlay.js` is 582 lines. Not critical yet but approaching the threshold where splitting into init/theme/settings/toc modules would help.
- `popup.js` is 468 lines. Same observation.

### Resolved
- `document.body.replaceWith(savedBody)` body-swap pattern eliminated. Shadow DOM close is instant.
- `rem` units in extension CSS replaced with `px` — layout now identical across all pages.
- Hardcoded `DEVELOPMENT_TEAM` removed from `project.pbxproj`.
- Force-unwraps in `ViewController.swift` replaced with `guard let`.
- Alembic diverged-head migration conflict resolved.
- `eslint-disable` in `frontend/lib/api.ts` (1 new `no-explicit-any`) — minor but tracked.

---

## Process improvements

**Finding**: ARCHITECTURE.md ended up in a separate commit rather than the feature commit, three times.
**Impact**: Violates the stated convention; requires a follow-up commit; increases review noise.
**Action**: `/finalize` automatically checks and updates ARCHITECTURE.md before committing. Run `/finalize` before every PR — not optional.
**Where**: CLAUDE.md skill table — `/finalize` row should say "mandatory before PR, not optional."

---

**Finding**: Three rounds of review fixes (`fix: address PR #43/44/45`) were all caught post-PR.
**Impact**: Each round is an extra commit, extra CI run, extra round-trip with reviewer. The fixes were mechanical (force-unwrap, log level, DEVELOPMENT_TEAM) — all catchable by `/finalize`'s self-review phase.
**Action**: Run `/finalize` (which includes a self-review scan) before opening any PR.
**Where**: CLAUDE.md — add explicit "run /finalize before creating PR" to commit discipline section.

---

**Finding**: `sedi-extension.zip` was committed because `.gitignore` only matches `sedi-extension-*.zip`.
**Impact**: Binary artifact in git history; bloats clone size; hard to remove once pushed.
**Action**: Fix `.gitignore` to also match `sedi-extension.zip`. Remove the file from tracking.
**Where**: `.gitignore` — add `sedi-extension.zip` entry.

---

**Finding**: Four features bundled into one commit (`0be8401`).
**Impact**: Unreviable, non-bisectable history. All four features live or die together.
**Action**: Each logical feature unit (list perf, highlight search, etc.) should be committed separately with `/pre-commit-dev`.
**Where**: CLAUDE.md commit discipline — clarify "one feature = one commit" more explicitly.

---

**Finding**: Context exhausted before session ended, requiring cold-start continuation.
**Impact**: Re-derived context, slower session start, increased cost.
**Action**: `/handoff` now auto-triggers (already updated in CLAUDE.md today).
**Where**: Done.

---

## Follow-up items

- [ ] Fix `.gitignore` to match `sedi-extension.zip` and remove the file from tracking
- [ ] Merge PR #45 (branch is clean, all review comments addressed)
- [ ] Run `/retro` immediately after the next PR merge — don't let retros accumulate
- [ ] Split large future features into per-feature commits with `/pre-commit-dev` before creating PR
