# Writing guide for chapter authors

Read this first, then read the finished example chapter, `src/retrieval/chunking-and-embedding.md`
(rendered: it is the model for voice, depth and layout). The plan is in `docs/plans/study-guide.md`.

## Who the reader is

The repository owner, who built this system with help and now wants to understand and explain every
part of it technically, including parts they know little about. Assume they can read code slowly
but have not seen this topic before. After a chapter they should be able to explain the component
in 60 seconds, name its main design choices and alternatives, say what is weak, and find the code.

## Chapter anatomy

Every chapter uses these `##` headings, in this order. The checker enforces the titles.

1. `## In one minute`: 5-7 sentences a newcomer can repeat. No jargon that is not defined in the same paragraph.
2. `## The problem`: open with the "why" question the chapter answers as one bold sentence (no label such as "The question this chapter answers"), then what breaks without the component. Name the obvious simpler design and why it falls short.
3. `## Mental model`: an analogy first, then the precise terms, then a small text diagram. Define each term at first use; a small table of terms is fine.
4. `## Walkthrough: <the example>` (the title may continue after "Walkthrough"): ONE concrete input followed step by step with real code and real output. Use `###` steps. This is the heart of the chapter: it should be possible to follow without opening the repo.
5. `## How it is built here`: file map (table), then the key code as verbatim excerpts with one line on what each does.
6. `## Design choices`: a table of choice, alternatives, why this one. Link the ADR in `docs/decisions/` when one exists. Give an opinion where the evidence supports one.
7. `## How it fails`: failure modes, incidents, what was changed. Use real incidents from `docs/changelog/`, `docs/retros/`, `docs/audits/` and git history.
8. `## What is weak or missing`: honest gaps, ranked by how likely they are to matter. This is a public site: describe the problem and the fix, not step-by-step exploitation.
9. A `{% say %}` block: the 60-second spoken explanation, first person, plain words.
10. `## Check yourself`: 4-6 `{% qa "question" %}` blocks with answers. Questions test understanding (why, what happens if, what would you change), not recall of constants.

Extra `##` sections are allowed between 5 and 8 when the topic needs one (for example "What the scores look like"). Orientation and glossary chapters may set `anatomy: custom` in front matter to skip the section check.

Length: 2,000 to 5,000 words. Longer than that means two chapters.

## Voice

- State facts directly. No filler, no persuasion, no dramatic framing, no over-explaining, no emoji, no exclamation marks.
- Short paragraphs; one new idea each. Define a term when it first appears.
- Concrete before abstract: the example, then the rule.
- Every number carries a unit and a status: "measured" (say how and where) or "illustrative" (made up to show the shape). Never present an estimate as a measurement.
- Say what you verified and how. When you could not verify something, say "inferred from the code" or "not verified".
- Prefer plain words to jargon; when jargon is the correct term, use it and define it once.
- Talk about the system as it is. Do not write history ("we used to") unless the history explains a design choice.

## Evidence rules (these are what make the guide trustworthy)

1. **The code is the source of truth.** `ARCHITECTURE.md` and the other docs can be stale. Read the code. When a document disagrees with the code, write what the code does and append a line to `guide/discrepancies/<your-chapter-numbers>.md` (file, claim, what the code does).
2. **Run the code for examples.** Use the backend virtualenv: `cd content-queue-backend && PYTHONPATH=. .venv/bin/python script.py` with dummy environment variables (`DATABASE_URL`, `REDIS_URL=redis://localhost:6379/15`, `SECRET_KEY` of 40+ characters, `OPENAI_API_KEY=dummy`). Capture real output and paste it. Mark what a script printed as captured.
3. **No outside calls.** Do not call OpenAI, Anthropic, Cohere, Resend, Railway or any paid or external service, and do not touch production. A fake OpenAI server is at `content-queue-backend/scripts/memprof/fake_openai.py` if you need an endpoint. When a step needs a real model response, show the request and mark the response "illustrative".
4. **Local data is read-only and aggregate.** The development Postgres is `docker exec -i content_queue_postgres psql -U postgres -d content_queue` (read-only queries only). Report counts, sizes, distributions and timings. Do not paste article text, titles, URLs, user emails or ids from it into the guide. Use your own sample inputs for anything textual. Never modify that database; use a scratch database if you need to write.
5. **Verbatim excerpts.** Put an HTML comment `<!--src path/from/repo/root#START-END-->` on the line before a fenced block; the checker verifies the block is exactly those lines of that file (indentation may be removed). Use `...` lines to skip code and omit the line range to let the checker search the file. Keep excerpts short (under 40 lines) and explain what to look at.
6. **File paths.** Any `path.ext` in backticks must exist in the repository. Spell paths from the repo root or relative to `content-queue-backend/app/` or `frontend/`.
7. **Do not edit application code, tests or existing docs.** Only create files under `guide/src/<part>/` for your chapters, plus your discrepancy file. Exception: none.

