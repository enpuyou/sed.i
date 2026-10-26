---
layout: layouts/chapter.njk
title: What sed.i is, and how to use this guide
part: 0
order: 1
anatomy: custom
summary: The product in plain terms, the path a reader takes through it, and how to study the rest of this guide.
verified: 2026-10-25
permalink: /orientation/what-is-sedi/
---

## In one minute

sed.i is a read-it-later app with a writing desk attached. You save a link; the backend downloads the page, cleans it into an article, and indexes it. You read it in a quiet reader, highlight passages, and search across everything you have saved by keyword, by meaning, or both. Highlights are compared with each other, so a passage in one article can point to a related passage in another. Articles can be grouped into lists, and each list can hold one draft: a markdown document you write beside the sources. An AI assistant such as Claude can use the same library through an MCP server, and a browser extension saves pages from Chrome and Safari.

The app is a single-reader product built by one person. This guide exists so that person (and anyone they explain it to) can describe how every part works, why it was built that way, and where it is weak.

## What the product does, in the order a reader meets it

Each step below names the route or code that implements it, so you can find it later. The routes are in `frontend/app/`; the API handlers are in `content-queue-backend/app/api/`.

### 1. Save a link

On the dashboard (`/dashboard`) you paste a URL into the add form. The API (`POST /content` in `content-queue-backend/app/api/content.py`) records the link as a `ContentItem` with status `pending` and answers immediately. A worker then fetches the page, extracts the article, and fills in title, author, thumbnail, text and reading time. The item moves from `pending` to `processing` to `completed`, or to `failed`. The same flow handles PDFs. How this works is the subject of [Saving a URL](/ingestion/saving-a-url/) and [Extracting an article](/ingestion/extracting-an-article/).

A second way in is the browser extension, which extracts the article inside your browser, where you are already logged in to paywalled sites, and sends the cleaned HTML along with the URL. See [The browser extension](/ingestion/browser-extension/).

The list of items you have saved and not yet read is the **queue**. It is a filtered view of your **library**, which is everything you have saved. Neither is a separate table.

### 2. Read

Clicking an item opens `/content/[id]`. The reader shows the article in a clean column with a progress bar, a table of contents, and your last scroll position. Reader settings (typeface, theme, an optional bionic-reading mode that bolds the start of each word) live in the browser. The reader is two components: `frontend/components/Reader.tsx` (the page chrome and side panels) and `frontend/components/ReaderArticle.tsx` (the article body). The frontend is covered in [The frontend](/platform/frontend/).

### 3. Highlight

Select text in the reader and a toolbar offers a color and an optional note. A highlight is stored with its character offsets so it can be drawn again on the next visit. Highlights are the signal that you cared about a passage, and several later features (connections, memory, research weighting) use them.

### 4. Search across everything

The search bar in the navbar sends the query to `GET /search/semantic`. A small rule-based classifier decides how to answer: a short word goes to keyword search, a question goes to meaning-based search, a longer phrase goes to both, and a query such as `author:Smith` becomes a database filter. The modal that opens with Ctrl+K or Cmd+K asks for every method at once and merges the results. Highlights are searched too. The whole subject is [Hybrid search](/retrieval/hybrid-search/); its foundations are [RAG from first principles](/retrieval/rag-from-first-principles/) and [Chunking and embedding](/retrieval/chunking-and-embedding/).

### 5. See connections between highlights

In the reader, the `c` key opens a panel listing highlights from other articles that say something similar to the one you picked, with the article each came from. Similarity is computed on demand from highlight embeddings; nothing about a connection is stored. The panel is `frontend/components/ConnectionsPanel.tsx` and the backend route is `GET /search/connections/{highlight_id}`. Details: [Entity graph and connections](/retrieval/entity-graph-and-connections/).

### 6. Write in a list, beside a draft

A **list** is a named collection of articles (`/lists`, `/lists/[id]`). Opening a list shows the writing workspace: the source article on the left and a markdown editor on the right. The editor edits the list's **draft**. The database allows one draft per list per user (the constraint `uq_drafts_list_user` in `content-queue-backend/app/models/draft.py`). The component is `frontend/components/WritingWorkspace.tsx`.

