---
layout: layouts/chapter.njk
title: Extracting an article
part: 1
order: 2
summary: How a worker downloads a page, reads its metadata, cuts the article out of the page chrome, turns it into clean HTML, and decides whether the result is complete.
verified: 2026-10-26
permalink: /ingestion/extracting-an-article/
---

## In one minute

After the API returns 201, a worker downloads the page twice. The first pass reads only the metadata in the page head (title, description, author, date, cover image) and stores it so the library card can show something quickly. The second pass runs a library called trafilatura, which finds the main article in the page and drops navigation and footers. A converter in sed.i turns trafilatura's output into simple HTML, putting images back where they belong.

The worker stores that HTML, counts the words, estimates reading time at 200 words per minute, and runs a set of heuristics that guess whether a paywall or a teaser cut the article short. If so, it sets a warning in `processing_error` and the frontend shows "Limited extraction". Then it hands the item to the embedding task.

## The problem

The question this chapter answers: **how does sed.i get from an arbitrary web page to clean, readable article HTML, and how does it know when it failed to?**

A web page is mostly not the article. It contains navigation, ads, cookie banners, related-story lists and footers, and its markup differs on every site. The obvious simple design is to store the page's HTML as it is. That fails because the reader would show the navigation and ads, the stored text would be dominated by boilerplate (which also pollutes search and embeddings), and styles and scripts from other sites would run inside the app.

The opposite simple design, plain text only, loses headings, links, lists and images, which are most of what makes an article readable.

So extraction has to do three jobs: find the article, keep its structure, and report honestly when what it found is only part of the article. The last job matters because the extractor cannot tell the difference between a short article and a paywalled preview from the text alone.

## Mental model

Think of a clipping service. A clerk opens the newspaper, cuts the article out with scissors (trafilatura), tapes the photos back next to the paragraphs they illustrated (the converter), and writes a note on the clipping if the page looked like a subscription teaser (the limited-extraction heuristics). The clerk never copies the photos, only the address where each photo can be found.

| Term | Meaning here |
|---|---|
| Open Graph (OG) tags | `<meta property="og:...">` tags in the page head that sites add so links preview well: title, description, image. |
| JSON-LD | A `<script type="application/ld+json">` block with structured data about the page (author, image, whether the article is free). |
| trafilatura | An open-source Python library that extracts the main text of a page. Here it is asked for XML output. |
| Hotlinking | Showing an image by pointing at the original site's URL instead of storing a copy. |
| Limited extraction | A completed item whose `processing_error` is set because the text is probably incomplete. |

{% fig "From page to stored article" %}
```text
 URL
  | fetch_bounded (cap 10 MB, skip binary types)
  v
 extract_metadata (phase 1)       status: processing
  |  OG / Twitter / JSON-LD -> title, description, author,
  |  date, thumbnail
  v
 extract_full_content (phase 2)   fetches the page again
  |  trafilatura -> XML (headings, paragraphs, <graphic>)
  |  xml_to_html -> clean HTML, images placed by context
  |  word_count, reading time, limited-extraction check
  v
 completed (processing_error set if limited)
  |
  v
 generate_embedding -> analyze_article + chunk embeddings
```
{% endfig %}

## Walkthrough: one sample article

The input is a 35-line page written for this guide. It has Open Graph tags, a JSON-LD block with a different author name, a navigation bar, a footer, and three body images: one lazy-loaded (a placeholder in `src`, the real address in `data-src`), one with a relative path, and one whose address contains the name "david".