## Safety (the site is public)

No secrets or tokens, no production hostnames or project ids, no email addresses, no absolute local paths (`/Users/...`), no real user data. Describe security gaps as "the problem and the fix"; do not write exploit steps. Do not include `.env` contents.

## Markdown and shortcodes

Front matter (all required; the checker enforces them):

```yaml
---
layout: layouts/chapter.njk
title: Chunking and embedding
part: 2
order: 2
summary: One sentence under the title.
verified: 2026-10-25
permalink: /retrieval/chunking-and-embedding/
---
```

`verified` is the date (YYYY-MM-DD) on which you checked the chapter against the code.

Shortcodes (Nunjucks paired tags):

| Tag | Use |
|---|---|
| `{% raw %}{% fig "Caption" %} ... {% endfig %}{% endraw %}` | wraps a fenced code block or diagram and adds a caption |
| `{% raw %}{% aside %} ... {% endaside %}{% endraw %}` | a side note; sits in the margin on wide screens |
| `{% raw %}{% say %} ... {% endsay %}{% endraw %}` | the 60-second explanation |
| `{% raw %}{% qa "Question?" %} answer {% endqa %}{% endraw %}` | a self-test question with a hidden answer |

Rules:

- Use `##` and `###` headings only. Do not write a `#` title; the layout renders it.
- Links to other chapters are root-relative: `[Hybrid search](/retrieval/hybrid-search/)` (permalink table below). The build adds the site prefix. Links to a chapter that another author has not written yet are fine; the final build checks them.
- Link to code on GitHub only when needed, as plain `path` in backticks (no URL).
- Code fences always have a language: `python`, `sql`, `typescript`, `bash`, `json`, `yaml`, `html`, `css`, or `text` for diagrams and captured output.
- Any `{{` or `{%` inside a fenced block must be wrapped in `{% raw %}` ... `{% endraw %}`.
- Keep lines in captured output and diagrams under 78 characters so they do not scroll sideways.
- Tables for comparisons, file maps and failure modes. Do not use HTML other than comments.
- Diagrams are plain text inside a `text` fence in a `{% raw %}{% fig %}{% endraw %}`, narrower than 70 characters.

## Permalinks (use these exactly)

| Chapter | Permalink |
|---|---|
| 0.1 What sed.i is | `/orientation/what-is-sedi/` |
| 0.2 The system on one page | `/orientation/system-on-one-page/` |
| 0.3 Glossary | `/orientation/glossary/` |
| 1.1 Saving a URL | `/ingestion/saving-a-url/` |
| 1.2 Extracting an article | `/ingestion/extracting-an-article/` |
| 1.3 PDFs | `/ingestion/pdfs/` |
| 1.4 The browser extension | `/ingestion/browser-extension/` |
| 2.1 RAG from first principles | `/retrieval/rag-from-first-principles/` |
| 2.2 Chunking and embedding | `/retrieval/chunking-and-embedding/` |
| 2.3 Hybrid search | `/retrieval/hybrid-search/` |
| 2.4 Entity graph and connections | `/retrieval/entity-graph-and-connections/` |
| 2.5 Measuring retrieval | `/retrieval/measuring-retrieval/` |
| 3.1 The LLM gateway | `/llm/gateway/` |
| 3.2 Tagging and article analysis | `/llm/tagging-and-analysis/` |
| 3.3 Memory consolidation | `/llm/memory-consolidation/` |
| 3.4 The research agent | `/llm/research-agent/` |
| 3.5 MCP and text-to-SQL | `/llm/mcp-and-text-to-sql/` |
| 4.1 Data model and Postgres | `/platform/data-model/` |
| 4.2 Background work | `/platform/background-work/` |
| 4.3 Auth and security | `/platform/auth-and-security/` |
| 4.4 The frontend | `/platform/frontend/` |
| 4.5 Deploy and operations | `/platform/deploy-and-operations/` |
| 5.1 Distributed-systems review | `/reliability/distributed-systems/` |
| 5.2 Memory: investigation and fixes | `/reliability/memory/` |
| 5.3 Gaps and roadmap | `/reliability/gaps-and-roadmap/` |
| 6.1 The 10-minute system-design walkthrough | `/talking/system-design-walkthrough/` |
| 6.2 Decision log | `/talking/decision-log/` |

Files live in `guide/src/<part-slug>/<chapter-slug>.md` where the part slug is the first path segment of the permalink.

## Checking your work

```bash
cd guide
npx --no-install @11ty/eleventy --output=/tmp/guide_<yourname>    # builds all chapters present
GUIDE_SITE=/tmp/guide_<yourname> node scripts/check.mjs --source-only
```

`--source-only` skips link checks (other chapters may not exist yet). Fix every reported problem. Then
re-read the chapter once as the reader: is every term defined before it is used, does the walkthrough
stand on its own, is every number labelled measured or illustrative, and would the 60-second
explanation make sense to someone who has not read the chapter?
