# Plan: sed.i study guide (GitHub Pages)

Goal: one place to learn how sed.i works inside out, without reading code, well enough to explain
any component technically. Source of truth stays the code and `ARCHITECTURE.md`; the guide is the
teaching layer on top of them.

Acceptance: a reader with little background in a topic can open its chapter, follow the worked
example, and afterwards (a) explain the component in 60 seconds, (b) name the main design choices
and their alternatives, (c) say what is weak or missing, (d) find the code. Done when every chapter
below exists, builds, passes the checks at the end of this file, and is live on GitHub Pages.

## How the research shaped it

| Finding | Source | Consequence |
|---|---|---|
| Explanation pages should give context and reasons, admit opinion, connect to neighbouring ideas, stay bounded by a "why" question, and keep step-by-step instructions out | Diátaxis, diataxis.fr/explanation | Every chapter is organized around a "why" question; how-to and reference material is linked to (ARCHITECTURE.md, ADRs), not copied |
| Worked examples teach novices faster than abstract rules (worked-example effect); retrieval practice beats rereading | learning-science literature (Sweller; Roediger) | Each chapter traces one real input end to end, then ends with self-test questions that hide the answer |
| Concrete before abstract; analogy first, then the precise term; one new idea at a time | common technical-writing practice | Fixed chapter anatomy (below) |
| Body text 15-25 px, line spacing 120-145%, 45-90 characters per line, one professional face, little bold/italic | Butterick, Practical Typography | Single reading column ~68 characters, serif body, restrained emphasis |
| Margin notes keep the main column short without losing detail | Tufte CSS | Asides render in the margin on wide screens, inline on narrow |
| Static search with no server: build an index from the HTML | Pagefind | Search works on GitHub Pages |
| Eleventy: no framework, own templates and CSS; deploy with a GitHub Actions workflow and `pathPrefix` | 11ty docs | Chosen generator |

## Chapter anatomy (every chapter, same order)

1. **In one minute**: five or six sentences a newcomer can repeat.
2. **The problem**: what breaks without this component; the "why" question for the chapter.
3. **Mental model**: an analogy, then the precise terms, then a small diagram.
4. **Walkthrough**: one real input followed step by step, with the real code and the real output (captured by running the code, or marked "illustrative" when it cannot be).
5. **How it is built here**: file map and the key code excerpts, trimmed, with a line on what each does.
6. **Design choices**: what was chosen, what the alternatives were, why not them (link the ADR when one exists).
7. **How it fails**: failure modes, incidents, what was learned.
8. **What is weak or missing**: honest gaps, ranked.
9. **Say it in 60 seconds**: a spoken-style explanation; plus the vocabulary to use precisely.
10. **Check yourself**: 4-6 questions, answers hidden in `<details>`.

