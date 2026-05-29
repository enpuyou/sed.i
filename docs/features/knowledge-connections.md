---
type: feature
status: active
last_updated: 2026-05-28
consumer: human
---

# Knowledge Connections

**Status:** Live
**Last updated:** 2026-05-15 (connections panel UI overhaul)

sed.i can now tell you not just which articles are similar — but which specific ideas connect them, how those ideas cluster across your reading history, and what you're reading about overall.

---

## Prerequisites (read this first)

Before testing any of these features, make sure the following are in place:

1. **Worker is running** — `make worker`. Without it, no embeddings or tags are generated.
2. **Articles have semantic tags** — tags are generated automatically after an article is saved and embedded (~30s). Old articles may need the backfill task (see §1 Notes).
3. **Highlight Connections need highlight embeddings** — generated when you create a highlight, but only if the worker was running. Run the batch trigger if your highlights are old (see §4 Manual triggers).
4. **Reading Themes need ≥10 tagged articles** — and the clustering task must have run (see §3 Manual trigger).

---

## Overview

Four surfaces make up the knowledge connections system:

| Surface | Where | What it does |
|---------|-------|--------------|
| Semantic tags | Every article | Automatically extracts specific ideas from article content |
| Find Related | Bottom of any article | Shows related articles with the ideas they share with the one you're reading |
| Reading Themes | `/themes` | Groups your library into reading clusters based on shared ideas |
| Highlight Connections | `c` key in reader | Shows which highlights in other articles connect to yours, labeled by shared idea |
| Relevant Reading | Draft workspace | Surfaces articles from your library that are relevant to what you're writing |

---

## 1. Semantic Tags

### What it does

When you save an article, sed.i reads the content and extracts 4–6 specific tags that capture what the article actually discusses. These are different from broad categories:

| Before | After |
|--------|-------|
| AI | mesa-optimization |
| Politics | ranked choice voting |
| Finance | compound interest mechanics |
| Food | maillard reaction |

Tags are generated at two levels:
- **Domain** (1–2 tags): The field the article belongs to — specific enough to be useful, broad enough to group related articles
- **Concepts** (3–4 tags): The precise ideas the article actually discusses

### Where to see it

Open any article → tags appear below the title in the reader.

### How to test it

1. Save a new article on any topic
2. Wait ~30 seconds for processing to complete
3. Open the article — you should see 4–6 specific tags
4. Compare them to the article's actual content — they should name specific ideas, not broad fields

### Notes

- Articles saved before this update may have old coarse tags ("AI", "Technology"). These get re-tagged automatically when the backfill runs, or you can remove them manually.
- You can remove tags you don't want — the system won't re-add them.

---

## 2. Find Related (with shared ideas)

### What it does

The "Find Related" section at the bottom of each article now shows **which ideas connect** it to each result — not just a similarity percentage.

Each related article card now displays shared concept tags between the two articles:

```
The Alignment Problem
83% similar

[mesa-optimization]  [AI safety]
```

### Where to find it

Open any article → scroll to the bottom → click "Find Related".

### How to test it

1. Open an article that has semantic tags (saved recently, or recently re-tagged)
2. Scroll to the bottom and click "Find Related"
3. Each result card should show tag pills below the title
4. The tags shown are ideas that appear in **both** the article you're reading and the result
5. Click a result — the system records the interaction so we can improve recommendations over time

### What "no shared tags" means

If a result card shows no tag pills, the two articles are similar at the embedding level (similar vocabulary and writing style) but don't share specific labeled concepts. This is still a valid connection — just a broader one.

---

## 3. Reading Themes

### What it does

Reading Themes groups your entire library into clusters based on shared ideas. Instead of browsing article by article, you can see what you're actually reading about.

Each theme shows:
- A cluster name (the most common idea within the group)
- How many articles it contains
- The related concept tags that define the cluster
- Up to 3 article titles as a preview

### Where to find it

