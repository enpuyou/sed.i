/**
 * content.js — sed.i extraction logic
 *
 * Injected on demand via chrome.scripting.executeScript. Readability.js is injected first.
 *
 * Exposes window.__sediExtractAndInlineContent so popup.js can invoke it via a func:
 * executeScript call — which Safari properly awaits, unlike files: Promise returns.
 *
 * Sync IIFE for scoping (prevents re-declaration errors on multiple injections).
 */

(function () {
if (window.__sediExtractAndInlineContent) return;

const MAX_IMAGES = 15;
const MIN_IMAGE_SIZE = 80; // px — skip tracking pixels / icons

// Noise selectors — removed before Readability runs to reduce junk extraction.
// Mirrors the approach in the backend's xml_to_html() function.
const NOISE_SELECTORS = [
  'nav', 'footer', 'aside',
  '[class*="related"]', '[class*="recommended"]',
  '[class*="sidebar"]',
  // newsletter: avoid matching Substack's 'newsletter-post' article class
  '[class*="newsletter-signup"]', '[class*="newsletter-embed"]',
  '[class*="newsletter-widget"]', '[class*="newsletter-form"]',
  '[class*="newsletter-subscribe"]', '[class*="newsletter-cta"]',
  '[class*="promo"]', '[class*="advertisement"]',
  '[class*="comment"]', '[class*="social-share"]',
  '[id*="related"]', '[id*="sidebar"]',
  '[id*="newsletter"]',
  '[data-testid*="ad"]',
];

/**
 * Wait for a selector to appear in the DOM, up to `timeout` ms.
 * Falls back gracefully — if content never appears we still extract whatever is there.
 */
function waitForSelector(selector, timeout = 3000) {
  return new Promise((resolve) => {
    if (document.querySelector(selector)) return resolve(true);
    const observer = new MutationObserver(() => {
      if (document.querySelector(selector)) {
        observer.disconnect();
        resolve(true);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); resolve(false); }, timeout);
  });
}

/**
 * Convert an image src to a base64 data URI.
 * Returns null on any failure (CORS, 404, etc.).
 */
async function imageToDataUri(src) {
  try {
    const resp = await fetch(src, { mode: 'cors', credentials: 'include' });
    if (!resp.ok) return null;
    const blob = await resp.blob();
    return await new Promise((res) => {
      const reader = new FileReader();
      reader.onloadend = () => res(reader.result);
      reader.onerror = () => res(null);
      reader.readAsDataURL(blob);
    });
  } catch {
    return null;
  }
}

/**
 * Pick the highest-resolution URL from a srcset attribute string.
 * e.g. "img-300.jpg 300w, img-800.jpg 800w" → "img-800.jpg"
 */
function bestSrcFromSrcset(srcset) {
  try {
    return srcset
      .split(',')
      .map((s) => {
        const parts = s.trim().split(/\s+/);
        return { url: parts[0], w: parseInt(parts[1]) || 0 };
      })
      .sort((a, b) => b.w - a.w)[0]?.url || '';
  } catch {
    return '';
  }
}

/**
 * Extract Open Graph / meta tag metadata from the live, authenticated DOM.
 * Sending this to the backend avoids the need for backend to re-fetch the URL
 * (which often results in 403 on paywalled/rate-limited sites).
 */
function extractPageMeta() {
  const get = (prop, attr = 'property') =>
    document.querySelector(`meta[${attr}="${prop}"]`)?.getAttribute('content') || '';

  // Collect all article:author meta tags — some articles have multiple
  function getAuthors() {
    const isUrl = (s) => s.startsWith('http') || s.startsWith('/');
    // Try all article:author metas first
    const metas = [
      ...document.querySelectorAll('meta[property="article:author"]'),
      ...document.querySelectorAll('meta[name="author"]'),
    ];
    const names = [];
    for (const m of metas) {
      const val = (m.getAttribute('content') || '').trim();
      if (val && !isUrl(val) && !names.includes(val)) names.push(val);
    }
    if (names.length) return names.join(', ');

    // Fallback: JSON-LD structured data
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const data = JSON.parse(script.textContent || '');
        const objs = Array.isArray(data) ? data : [data];
        for (const obj of objs) {
          const af = obj.author || obj.creator;
          if (!af) continue;
          const list = Array.isArray(af) ? af : [af];
          const found = list.map(a => typeof a === 'string' ? a : a.name).filter(Boolean);
          if (found.length) return found.join(', ');
        }
      } catch {}
    }
    return '';
  }

  return {
    description:   get('og:description')          || get('description', 'name'),
    thumbnail:     get('og:image')                || get('twitter:image', 'name'),
    author:        getAuthors(),
    publishedDate: get('article:published_time')  || get('datePublished', 'name'),
  };
}