{% fig "The sample page (shape; text shortened where marked)" %}
```html
<head>
  <title>How pour-over coffee works | Example Coffee Journal</title>
  <meta property="og:title" content="How pour-over coffee works">
  <meta property="og:description" content="A short guide to grind size, ...">
  <meta property="og:image" content="/media/cover.jpg">
  <meta name="twitter:title" content="Pour-over explained">
  <meta name="author" content="Mira Okafor">
  <meta property="article:published_time" content="2026-09-14T08:30:00Z">
  <script type="application/ld+json">
  {"@type": "Article", "author": {"name": "Someone Else"}, "image": "https://cdn.example.com/ld-cover.jpg"}
  </script>
</head>
<body>
  <nav><a href="/">Home</a> <a href="/subscribe">Subscribe</a> ...</nav>
  <article>
    <h1>How pour-over coffee works</h1>
    <p>Pour-over coffee is made by pouring hot water over ... </p>
    <p>The cup you get depends on how quickly ... </p>
    <img src="data:image/gif;base64,R0lGOD..." data-src="https://cdn.example.com/photos/kettle-pour.jpg" alt="..." width="800">
    <h2>Water temperature</h2>  <p>...</p> <p>...</p>
    <img src="/img/pour-bloom.jpg" alt="The coffee bed blooming after the first pour">
    <h2>Pour timing</h2>  <p>...</p> <p>...</p>
    <img src="https://cdn.example.com/photos/david-portrait.jpg" alt="The author tasting the cup" width="640">
    <p>Taste the result and change one variable at a time. ...</p>
  </article>
  <footer><p>Copyright Example Coffee Journal. ... Subscribe to our newsletter ...</p></footer>
</body>
```
{% endfig %}

The page was served to the real functions from a file; the page URL was `https://journal.example.com/posts/pour-over`. All outputs below were captured by running the code. No real site was contacted.

### Step 1: download, bounded

`extract_metadata` sets `processing_status = 'processing'`, then downloads with `fetch_bounded`. That function is the only place extraction downloads a user-supplied URL. It streams the response in 16 KB pieces so that a page's memory cost is limited by a cap, not by what the remote server sends. The rules are:

- HTML, and any type not listed below, is **truncated** at `FETCH_HTML_MAX_BYTES` (10 MB). The item still ingests; `extract_full_content` later adds a "Partial extraction" warning.
- A PDF is **rejected** above `FETCH_PDF_MAX_BYTES` (100 MB), both when the declared length is too large and when the stream passes the cap.
- Types that cannot hold an article (video, audio, image, font, archives) are **not downloaded**; the caller gets an empty body.
- A 60-second wall-clock limit (`FETCH_MAX_SECONDS`) applies while data keeps arriving.

{% fig "core/fetch.py: stop reading at the cap" %}
<!--src content-queue-backend/app/core/fetch.py#124-135-->
```python
if total + len(piece) > limit:
    if is_pdf:
        raise FetchRejectedError(
            f"PDF too large (over {limit // (1024 * 1024)} MB)"
        )
    # Keep only what fits so the cap is never exceeded in memory.
    piece = piece[: limit - total]
    truncated = True
pieces.append(piece)
total += len(piece)
if truncated:
    break
```
{% endfig %}

To see it work without waiting for 10 MB, the caps were set to 1 MB (HTML) and 2 MB (PDF) and a local test server returned five kinds of response (captured):

{% fig "fetch_bounded against a local server, caps scaled down (captured)" %}
```text
/small     status=200 bytes=38 truncated=False
/big       status=200 bytes=1048576 truncated=True
/video     status=200 bytes=0 truncated=False
/huge.pdf  FetchRejectedError: PDF too large (2 MB, limit 2 MB)
/gone      HTTPError: 404 Client Error: Not Found ...
```
{% endfig %}

The `/big` page was about 1.6 MB and was cut at exactly 1,048,576 bytes. The 5 MB video body was never read. The PDF was 3,000,005 bytes, and the message says "2 MB" because it divides by 1,048,576 and rounds down; the wording is slightly off but the rule is applied correctly.

Memory is the reason for the cap: during extraction the parsed page costs many times its size. The code comment says about 37 times; the later memory investigation measured 25 to 31 times in a standalone run. See [Memory: investigation and fixes](/reliability/memory/).

{% aside %}
The request carries a desktop-browser `User-Agent` and a `Referer` of a search engine. This improves the chance that a site serves the page, and it is also a choice to look like a browser. `fetch_bounded` does not check where the URL points (no private-address check); the chapter [Saving a URL](/ingestion/saving-a-url/) lists this as a gap.
{% endaside %}

### Step 2: phase 1, metadata