Navigate to `/themes` — or enable the "Reading themes" link in the dashboard by setting `NEXT_PUBLIC_SHOW_READING_THEMES=true`.

### How to test it

1. Go to `/themes`
2. If you see an empty state: you need at least 10 articles with semantic tags. Save more articles and wait for them to process, then themes will be generated on the next weekly run (or trigger manually)
3. If you see clusters: check that the articles within each theme genuinely share a common subject
4. Click any article title in a cluster to open it

### Triggering themes manually (dev)

```python
from app.tasks.clustering import cluster_user_tags
result = cluster_user_tags("<your-user-id>")
print(result)
```

### What good themes look like

- **"distributed systems"** — 12 articles on consensus protocols, replication, CAP theorem
- **"personal finance"** — 8 articles on investing, budgeting, compound interest
- **"documentary filmmaking"** — 5 articles on narrative structure, cinematography

Themes that are too narrow (1–2 articles) are automatically excluded. Themes will improve as more articles get semantic tags.

---

## 4. Highlight Connections (two-mode panel)

### What it does

The Connections panel has two modes:

**Mode 2 — all highlights** (`c` key, default view)
- Shows every highlight in the article that has at least one connection, ordered by their position in the article text
- Each highlight card shows its text; connected articles are listed below it, sorted by connection score (strongest first)
- Click the highlight card header → drills into Mode 1 for that highlight
- Click any article row → drills into Mode 1 AND scrolls the panel to that specific connection card

**Mode 1 — single highlight** (click a highlight or blue dot)
- Opens scoped to one highlight — reader auto-scrolls to the source highlight in the article
- Each connected article card shows: title, author + domain, connection score, shared concept tags, and matched passages
- Click any matched passage → reader navigates to that article and jumps to that specific passage
- "open article →" footer → opens that article (no specific passage)
- A one-sentence insight explains the specific idea linking your highlight to that article (AI-generated, appears a few seconds after the card loads)
- `← all highlights` returns to Mode 2

**Blue connection dot**
- A small blue dot appears at the top-left corner of any highlight that has connections
- Click the dot to open Mode 1 for that highlight (same as clicking the highlight text)

### Where to find it

Open any article in the reader (desktop only, ≥1280px wide):
- Click any highlighted text or its blue dot → Connections panel opens in **Mode 1**
- Press `c` → opens in **Mode 2** (overview of all highlights)

### Keyboard shortcuts

| Key | Behavior |
|-----|----------|
| `c` (panel closed) | Open in Mode 2 |
| `c` (in Mode 1) | Switch to Mode 2 |
| `c` (in Mode 2) | Close panel |

### Manual triggers (dev)

If your highlights exist but connections aren't showing, the highlights may not have embeddings yet. Run:

```python
from app.core.database import SessionLocal
from app.models.user import User
db = SessionLocal()
user = db.query(User).filter(User.email == "your@email.com").first()
user_id = str(user.id)
db.close()

# Generate embeddings for all highlights that are missing them
from app.tasks.embedding import generate_highlight_embeddings_batch
result = generate_highlight_embeddings_batch.delay(user_id)
print(result.get())
```

Or, if you just want to check without Celery:

```python
from app.tasks.embedding import process_all_missing_embeddings
process_all_missing_embeddings.delay()
```

### How to test it

1. Make sure your highlights have embeddings (see Prerequisites and Manual triggers above)
2. Open an article where you have highlights — you don't need shared tags, just a highlight with an embedding and a cosine similarity ≥0.3 against any other highlight in your library
3. **Blue dot test**: highlighted passages with connections show a small blue dot at the top-left corner of the highlight — verify it appears
4. **Mode 1 via click**: click a highlighted passage → panel opens in Mode 1; reader scrolls to that highlight automatically
5. Verify Mode 1 card: title, author/domain, score (e.g. `0.82`), shared tag pills (● tag), and matched passage text
6. Wait 2–3 seconds → insight sentence loads below the tags ("generating insight…" appears first)
7. Click a matched passage → navigates to that article, jumps to the specific passage
8. Click "open article →" → opens that article without jumping to a specific passage
9. Click `← all highlights` → returns to Mode 2
10. **Mode 2 test**: press `c` → Mode 2 shows all connected highlights in document order
11. Article rows under each highlight are sorted by score (highest first)
12. Click an article row → enters Mode 1 AND scrolls panel to that connection card
13. Press `c` in Mode 2 → panel closes