Voice (from the project's technical-writing rules): state facts directly; no filler, no persuasion,
no dramatic framing; define a term at first use; numbers carry units and a source; say "measured"
or "illustrative" for every number; no emoji.

## Structure

| Part | Chapter | Covers | Main sources |
|---|---|---|---|
| 0 Orientation | 0.1 What sed.i is, how to use this guide | product, vocabulary, page anatomy | CONTEXT.md |
| | 0.2 The system on one page | topology, request lifecycle, repo map | ARCHITECTURE.md §3, system-wiki |
| | 0.3 Glossary | terms with links to chapters | CONTEXT.md |
| 1 Ingestion | 1.1 Saving a URL | normalize, dedupe, status machine, 201 then async | `services/content.py`, `api/content.py` |
| | 1.2 Extracting an article | bounded fetch, trafilatura, metadata, images, limited-extraction heuristics | `tasks/extraction.py`, `core/fetch.py` |
| | 1.3 PDFs | layout model in a subprocess, stages, confidence | `pdf-extraction.md`, ADR-0007 |
| | 1.4 The browser extension | extraction in the page, overlay, auth, Safari | `extension-architecture.md` |
| 2 Retrieval | 2.1 RAG from first principles | embeddings, similarity, pgvector, HNSW | `models/`, `core/hybrid_search.py` |
| | 2.2 Chunking and embedding | structure-aware chunks, contextual prefix, batching | `tasks/chunk_embeddings.py` |
| | 2.3 Hybrid search | router, four lanes, RRF, caching | `core/search_router.py`, `hybrid_search.py` |
| | 2.4 Entity graph and connections | GraphRAG-lite, highlight connections | `core/entity_graph.py`, `entity-graph-search.md` |
| | 2.5 Measuring retrieval | eval harness, metrics, the reranker decision | `evals/`, `sota-gap-action-plan.md` |
| 3 LLM layer | 3.1 The LLM gateway | providers, retries, budgets, tracing | `core/llm_client.py`, ADR-0003 |
| | 3.2 Tagging and article analysis | structured output, prompts | `tasks/tagging.py`, `article_analysis.py` |
| | 3.3 Memory consolidation | profile, eval-validated prompts | `tasks/memory.py` |
| | 3.4 The research agent | planner, chord, budgets, recovery | `tasks/research.py` |
| | 3.5 MCP and text-to-SQL | OAuth with PKCE, tools, SQL guard | `app/mcp/`, ADR-0006 |
| 4 Platform | 4.1 Data model and Postgres | tables, soft delete, indexes, migrations | `models/`, `alembic/` |
| | 4.2 Background work | Celery, Redis, beat, sweeps, locks | `redis-and-celery.md` |
| | 4.3 Auth and security | JWT, cookies, CSRF, refresh rotation, rate limits, isolation | `api/auth.py`, `middleware/` |
| | 4.4 The frontend | app structure, reader, highlights, lists, state, errors | `frontend/` |
| | 4.5 Deploy and operations | Railway, Vercel, Cloudflare, CI, observability, incidents | `railway.json`, workflows, ADR-0002 |
| 5 Reliability | 5.1 Distributed-systems review | delivery, consistency, scale, gaps | `distributed-systems-guide.md` |
| | 5.2 Memory: investigation and fixes | method, findings, fixes, results, production baseline | `docs/audits/2026-10-24-*` |
| | 5.3 Gaps and roadmap | ranked, consolidated | audits, guides |
| 6 Talking about it | 6.1 The 10-minute system-design walkthrough | pitch, numbers, follow-up questions | all of the above |
| | 6.2 Decision log | ADRs in plain language | `docs/decisions/` |

## Visual design

Minimal, bookish, no decoration that does not carry meaning.

- One reading column of about 68 characters; left navigation that stays out of the way; margin notes on wide screens.
- Warm paper background, near-black ink, one muted brown accent (the sed.i sepia theme), hairline rules. No cards, shadows, gradients, icons, badges or emoji.
- Body in a system serif stack (Charter, Iowan Old Style, Georgia); code in a system monospace stack; no web fonts to download.
- Code blocks and diagrams are figures with a caption; diagrams are plain text or hand-written SVG in the same ink color.
- Light and dark follow the system setting.
- Self-test answers use native `<details>`; search is Pagefind.

## Hosting

`guide/` (Eleventy project) builds to `guide/_site`; `.github/workflows/guide.yml` builds on push to
`main` when `guide/**` changes and deploys with the official Pages actions. URL:
`https://enpuyou.github.io/sed.i/`. The repository is public, so the site is public: chapters on
gaps describe the problem and the fix, not step-by-step exploitation.

## Process and checks

1. Scaffold the site and write one chapter (2.2) by hand to fix voice and layout; screenshot and review.
2. Write the remaining chapters in parallel, each against its source files, running the code to capture real examples.
3. Integrate: navigation, cross-links, glossary links, search.
4. Checks: build has no warnings; every internal link resolves; every `path/to/file` mentioned exists in the repo; every code excerpt is a verbatim excerpt of the file (script-checked); every chapter has all ten sections; the page renders at phone width without horizontal scroll; Pagefind finds a known term.
5. Enable Pages (Settings, Pages, Source: GitHub Actions), merge, confirm the live URL.