`_extract_page_metadata` reads the page with BeautifulSoup and applies a fixed priority for each field:

| Field | Sources, first non-empty wins |
|---|---|
| title | `og:title`, `twitter:title`, `<title>`, a title guessed from the URL |
| description | `og:description`, `twitter:description`, `<meta name="description">` |
| author | `article:author` meta, `<meta name="author">`, JSON-LD `author`/`creator`, Dublin Core `dc.creator`, Highwire `citation_author`; values that are URLs are ignored |
| published date | `article:published_time`, `datePublished` meta tags |
| thumbnail | see below |

It also sets `content_vertical` to `academic` when the host is on a list of academic publishers (or contains `.edu`) or the page carries Highwire/PRISM citation tags. On the sample page, captured:

{% fig "_extract_page_metadata on the sample (captured)" %}
```json
{
 "title": "How pour-over coffee works",
 "description": "A short guide to grind size, water temperature and pour timing for a cleaner cup.",
 "thumbnail": "https://journal.example.com/media/cover.jpg",
 "author": "Mira Okafor",
 "published_date": "2026-09-14T08:30:00Z",
 "content_vertical": "general",
 "vertical_metadata": {}
}
```
{% endfig %}

The `og:title` won over `twitter:title` and `<title>`. The author came from `<meta name="author">`, so the different name in the JSON-LD block was never read: JSON-LD is only consulted when the earlier sources give nothing. The relative `og:image` (`/media/cover.jpg`) was resolved against the page URL.

**The thumbnail fallback chain** tries, in order: `og:image:secure_url`, `og:image`, `twitter:image:src`, `twitter:image`, `itemprop=image`, JSON-LD `image`, `link rel=image_src`, then the first usable `<img>` inside `<article>`, `<main>` or `<body>`. A candidate is rejected if it is a `data:` URI, ends in `.svg`, or contains `sprite`, `favicon`, `icon` or `avatar` anywhere in the string. Every accepted candidate is made absolute with `urljoin`. Captured results of the function on eight small pages:

{% fig "_extract_thumbnail_url (captured)" %}
```text
og:image absolute              -> https://cdn.example.com/cover.jpg
og:image relative              -> https://journal.example.com/media/cover.jpg
og:image is .svg               -> https://cdn.example.com/tw.jpg
og:image 'silicon' in name     -> https://cdn.example.com/tw.jpg
only JSON-LD image             -> https://cdn.example.com/ld.jpg
only link rel=image_src        -> https://cdn.example.com/linksrc.jpg
only first <img> in article    -> https://cdn.example.com/body.jpg
nothing                        -> None
```
{% endfig %}

Row 4 shows a side effect of matching substrings: "silicon" contains "icon", so a valid cover image named `silicon-valley.jpg` was thrown away and the next source was used. Row 7 shows the last resort: the avatar was skipped and the `data-src` image (written as `//cdn...`) became `https://cdn...`.

Phase 1 stores these fields and commits; the status stays `processing`. Then it queues phase 2 (`extract_full_content.delay`), or submits an optional Prefect flow when `PREFECT_ENABLED` is on. Prefect is off by default; if its submission fails the code falls back to the Celery task. Page type is also decided here: `_detect_content_type` marks `.pdf` URLs and `application/pdf` as `pdf`, known video hosts as `video`, known social hosts as `social`, and everything else as `article`.

### Step 3: phase 2, trafilatura

`extract_full_content` downloads the page again (a second request, up to the same cap) and calls trafilatura:

{% fig "tasks/extraction.py: ask trafilatura for XML" %}
<!--src content-queue-backend/app/tasks/extraction.py#623-631-->
```python
xml_content = trafilatura.extract(
    downloaded,
    include_comments=False,
    include_tables=True,
    include_images=True,
    include_links=True,
    output_format="xml",
    no_fallback=False,
)
```
{% endfig %}

If the item already has more than 100 characters of `full_text` (a browser-extension save), this step is skipped and only the embedding task is queued. On the sample page trafilatura returned this (captured, abridged):