### 7. Ask an agent through MCP

MCP, the Model Context Protocol, is a standard way for an AI assistant to call tools in another program. sed.i runs an MCP server inside the API process. Claude Desktop or claude.ai signs in with OAuth and can then call 16 tools (counted in `content-queue-backend/app/mcp/http_server.py`): list and read lists, search, find similar articles, read highlights and drafts, save a URL, create a list, add an item to a list, summarize a list, update a draft, answer a question about the library with generated SQL, synthesize a topic, and assist with a draft. See [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/). A separate multi-agent research pipeline produces a written brief from a question; see [The research agent](/llm/research-agent/).

### 8. Smaller surfaces

- A **public profile** (`/[username]`) can show your queue read-only.
- **Crates** (`/crates`) is a vinyl-record collection fed by Discogs. It is unrelated to reading and is behind a feature flag in `frontend/lib/flags.ts` that defaults to on.
- **Reading themes** (`/themes`) groups tags into clusters; its flag defaults to off.
- `/guide` is the in-app user guide. It is separate from this study guide.

## Who it is for

The product is written for one kind of user: someone who saves many long articles, wants to keep what they read, and writes from it. The data model supports several accounts (the `users` table, per-user rows everywhere, public profiles), but the design target is a single heavy reader. The development database used for the measurements in this guide holds 5 users, 71 active items and 77 highlights (measured with read-only counts on the local database on 2026-10-25). Treat all "at scale" statements in the guide with that in mind: the system has been run and measured at the size of one person's library, and reasoned about beyond that.

## How this guide is organized

The guide has seven parts. Each answers one question.

| Part | Question it answers | Chapters |
|---|---|---|
| 0 Orientation | What is this, how do the pieces fit, what do the words mean? | this chapter, [The system on one page](/orientation/system-on-one-page/), [Glossary](/orientation/glossary/) |
| 1 Ingestion | How does a link become a clean, stored article? | [Saving a URL](/ingestion/saving-a-url/), [Extracting an article](/ingestion/extracting-an-article/), [PDFs](/ingestion/pdfs/), [The browser extension](/ingestion/browser-extension/) |
| 2 Retrieval | How does sed.i find things? | [RAG from first principles](/retrieval/rag-from-first-principles/), [Chunking and embedding](/retrieval/chunking-and-embedding/), [Hybrid search](/retrieval/hybrid-search/), [Entity graph and connections](/retrieval/entity-graph-and-connections/), [Measuring retrieval](/retrieval/measuring-retrieval/) |
| 3 The LLM layer | Where are language models used, and how are the calls controlled? | [The LLM gateway](/llm/gateway/), [Tagging and article analysis](/llm/tagging-and-analysis/), [Memory consolidation](/llm/memory-consolidation/), [The research agent](/llm/research-agent/), [MCP and text-to-SQL](/llm/mcp-and-text-to-sql/) |
| 4 Platform | What does everything stand on? | [Data model and Postgres](/platform/data-model/), [Background work](/platform/background-work/), [Auth and security](/platform/auth-and-security/), [The frontend](/platform/frontend/), [Deploy and operations](/platform/deploy-and-operations/) |
| 5 Reliability and scale | Where is it fragile, and what was done? | [Distributed-systems review](/reliability/distributed-systems/), [Memory: investigation and fixes](/reliability/memory/), [Gaps and roadmap](/reliability/gaps-and-roadmap/) |
| 6 Talking about it | How do I explain it to someone else? | [The 10-minute system-design walkthrough](/talking/system-design-walkthrough/), [Decision log](/talking/decision-log/) |

Parts 1 to 4 can be read in any order after Part 0. Part 6 assumes the rest.

## How to study from it

Every chapter after Part 0 has the same ten sections in the same order, so you always know where to look.