/**
 * Detect whether the current page has an access restriction (paywall/subscription gate).
 *
 * Runs in the authenticated browser context — has access to the real JSON-LD and
 * rendered DOM that the backend cannot see from an unauthenticated fetch.
 *
 * Returns true only when there is strong evidence the article body is gated.
 */
function detectAccessRestriction() {
  // 1. Schema.org JSON-LD: isAccessibleForFree: false is a publisher-declared paywall signal
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const data = JSON.parse(script.textContent || '');
      function walk(node) {
        if (!node || typeof node !== 'object') return false;
        if (Array.isArray(node)) return node.some(walk);
        const flag = node.isAccessibleForFree;
        if (flag === false) return true;
        if (typeof flag === 'string' && ['false', 'no', '0'].includes(flag.trim().toLowerCase())) return true;
        return Object.values(node).some(walk);
      }
      if (walk(data)) return true;
    } catch {}
  }

  // 2. content_tier meta tag (used by publishers like Bloomberg, FT)
  const tierMeta = document.querySelector('meta[name*="content_tier"], meta[name*="contentTier"]');
  if (tierMeta) {
    const tier = (tierMeta.getAttribute('content') || '').toLowerCase();
    if (['paid', 'premium', 'subscriber', 'metered'].some(t => tier.includes(t))) return true;
  }

  // 3. DOM paywall gate: look for common paywall wrapper elements
  const paywallSelectors = [
    '[class*="paywall"]', '[id*="paywall"]',
    '[class*="subscribe-wall"]', '[class*="subscribewall"]',
    '[class*="access-wall"]', '[class*="meter-wall"]',
    '[data-testid*="paywall"]', '[data-testid*="subscribe"]',
  ];
  for (const sel of paywallSelectors) {
    if (document.querySelector(sel)) return true;
  }

  return false;
}

/**
 * Main extraction entry point.
 */