{% fig "trafilatura XML (captured, long paragraphs shortened)" %}
```text
<doc fingerprint="e64beb5c8f60d6a2">
  <main>
    <head rend="h1">How pour-over coffee works</head>
    <p>Pour-over coffee is made by pouring hot water ...</p>
    <p>The cup you get depends on how quickly ...</p>
    <graphic src="https://cdn.example.com/photos/kettle-pour.jpg" alt="Water poured ..."/>
    <head rend="h2">Water temperature</head>
    <p>Most brewers aim for water between 92 and 96 ...</p>
    <p>Letting a kettle rest for thirty seconds ...</p>
    <graphic src="/img/pour-bloom.jpg" alt="The coffee bed blooming ..."/>
    <head rend="h2">Pour timing</head>
    ...
    <graphic src="https://cdn.example.com/photos/david-portrait.jpg" alt="The author tasting the cup"/>
    <p>Taste the result and change one variable at a time. ...</p>
  </main>
  <comments/>
</doc>
```
{% endfig %}

The navigation bar and footer are gone. The lazy-loaded image appears with its real `data-src` address, not the placeholder. The relative image appears as `/img/pour-bloom.jpg`: trafilatura was called without the page URL, so it did not make it absolute.

### Step 4: XML to HTML

`xml_to_html(xml, original_html=downloaded)` builds the HTML. It also re-reads the original page to learn three things: the original heading levels, the page title and description (to drop duplicates of them from the body), and a list of images with the text just before and after each. Headings are renormalized into the `h2` to `h4` range, and a heading that matches the page title is dropped (that is why the `<h1>` disappeared). Paragraphs, lists and quotes are rendered; images become `<figure>` elements. Output on the sample (captured, text shortened):

{% fig "xml_to_html on the sample (captured, abridged)" %}
```html
<p>Pour-over coffee is made by pouring hot water ...</p>
<p>The cup you get depends on how quickly ...</p>
<figure style="margin:1.5em 0; text-align:center;"><img src="https://cdn.example.com/photos/kettle-pour.jpg" alt="Water poured from a gooseneck kettle" style="max-width:100%; height:auto; border-radius:4px;"/></figure>
<h2>Water temperature</h2>
<p>Most brewers aim for water between 92 and 96 ...</p>
<p>Letting a kettle rest for thirty seconds ...</p>
<figure style="..."><img src="/img/pour-bloom.jpg" alt="The coffee bed blooming after the first pour" style="..."/></figure>
<h2>Pour timing</h2>
<p>Start with a short bloom: ...</p>
<p>Then pour in slow circles ...</p>
<figure style="..."><img src="https://cdn.example.com/photos/david-portrait.jpg" alt="The author tasting the cup" style="..."/><figcaption style="...">Taste the result and change one variable at a time. Grind size is usually the first thing to adjust, ...</figcaption></figure>
<p>Taste the result and change one variable at a time. Grind size is usually ...</p>
```
{% endfig %}

Three things in this output are worth knowing.

- **The image order is right**: each image sits after the paragraph it followed on the page. Images that trafilatura reports as `<graphic>` are inserted where it put them. Images it did not report are re-inserted by matching the text before or after them in the original page (50 characters must match); an image with no nearby text longer than 50 characters is dropped.
- **The last figure has a wrong caption.** For a `<graphic>`, the code takes the next paragraph as a caption if it is under 200 characters. Here that paragraph is body text, so it is shown twice: as a caption and as a paragraph. Confirmed on this sample.
- **The relative image kept its relative address** (`/img/pour-bloom.jpg`). The reader will request it from sed.i's own domain, not from the article's site. Confirmed below.

### Step 5: images, and three suspected weaknesses

Body images are **hotlinked**: the stored HTML holds the original remote address, and the browser fetches each image from the article's site every time the article is read. Nothing is downloaded or copied. In the development library (66 items with text, aggregate counts, measured) 25 items contain images; the 149 `<img>` tags there have 119 `http(s)` addresses, 30 `data:` addresses (embedded inline, from extension saves) and 0 relative addresses.

Three suspected weaknesses were checked by reading the code and running it:

| Suspicion | Verdict | Evidence |
|---|---|---|
| Relative image URLs are not resolved against the page URL | **Confirmed** | Step 3 and 4 output above: `src="/img/pour-bloom.jpg"` goes through unchanged. The same holds for links: `<a href="/glossary/bloom">` stayed relative (captured). Neither `xml_to_html` nor the frontend resolves it; only `//host/...` is rewritten to `https:`. In the dev library no image is relative, but 3 of 42 items with links have relative links. |
| The skip-list matches substrings anywhere in the URL | **Confirmed** | Run below |
| No referrer policy on image tags | **Confirmed** | The frontend's `img` elements (reader body, thumbnail) set no `referrerPolicy`, `next.config.ts` sets no `Referrer-Policy` header, and only anchors use `rel="noopener noreferrer"`. Not tested in a browser. |

The skip-list is a filter on images re-inserted by context matching:

{% fig "tasks/extraction.py: images whose URL contains any of these words are skipped" %}
<!--src content-queue-backend/app/tasks/extraction.py#1200-1221-->
```python
# Skip icons/logos/social/video thumbnails/placeholders
if src and any(
    x in src.lower()
    for x in [
        "icon",
        "logo",
        "avatar",
        "badge",
        "video",
        "play",
        "thumbnail",
        "poster",
        "vid",
        "facebook",
        "twitter",
        "linkedin",
        "placeholder",
        "vid-thumbnail",
        "video-poster",
        "video_placeholder",
    ]
):
```
{% endfig %}

The test is `word in url`, applied to the whole address including the host. Captured with a page whose paragraphs trafilatura returned without images, so only the context-matching path could add them:

{% fig "Which image URLs survive the skip-list (captured)" %}
```text
KEPT    https://cdn.example.com/photos/kettle-pour.jpg
DROPPED https://cdn.example.com/photos/david-portrait.jpg
KEPT    https://cdn.example.com/photos/davis-portrait.jpg
DROPPED https://cdn.example.com/photos/silicon-valley.jpg
DROPPED https://cdn.example.com/display/espresso.jpg
KEPT    https://catalog.example.com/espresso.jpg
DROPPED https://playground.example.com/espresso.jpg
KEPT    /img/pour-bloom.jpg
```
{% endfig %}

"david" contains `vid`, "silicon" contains `icon`, "display" and "playground" contain `play`. So legitimate photographs are removed. In the walkthrough the "david" image survived only because trafilatura reported it as a `<graphic>`, and that path does not consult the list. The effect is therefore limited to images trafilatura missed, but the rule is wrong for them.

Two smaller findings from the same code: image `alt` text and captions are placed into the HTML without escaping (a quote in an `alt` ends the attribute early, captured), and the frontend relies on its sanitizer (`DOMPurify`, applied in `lib/bionicReading.ts`) for safety. Other readers of `full_text` (search, API clients) see the unescaped text.

### Step 6: word count, reading time, completeness

The worker stores the HTML, takes the visible text, and splits on whitespace:

```python
words = len(BeautifulSoup(html_text, "html.parser").get_text().split())
reading_time_minutes = max(1, round(words / 200))
```

The sample gave 257 words and 1 minute (captured). The count includes the duplicated caption from Step 4. If the HTML is shorter than 100 characters the code falls back to trafilatura's plain-text output wrapped in paragraphs; if that is also empty, the item is `completed` with the error "Could not extract article text".

For `article` items, `_detect_limited_extraction_reason(downloaded, html_text)` then compares the original page with the extraction. In order, it flags:

1. **Restriction markers** (words such as "paywall", "subscribe to continue", "metered", found in the first 15,000 characters of page text or in any `class` or `id`) together with a short extraction (under 2,200 characters, 5 or fewer substantial paragraphs). Message: "Content appears restricted by a paywall or source access controls".
2. **Structured paywall signals** (JSON-LD `isAccessibleForFree: false`, or a content-tier meta tag saying paid or premium) with the same shortness test. If these signals are present but the text is long, extraction is trusted and the function returns nothing (a logged-in extension user got the whole article).
3. **Hero image and no body**: media present, no substantial paragraph, under 900 characters, from a page with at least 1,500 characters of text.
4. **Teaser overlap**: the first 200 characters of the page's description appear inside the extracted text, and the text is not much longer than the description.
5. **Low coverage**: many paragraphs or 5,000+ characters on the page, but 4 or fewer paragraphs extracted and less than 40% of the page's text; or a page of 4,000+ characters where under 900 characters were extracted (under 18%).

