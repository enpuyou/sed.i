# Knowledge Connections

**Status:** Live
**Last updated:** 2026-05-14

sed.i can now tell you not just which articles are similar — but which specific ideas connect them, how those ideas cluster across your reading history, and what you're reading about overall.

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

## 4. Highlight Connections (tag-grouped)

### What it does

When you highlight text in an article, sed.i finds highlights in your other articles that discuss the same ideas. The connections panel now:

- Shows **which ideas connect** each article pair (tag pills)
- Collapses long lists: shows 2 pairs, then "+ N more connections" to expand
- Removes the old arbitrary 5-per-article cap — all genuine connections surface

### Where to find it

Open any article in the reader → press `c` (desktop only, ≥1280px wide) → the Connections panel opens on the right.

### How to test it

1. Open an article where you have highlights
2. Press `c` to open the Connections panel
3. Each connected article shows:
   - The article title
   - Tag pills for the ideas both articles share (if any)
   - Your highlight vs. their highlight, side by side
4. If a connected article has more than 2 highlight pairs, a "+ N more connections" button appears — click it to expand
5. Clicking a tag pill records the interaction

### Tips

- The more highlights you have across your library, the richer the connections
- Connections are based on embedding similarity — they find passages that discuss the same concepts even if they use different words
- Tag pills only appear when both articles have semantic tags. Older articles without tags will still show connections but won't have tag labels.

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

Use this to verify all four surfaces are working end-to-end:

- [ ] Save a new article and confirm it gets 4–6 specific semantic tags within 60 seconds
- [ ] Open "Find Related" on that article and confirm tag pills appear on at least one result
- [ ] Visit `/themes` and confirm at least one reading cluster appears (requires ≥10 tagged articles)
- [ ] Open an article with highlights, press `c`, and confirm the Connections panel shows tag pills where articles share tags
- [ ] Open a draft, write 50+ words on a topic you have articles about, and confirm "Relevant reading" appears after the autosave

---

## Enabling features

| Feature | Flag | Default |
|---------|------|---------|
| Reading Themes link in dashboard | `NEXT_PUBLIC_SHOW_READING_THEMES` | `false` |
| Relevant Reading in drafts | `NEXT_PUBLIC_SHOW_DRAFT_READS` | `false` |

Semantic tags, Find Related shared tags, and Highlight Connections are always on — no flag required.
