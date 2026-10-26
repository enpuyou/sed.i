# sed.i study guide

Source of the study guide site. Built with Eleventy, searched with Pagefind, deployed to GitHub Pages
by `.github/workflows/guide.yml` when files under `guide/` change on `main`.

```bash
cd guide
npm ci
npm start                  # dev server with live reload (no search index)
npm run build              # build to _site and index it for search
npm run check              # source and link checks (run after build)
npm run build:pages        # build with the /sed.i/ path prefix, as deployed
```

- Chapters are Markdown files in `src/<part>/`. Read `STYLE.md` before writing one; `src/retrieval/chunking-and-embedding.md` is the model.
- The checker verifies required sections, that every `path.ext` in backticks exists, and that code excerpts marked `<!--src path#a-b-->` are verbatim from the repository, so a code change that breaks an excerpt shows up here.
- Plan and acceptance criteria: `docs/plans/study-guide.md`.
- Where documentation disagreed with the code while writing: `discrepancies/` (kept local and not committed, because it can describe unpatched issues).

Site: https://enpuyou.github.io/sed.i/ (public, like the repository).