Message for 3 to 5: "Limited content extracted from source page". An item that passes all checks gets no error. If the download was truncated at the cap and nothing else was flagged, the message is "Partial extraction: the page exceeded the size limit and was truncated".

Run on four small pages built for this guide (captured):

{% fig "_detect_limited_extraction_reason on four pages (captured)" %}
```text
A full article, no markers                       extracted= 1707 chars -> None
B paywalled teaser                               extracted=  151 chars -> 'Content appears restricted by a paywall or source access controls'
C short real article + 'continue reading' link   extracted=  880 chars -> 'Content appears restricted by a paywall or source access controls'
D short real article, nothing else               extracted=   66 chars -> None
```
{% endfig %}

Case B is a teaser with a `paywall` element and a JSON-LD flag, correctly flagged. Case C is an ordinary short article with an unrelated "Continue reading" link in a sidebar: it is flagged as paywalled, a **false positive** caused by the marker "continue reading" appearing anywhere in the page text. Case D shows that a genuinely tiny article is not flagged by size alone, which was the design intent ("avoid relying on absolute length alone").

On the sample page the check returned `None`.

### Step 7: what happens next

`extract_full_content` commits `completed` and queues `generate_embedding`. That task embeds the whole article, then queues two more: `analyze_article_task` (tags and entities) and `generate_chunk_embeddings_task` (see [Chunking and embedding](/retrieval/chunking-and-embedding/)). Tagging and analysis are covered in [Tagging and article analysis](/llm/tagging-and-analysis/).

## How it is built here

| File | Role |
|---|---|
| `content-queue-backend/app/tasks/extraction.py` | `extract_metadata`, `extract_full_content`, `_extract_page_metadata`, `_extract_thumbnail_url`, `xml_to_html`, the limited-extraction detectors |
| `content-queue-backend/app/core/fetch.py` | `fetch_bounded`: caps, truncation, binary-type skip, PDF rejection |
| `content-queue-backend/app/core/config.py` | `FETCH_HTML_MAX_BYTES`, `FETCH_PDF_MAX_BYTES`, `FETCH_MAX_SECONDS` |
| `content-queue-backend/app/workflows/ingestion.py` | the optional Prefect version of phases 2 to 5 |
| `frontend/lib/ingestErrors.ts` | turns `processing_status` plus `processing_error` into a label and message |
| `frontend/components/HighlightRenderer.tsx` | renders the stored HTML, including `img` elements |

### How errors are stored and shown

`processing_error` is free text, set by whichever code path hit the problem:

| Where | Status | `processing_error` |
|---|---|---|
| phase 1, HTTP 401 | `failed` | "Authorization required (401) - ..." |
| phase 1, HTTP 403 | `failed` | "Access forbidden (403) - Site blocks bots" |
| phase 1, other HTTP error, or timeout | `failed`, then retried | "HTTP 404: ..." or "Request timed out" |
| phase 1, any other exception | `failed` | the exception text |
| phase 2, timeout or request error | `completed` | "Request timed out" or "Request error: ..." |
| phase 2, no text | `completed` | "Could not extract article text" |
| phase 2, limited or truncated | `completed` | the messages above |
| embedding exhausted retries | `failed` | "Embedding error: ..." |

The frontend does not receive a code, only this text, so it classifies by searching the lowercase text for substrings. A `failed` item is sorted into blocked, unauthorized, source-access, network or unknown. A `completed` item with any error text is always "partial". Captured by compiling `ingestErrors.ts` and running it:

{% fig "getIngestIssue on sample inputs (captured; messages shortened)" %}
```text
failed     Access forbidden (403) - Site blocks bots          -> blocked
failed     Authorization required (401) - ...                 -> unauthorized
failed     Request timed out                                  -> network
failed     HTTP 404: 404 Client Error ... for url: .../a      -> source_access
failed     HTTP 404: 404 Client Error ... .../robots-guide    -> blocked
failed     Embedding error: connection reset                  -> network
failed     unexpected EOF                                     -> unknown
completed  Content appears restricted by a paywall ...        -> partial
completed  Request timed out                                  -> partial
completed  Could not extract article text                     -> partial
completed  (empty)                                            -> none
```
{% endfig %}

Two classification problems show up. The HTTP error text includes the URL, and the matcher looks for words such as "bot" anywhere, so a 404 for a URL containing "robots" is shown as "blocked by source site". And a 404 is labelled "Source site access issue", not "not found". Both come from classifying free text instead of a stored error code.

## Design choices

| Choice | Alternatives | Why this one |
|---|---|---|
| Two phases: metadata first, then full text | One task doing everything | The card gets a title and image within seconds, and a slow or failing full-text pass does not lose the metadata (a phase 2 timeout still completes with metadata). The price is a second download of the same page. |
| trafilatura for article detection | Mozilla Readability (used by the browser extension); an LLM to extract; custom per-site rules | trafilatura runs server-side in Python with no browser, handles many page layouts, and reports images and links. Readability needs a DOM. An LLM per page costs money and time. The extension path uses Readability in the user's browser instead, with the user's login. |
| XML output plus own converter | trafilatura's HTML or plain-text output | XML keeps heading, list, link and image structure in a form the converter can reshape (heading levels, image placement). The converter is the largest and most fragile function in the file. |
| Hotlink images | Copy images to object storage; proxy them through the backend | No storage cost, no copyright copy, nothing to garbage-collect. Cost: link rot, hotlink blocking, the image host sees the reader, and relative URLs break. |
| Heuristic limited-extraction flags | Always trust the text; ask an LLM whether the article is complete | Cheap, runs on every article, and the user gets a prompt to open the original. Cost: false positives and negatives that cannot be measured without labelled data. |
| Truncate HTML at 10 MB, reject PDFs at 100 MB | Reject big pages; no cap | A truncated article is still useful; the cap bounds worker memory. See ADR-0007 in `docs/decisions/0007-celery-memory-ml-tasks.md`. |
| Celery chain by default, Prefect optional | Prefect always | Fewer services to run; see ADR-0005 in `docs/decisions/0005-pipeline-orchestration.md`. |

## How it fails

| Failure | What happens |
|---|---|
| Site blocks the worker (403, bot page) | `failed` with a readable message; no retry; the user is told to open the original. |
| Site needs login or subscription | HTTP 401 gives `failed`. A paywalled page that returns 200 with a teaser becomes `completed` plus the paywall warning, when a heuristic fires. |
| Timeout or server error | Phase 1: `failed`, retried with backoff (about 1, 2 and 4 minutes). Phase 2: `completed` with a note, since metadata is already saved. |
| Page over 10 MB | Cut at 10 MB; ingested; marked "Partial extraction". Before the cap existed, a 20 MB page produced 7,634 chunks and a 1.9 GB worker peak (see [Chunking and embedding](/retrieval/chunking-and-embedding/)). |
| Binary URL (video, image) | Empty body; `completed` with "Could not extract article text". |
| trafilatura finds nothing | Falls back to plain text; if that fails too, the "Could not extract" outcome. |
| `xml_to_html` raises | The exception is caught and the text is returned as paragraphs, so the article is saved without structure. |
| Embedding fails after the text was saved | The item becomes `failed` although its text is stored. |

An item that already has more than 100 characters of text is never re-extracted: both phases return early. Improving the extractor therefore does not fix articles that were already saved (inferred from the code; not run).

## What is weak or missing

Ranked by how likely each is to matter.