| Section | What to do with it |
|---|---|
| In one minute | Read first. If you can repeat it in your own words, you know the outline. |
| The problem | Learn the question the chapter answers and why the obvious simple design is not enough. |
| Mental model | Read the analogy and the diagram; learn the defined terms. |
| Walkthrough | Follow one real input step by step. Do this slowly; it is the main part. |
| How it is built here | Use as a map: which file does what. |
| Design choices | Learn the alternatives. This is what interviewers and reviewers ask about. |
| How it fails | Real incidents and what they changed. |
| What is weak or missing | Honest gaps. A good answer to "what would you change?" starts here. |
| Say it in 60 seconds | Read it aloud, then say it without looking. |
| Check yourself | Answer each question before opening the answer. |

A study routine that works with this layout:

1. Read **In one minute** and say it back without looking.
2. Run the **walkthrough in your head**: close the page and narrate the example from input to output, naming the file at each step. Open the chapter to check the steps you skipped.
3. Answer the **Check yourself** questions out loud before opening them. A question you cannot answer marks the section to reread.
4. Finish with **Say it in 60 seconds**.

Two labels appear throughout. A number marked **measured** was produced by running the code or a query for this guide, and the chapter says how. A number marked **illustrative** was made up to show a shape and must not be quoted as a fact about the system. Where a chapter says **inferred from the code**, it read the code but did not run the path. When a document in the repository disagrees with the code, the chapter follows the code.

## The vocabulary you will meet first

These twelve terms appear in nearly every chapter. The [Glossary](/orientation/glossary/) defines all the others.

| Term | Plain meaning |
|---|---|
| ContentItem | One saved link and everything derived from it: metadata, text, embedding, reading state. |
| Queue / Library | Queue is unread items; library is all items. Both are views, not tables. |
| Ingestion | The path from a submitted URL to a finished `ContentItem`. |
| Worker | A separate process that does slow jobs (fetching, embedding, LLM calls) so the API can answer quickly. |
| Highlight | A saved text selection inside an article. |
| List and Draft | A named collection of articles, and the one markdown document attached to it. |
| Embedding | A list of 1,536 numbers that stands for the meaning of a text. |
| Chunk | One passage of an article, embedded on its own. |
| Hybrid search | Keyword search and meaning search, chosen or merged per query. |
| MCP | The protocol an AI assistant uses to call sed.i's tools. |
| Soft delete | Marking a row deleted with a timestamp instead of removing it. |
| Celery | The Python library that runs background jobs from a queue. |

{% say %}
sed.i is a read-it-later app. I paste a link, a background worker downloads and cleans the article, and I read it in a quiet reader where I can highlight. I can search everything by keyword, by meaning, or both, and the app can show me highlights from other articles that say something similar. Articles can be grouped into lists, and each list has one markdown draft I write beside the sources. The same library is open to Claude through an MCP server, and a browser extension saves pages from Chrome and Safari. It is built for one heavy reader; the data model supports several accounts, but I have only run and measured it at the size of one person's library.
{% endsay %}

## Check yourself

{% qa "What is the difference between the queue and the library, and where is each stored?" %}
The library is every `ContentItem` a user has saved; the queue is the unread, non-archived subset. Neither has its own table. Both are filters over `content_items`, so "save to queue" means creating a `ContentItem`.
{% endqa %}

{% qa "A user saves a link and the API answers in a fraction of a second, but the title is empty. Is something broken?" %}
No. The API creates the item with status `pending` and returns; a worker fills in the title and text afterwards. Doing the download inside the request would make every save as slow as the slowest website and would fail the request when the site did.
{% endqa %}

{% qa "You read a chapter and cannot answer its Check yourself questions. What should you do?" %}
Reread the walkthrough, not the summary. The questions test understanding of the example (why, what happens if), so the section that explains the step you missed is the one to redo. Then say the 60-second explanation aloud.
{% endqa %}

{% qa "A chapter says a connection between two highlights exists. Where is that connection stored?" %}
Nowhere. Connections are computed when the panel is opened, by comparing the highlight's embedding with the embeddings of highlights in other articles. Only the highlight and its embedding are stored.
{% endqa %}

{% qa "Why does this guide label numbers as measured or illustrative?" %}
Because a made-up number that sounds like a measurement is the easiest way to say something false about a system. Measured numbers say how and where they were obtained, so you can reproduce them or judge them; illustrative numbers only show the shape of an idea.
{% endqa %}