### Tips

- Connections appear for any highlight with cosine similarity ≥0.3 against another highlight — shared tags are no longer required
- If no connections appear, the highlight may not have an embedding yet (create the highlight while the worker is running)
- Insights are generated once and cached — fast on revisit

---

## 5. Relevant Reading in Drafts

### What it does

While writing a draft in a List, sed.i surfaces articles from your library that are relevant to what you're writing. These appear below the editor and update automatically each time your draft is saved.

Each result shows:
- Article title (click to open)
- The semantic tags for that article

### Where to find it

Open any List → click "Write" → the Relevant Reading panel appears below the editor.

Requires: `NEXT_PUBLIC_SHOW_DRAFT_READS=true` in your environment (already enabled locally).

### How to test it

1. Open a List and click "Write" to enter the draft workspace
2. Write at least 50 words about a topic you have articles on (e.g., "machine learning", "personal finance", "distributed systems")
3. Pause typing — the draft autosaves after ~1.5 seconds
4. After the save, "Relevant reading" appears below the editor with up to 5 articles from your library
5. Click any article title to open it in a new tab while keeping the draft open

### What triggers a refresh

Relevant reads update after each autosave. Autosave fires 1.5 seconds after you stop typing. So: type → pause → save → results update.

### When you get no results

- Draft is under 50 words — add more content
- No articles in your library match the topic — save some articles first
- The draft topic is very niche — try broader phrasing

---

## Testing checklist

Use this to verify all five surfaces are working end-to-end:

**Setup (do this first):**
- [ ] `make worker` is running
- [ ] You have ≥2 articles with overlapping semantic tags
- [ ] You have highlights in at least one of those articles
- [ ] Run the highlight embedding trigger if highlights are old (see §4 Manual triggers)

**Semantic tags:**
- [ ] Save a new article and confirm it gets 4–6 specific tags within 60 seconds (open the article → tags below the title)

**Find Related:**
- [ ] Open "Find Related" at the bottom of a tagged article and confirm tag pills appear on at least one result card

**Reading Themes:**
- [ ] Visit `/themes` and confirm at least one reading cluster appears (requires ≥10 tagged articles + clustering task run)

**Highlight Connections (two-mode panel):**
- [ ] Open an article with highlights that have embeddings
- [ ] Blue dot appears at top-left corner of connected highlights
- [ ] Click a highlighted passage → Mode 1 opens; reader scrolls to that highlight automatically
- [ ] Mode 1 card: title, author/domain, score, tag pills, matched passage text all visible
- [ ] Wait 2–3s → insight sentence appears below the tags
- [ ] Click matched passage → navigates to connected article at that specific passage
- [ ] Press `c` → Mode 2 shows highlights in document order; article rows sorted by score
- [ ] Click article row in Mode 2 → Mode 1 opens AND panel scrolls to that connection card
- [ ] Click `← all highlights` → returns to Mode 2
- [ ] Press `c` in Mode 2 → panel closes

**Relevant Reading:**
- [ ] Open a List, click "Write", type 50+ words on a topic you have articles about, pause → "Relevant reading" appears after autosave

---

## Enabling features

| Feature | Flag | Default |
|---------|------|---------|
| Reading Themes link in dashboard | `NEXT_PUBLIC_SHOW_READING_THEMES` | `false` |
| Relevant Reading in drafts | `NEXT_PUBLIC_SHOW_DRAFT_READS` | `false` |

Semantic tags, Find Related shared tags, and Highlight Connections are always on — no flag required.