1. **Relative image and link addresses are not resolved.** Images fail to load for any page that uses relative paths. Fix: pass the page URL to trafilatura (`url=` argument) and call `urljoin` on every `img`, `graphic` and `a` address in `xml_to_html`.
2. **Hotlinked images with no referrer policy.** Sites that block foreign referrers show broken images, and every image request tells the image host the reader's IP and the sed.i origin. Fix: set `referrerpolicy="no-referrer"` on article images (or a `Referrer-Policy` header), and decide whether to copy images into storage.
3. **Limited-extraction heuristics have no measured accuracy.** Case C above is a false positive from one phrase. The thresholds (2,200 characters, 5 paragraphs, 0.4, 0.18) were set by hand. Fix: collect a labelled set of saved articles and measure precision and recall before changing them.
4. **Substring matching in several places**: the image skip-list, the thumbnail filter and the frontend error classifier all match words inside longer strings. Fix: match whole path segments or filename tokens, and store a machine-readable error code next to the text.
5. **Wrong caption from the next paragraph.** A short paragraph after an image is duplicated as its caption. Fix: only use `<figcaption>` from the original page.
6. **`failed` is overloaded**, and an item whose embedding failed looks like an extraction failure.
7. **Two downloads per article, and several parses of the same HTML** (metadata, trafilatura, the converter, the detector). It costs time and memory; the second download could be avoided by passing the first response forward.
8. **No re-extraction path.** Items with text are skipped, so extractor fixes need a manual reset.

{% say %}
After the save returns, a worker downloads the page with a size cap of 10 megabytes and skips video and other binary types. It first reads the metadata tags, Open Graph, Twitter and JSON-LD, and stores title, description, author, date and a cover image so the library card is useful quickly. Then it downloads the page again and runs trafilatura, which cuts the article out of the page and gives back XML. My own converter turns that into HTML, normalizes the heading levels, and puts images back next to the paragraphs they followed. It counts words, estimates reading time at 200 words a minute, and runs heuristics that look for paywall markers, teasers and low coverage; if one fires, the item is completed with a warning that the frontend shows as limited extraction. Images are hotlinked, not copied, and the weak spots I would name are that relative image URLs are not resolved, that a substring skip-list drops images like david-portrait, and that the frontend sets no referrer policy.
{% endsay %}

## Check yourself

{% qa "Why does the worker download the page twice, and what does each pass buy?" %}
The first pass gives the library card a title, description and cover image quickly, and keeps them even if the second pass times out. The second pass does the heavy trafilatura work. The cost is two downloads and extra parsing; passing the first response to the second task would remove that.
{% endqa %}

{% qa "A site serves a 3 MB article page and a 30 MB video at the same URL path pattern. What does fetch_bounded do with each?" %}
The page is read in full (under the 10 MB cap). The video response is recognized by its content type and not downloaded at all, so the body is empty and the item ends `completed` with "Could not extract article text".
{% endqa %}

{% qa "An article contains an image at /img/chart.png. What does the reader show and why?" %}
A broken image. trafilatura was called without the page URL and `xml_to_html` only rewrites protocol-relative addresses, so the stored HTML keeps `/img/chart.png`. The browser resolves it against the sed.i domain. The fix is to make addresses absolute with `urljoin` against the page URL during extraction.
{% endqa %}

{% qa "Why is a short real article not flagged as limited, but one with a sidebar link saying Continue reading is?" %}
The heuristics avoid flagging on length alone, so a tiny article with no markers passes. The marker list includes "continue reading", which is searched in all the page text, so the sidebar phrase counts as a paywall marker and, combined with a short extraction, triggers the flag.
{% endqa %}

{% qa "The frontend shows Blocked by source site for a 404. What in the code allows that?" %}
The classifier searches the error text for words such as "bot". The stored message for an HTTP error includes the URL, so a URL containing "robots" matches. Storing a numeric error code, and classifying on it, removes the guesswork.
{% endqa %}

{% qa "Why are images re-inserted by matching surrounding text, and what happens to an image with no text near it?" %}
trafilatura can omit images, so the converter records each image and the 100 characters before and after it in the original page, and re-inserts it when a paragraph in the output matches. If there is no neighbouring text longer than 50 characters, the image is never recorded and is dropped.
{% endqa %}