async function extractAndInlineContent() {
  // Priority selectors: specific publisher article-body containers.
  // We try these first (with a shorter timeout) before falling back to generic ones.
  // Race them all in parallel — resolves as soon as any one appears (or 2s timeout).
  const specificSelectors = [
    '[data-article-body]',      // Nature, Springer
    '.article__body',           // Nature (legacy)
    '.c-article-body',          // Springer Nature group
    '.c-article-section',       // Springer Nature sections
  ];

  const genericSelectors = [
    'article .body',
    '#article-content',
    '.article-content',
    '.post-content',
    '.entry-content',
    'article',
  ];

  // Skip selector waiting entirely for the read path — page is already loaded,
  // Readability works on whatever DOM is present. Waiting is only useful for
  // SPAs still rendering content, which is irrelevant for an already-viewed page.
  let foundSpecific = false;
  if (!window.__SEDI_SKIP_IMAGE_INLINE) {
    foundSpecific = await Promise.race(
      specificSelectors.map(sel => waitForSelector(sel, 2000))
    );
    if (!foundSpecific) {
      for (const sel of genericSelectors) {
        const found = await waitForSelector(sel, 1000);
        if (found) break;
      }
    }
  } else {
    foundSpecific = specificSelectors.some(sel => !!document.querySelector(sel));
  }

  // Capture debug info about what we found before Readability strips it
  const debugInfo = {
    foundSpecific,
    specificSelectorPresent: specificSelectors.map(s => ({ sel: s, found: !!document.querySelector(s) })),
    bodyTextLength: document.body?.innerText?.length ?? 0,
  };

  // Extract live-DOM metadata before cloning (includes auth-gated OG tags)
  const pageMeta = extractPageMeta();

  // Detect access restriction from the live authenticated DOM — must run before
  // cloning since paywall DOM elements may be removed/altered by Readability.
  const accessRestricted = detectAccessRestriction();

  // Clone the full document so Readability doesn't mutate the live page
  const docClone = document.cloneNode(true);

  // Pre-clean noise from the clone before Readability runs.
  const cloneBody = docClone.querySelector('body');
  if (cloneBody) {
    // 0. Hoist the known article container to body so Readability scores only real content
    const ARTICLE_CONTAINERS = [
      '.available-content',   // Substack
      '.post-content',
      '.entry-content',
      'article',
    ];
    for (const sel of ARTICLE_CONTAINERS) {
      const articleEl = cloneBody.querySelector(sel);
      if (articleEl && articleEl.textContent.trim().length > 200) {
        cloneBody.innerHTML = '';
        cloneBody.appendChild(articleEl);
        break;
      }
    }

    // 1. Strip classes/ids from headings so Readability's _cleanHeaders can't give
    //    them negative weight and remove them (e.g. Substack h2s have "widget" in class)
    cloneBody.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
      h.removeAttribute('class');
      h.removeAttribute('id');
    });

    // 2. Remove structural noise elements (nav, footer, ads, etc.)
    NOISE_SELECTORS.forEach((sel) => {
      try { cloneBody.querySelectorAll(sel).forEach((el) => el.remove()); } catch {}
    });

    // 2. Remove elements with inline display:none or visibility:hidden —
    //    these are invisible to the user but Readability picks up their text.
    //    Only checking inline styles (getComputedStyle doesn't work on detached clones).
    //    Guard: keep elements with >50 words — could be meaningful collapsed content
    //    (e.g. tabbed sections). Only remove small hidden nodes (ad labels, decorative spans).
    cloneBody.querySelectorAll('[style]').forEach((el) => {
      const s = el.getAttribute('style') || '';
      if (/display\s*:\s*none/i.test(s) || /visibility\s*:\s*hidden/i.test(s)) {
        if ((el.textContent?.trim().split(/\s+/).length ?? 0) > 50) return;
        el.remove();
      }
    });

    // Pre-mark Substack ImageGallery figures with their shared gallery class as data-sedi-gid.
    // keepClasses:false strips classes after Readability, but data attributes survive.
    cloneBody.querySelectorAll('figure[data-component-name="ImageGallery"]').forEach(f => {
      const galleryClass = Array.from(f.classList).find(c => c.startsWith('gallery-'));
      if (galleryClass) f.setAttribute('data-sedi-gid', galleryClass);
    });
  }

  const reader = new Readability(docClone, {
    charThreshold: 20,
    keepClasses: false,
    nbTopCandidates: 20,
  });
  const article = reader.parse();

  console.log('[sedi][content.js][safari-resources] loaded patch: gallery grouping rules updated');

  if (!article || !article.content) {
    return { error: 'Could not extract article content from this page.' };
  }

  const { title, byline, content } = article;

  // Parse the extracted HTML into a temp element for image processing and dedup
  const container = document.createElement('div');
  container.innerHTML = content;


  // Strip standalone ad-label text artifacts that slipped through Readability.
  // Only removes leaf-ish nodes (no <p>/<h*>/<img> children) whose entire text
  // is an ad label — safe for Wikipedia folded sections which have real children.
  const adPattern = /^(advertisement|skip\s+advertisement)$/i;
  container.querySelectorAll('*').forEach((el) => {
    if (adPattern.test(el.textContent.trim()) && !el.querySelector('p, h1, h2, h3, img')) {
      el.remove();
    }
  });

  // Prefer the article's <h1> as the title — it reflects what the reader sees,
  // whereas the <title> tag is often a SEO/social variant with different phrasing.
  const h1El = container.querySelector('h1');
  const h1Text = h1El?.textContent?.trim() || '';
  const effectiveTitle = (h1Text.length > 10) ? h1Text : (title || document.title);

  // Remove the h1 if it matches our chosen title (avoid showing it twice in reader)
  if (h1El && h1Text.toLowerCase() === effectiveTitle.toLowerCase()) {
    h1El.remove();
  }

  // Remove subtitle elements — shown separately in the reader header.
  const subtitleSelectors = [
    '[class*="subtitle"]', '[class*="post-subtitle"]', '[class*="article-subtitle"]',
    '[class*="deck"]', '[class*="article-deck"]',
  ];
  subtitleSelectors.forEach((sel) => {
    try { container.querySelectorAll(sel).forEach((el) => el.remove()); } catch {}
  });
  if (pageMeta.description) {
    const descNormSub = pageMeta.description.trim().toLowerCase();
    const candidates = Array.from(container.querySelectorAll('h2, h3, p')).slice(0, 5);
    for (const el of candidates) {
      const text = el.textContent.trim().toLowerCase();
      if (!text || text.length > 300) continue;
      const minLen = 20;
      const isMatch = text === descNormSub
        || (text.length >= minLen && descNormSub.startsWith(text))
        || (descNormSub.length >= minLen && text.startsWith(descNormSub));
      if (isMatch) {
        el.remove();
        break;
      }
    }
  }

  // Remove author avatar / profile images — small circular images that appear in bylines.
  // Identified by: avatar/author/profile in class or alt, or very small naturalWidth.
  container.querySelectorAll('img').forEach((img) => {
    const cls = (img.className || '').toLowerCase();
    const alt = (img.getAttribute('alt') || '').toLowerCase();
    const parentCls = (img.parentElement?.className || '').toLowerCase();
    if (/avatar|author|profile|byline|headshot/.test(cls + alt + parentCls)) {
      img.closest('figure')?.remove() || img.remove();
    }
  });

  // ── Strip metadata that the reader shows separately ──────────────────────────
  //
  // The reader displays title, description, author, published date, and thumbnail
  // in its own header section. Remove any HTML elements in the extracted body that
  // duplicate these — so the article body starts at the actual article content.

  // Helper: strip the last hyphen-separated size suffix from a CDN filename stem.
  // e.g. "photo-ktcv-superJumbo.jpg" → "photo-ktcv"
  // This lets us match "facebookJumbo" and "superJumbo" variants of the same image.
  function fileStem(url) {
    const file = url.split('?')[0].split('/').pop() || '';
    const noExt = file.replace(/\.[^.]+$/, '');
    return noExt.replace(/-[^-]+$/, '');
  }

  // 1. Thumbnail — remove ALL images whose stem matches og:image stem.
  //    CDNs serve the same image with different size suffixes; filename-only match catches those.
  if (pageMeta.thumbnail) {
    const ogStem = fileStem(pageMeta.thumbnail);
    if (ogStem) {
      container.querySelectorAll('img').forEach((img) => {
        const src = img.getAttribute('src') || img.getAttribute('data-src') || '';
        if (fileStem(src) === ogStem) {
          img.closest('figure')?.remove() || img.remove();
        }
      });
    }
  }

  // 2. Description — remove paragraphs that are the OG description or overlap with it.
  //    OG description is often a truncated version of the lede paragraph, so we match
  //    if either string starts with the other (handles both truncated and full variants).
  if (pageMeta.description) {
    const descNorm = pageMeta.description.trim().toLowerCase();
    container.querySelectorAll('p').forEach((p) => {
      const pText = p.textContent.trim().toLowerCase();
      if (pText === descNorm || pText.startsWith(descNorm) || descNorm.startsWith(pText)) {
        p.remove();
      }
    });
  }

  // 3. Author — remove small elements whose entire text matches the author name(s).
  //    Strips "By " / "by " prefix — some sites include it in the byline element text.
  //    Only removes leaf-ish nodes to avoid accidentally removing author bio paragraphs.
  if (pageMeta.author) {
    const authorNorm = pageMeta.author.trim().toLowerCase();
    container.querySelectorAll('*').forEach((el) => {
      if (el.querySelector('p, h1, h2, h3, img')) return;
      const text = el.textContent.trim().toLowerCase().replace(/^by\s+/, '');
      if (text === authorNorm) el.remove();
    });
  }

  // 4. Published date — remove small elements whose entire text looks like the publish date.
  //    We match by checking if the element text is contained in the ISO date string or vice versa,
  //    since date display formats vary ("Feb. 20, 2026" vs "2026-02-20T...").
  if (pageMeta.publishedDate) {
    // Build a set of normalized date strings to compare against
    const isoDate = pageMeta.publishedDate;
    const dateObj = new Date(isoDate);
    const isValidDate = !isNaN(dateObj.getTime());
    container.querySelectorAll('*').forEach((el) => {
      if (el.querySelector('p, h1, h2, h3, img')) return; // skip containers
      const text = el.textContent.trim();
      if (!text || text.length > 60) return; // dates are short
      // Match if the element looks like a date: contains digits and month-like words or separators
      const looksLikeDate = /\d{4}|\d{1,2}[\/\-\.]\d{1,2}|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(text);
      if (!looksLikeDate) return;
      // Check against the ISO date parts
      if (isValidDate) {
        const year = String(dateObj.getFullYear());
        const month = dateObj.toLocaleString('en', { month: 'short' }).toLowerCase();
        const day = String(dateObj.getDate());
        const textLower = text.toLowerCase();
        if (textLower.includes(year) && (textLower.includes(month) || textLower.includes(day.padStart(2, '0')))) {
          el.remove();
        }
      }
    });
  }

  // Process images — convert to data URIs so they survive cross-origin display.
  // Skipped when __SEDI_SKIP_IMAGE_INLINE is set (ephemeral read path) because
  // the browser tab can load images directly from their original URLs.
  const images = Array.from(container.querySelectorAll('img')).slice(0, MAX_IMAGES);

  if (window.__SEDI_SKIP_IMAGE_INLINE) {
    // Resolve to absolute src — prefer the live DOM .src property (already resolved
    // by lazy-load JS) over attributes which may still hold placeholder data URIs.
    images.forEach((img) => {
      const liveSrc = img.src || '';
      const isLiveReal = liveSrc && !liveSrc.startsWith('data:') && liveSrc.startsWith('http');

      const srcsetVal = img.getAttribute('srcset');
      let attrSrc = srcsetVal ? bestSrcFromSrcset(srcsetVal) : '';
      if (!attrSrc) attrSrc = img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original') || img.getAttribute('src') || '';
      const isAttrReal = attrSrc && !attrSrc.startsWith('data:');

      let src = '';
      if (isLiveReal) src = liveSrc;
      else if (isAttrReal) src = new URL(attrSrc, location.href).href;

      if (!src) { img.remove(); return; }
      img.src = src;
      img.setAttribute('loading', 'lazy');
      img.removeAttribute('srcset');
      img.removeAttribute('data-src');
      img.removeAttribute('data-lazy-src');
      img.removeAttribute('data-original');
    });
  } else {
    await Promise.all(images.map(async (img) => {
      // Prefer highest-resolution source from srcset
      let src = '';
      const srcsetVal = img.getAttribute('srcset');
      if (srcsetVal) src = bestSrcFromSrcset(srcsetVal);

      // Fall back through lazy-load attributes
      if (!src) {
        src =
          img.getAttribute('src') ||
          img.getAttribute('data-src') ||
          img.getAttribute('data-lazy-src') ||
          img.getAttribute('data-original') ||
          '';
      }

      if (!src) { img.remove(); return; }
      if (src.startsWith('data:')) return;

      // Skip tiny images (icons, tracking pixels) — use naturalWidth if available
      if (img.naturalWidth > 0 && img.naturalWidth < MIN_IMAGE_SIZE) { img.remove(); return; }
      if (img.naturalHeight > 0 && img.naturalHeight < MIN_IMAGE_SIZE) { img.remove(); return; }

      // Make sure src is absolute
      const absoluteSrc = new URL(src, location.href).href;
      img.src = absoluteSrc;
      img.setAttribute('loading', 'lazy');
      img.removeAttribute('srcset');
      img.removeAttribute('data-src');
      img.removeAttribute('data-lazy-src');
      img.removeAttribute('data-original');

      const dataUri = await imageToDataUri(absoluteSrc);
      if (dataUri) img.src = dataUri;
      // On failure, keep the absolute src — backend can still display it
    }));
  }

  // ── Gallery grouping ─────────────────────────────────────────────────────────
  // Substack structure: <figure><div>[<img/><img/>...]</div><figcaption/></figure>
  // The div inside the figure holds all the images — mark that div as .sedi-gallery.
  // Also handle adjacent <figure> siblings as a fallback for other sites.
  (function groupGalleries() {
    // Pattern A: figures were pre-marked with data-sedi-gid before Readability.
    // Group them now by extracting each gid group and wrapping in a .sedi-gallery div
    // inserted at the position of the first figure in the group.
    // Group only contiguous runs of figures that share the same data-sedi-gid.
    // This preserves original positions in the DOM and avoids moving distant images.
    const allFigures = Array.from(container.querySelectorAll('figure[data-sedi-gid]'));
    // Walk per parent to preserve local ordering
    const parents = new Map();
    allFigures.forEach(f => {
      const p = f.parentNode;
      if (!parents.has(p)) parents.set(p, []);
      parents.get(p).push(f);
    });
    parents.forEach((figs, parent) => {
      // Iterate the parent's children and collect contiguous runs
      let run = [];
      for (let node = parent.firstElementChild; node; node = node.nextElementSibling) {
        if (node.tagName === 'FIGURE' && node.hasAttribute('data-sedi-gid')) {
          const gid = node.getAttribute('data-sedi-gid');
          if (run.length === 0) run.push(node);
          else {
            const lastGid = run[0].getAttribute('data-sedi-gid');
            if (lastGid === gid) run.push(node);
            else {
              if (run.length >= 2) {
                const gallery = document.createElement('div');
                gallery.className = 'sedi-gallery';
                parent.insertBefore(gallery, run[0]);
                run.forEach(f => { f.removeAttribute('data-sedi-gid'); gallery.appendChild(f); });
                try { console.log('[sedi][content.js][safari-resources] created contiguous gallery preview=', gallery.outerHTML.slice(0,400)); } catch (e) {}
              }
              run = [node];
            }
          }
        } else {
          if (run.length >= 2) {
            const gallery = document.createElement('div');
            gallery.className = 'sedi-gallery';
            parent.insertBefore(gallery, run[0]);
            run.forEach(f => { f.removeAttribute('data-sedi-gid'); gallery.appendChild(f); });
            try { console.log('[sedi][content.js][safari-resources] created contiguous gallery preview=', gallery.outerHTML.slice(0,400)); } catch (e) {}
          }
          run = [];
        }
      }
      // tail run
      if (run.length >= 2) {
        const gallery = document.createElement('div');
        gallery.className = 'sedi-gallery';
        parent.insertBefore(gallery, run[0]);
        run.forEach(f => { f.removeAttribute('data-sedi-gid'); gallery.appendChild(f); });
        try { console.log('[sedi][content.js][safari-resources] created contiguous gallery preview=', gallery.outerHTML.slice(0,400)); } catch (e) {}
      }
    });

    // Pattern B: figure > div containing multiple imgs (single figure with inline gallery)
    container.querySelectorAll('figure > div').forEach((div) => {
      if (div.closest('.sedi-gallery')) return;
      if (div.querySelectorAll('img').length >= 2) {
        div.classList.add('sedi-gallery');
      }
    });
  })();

  const html = container.innerHTML;
  const wordCount = html.replace(/<[^>]+>/g, ' ').trim().split(/\s+/).filter(Boolean).length;

  return {
    title:            effectiveTitle,
    byline:           byline || '',
    html,
    url:              location.href,
    wordCount,
    debugInfo,
    accessRestricted,
    // Page metadata from the live authenticated DOM
    description:      pageMeta.description,
    thumbnail:        pageMeta.thumbnail,
    author:           pageMeta.author  || (byline || '').replace(/<[^>]+>/g, '').trim(),
    publishedDate:    pageMeta.publishedDate,
  };
}

window.__sediExtractAndInlineContent = extractAndInlineContent;

})();
