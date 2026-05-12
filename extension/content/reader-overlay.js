/**
 * reader-overlay.js — sed.i read mode
 *
 * Replaces the page body with Readability-extracted content + injected CSS.
 * The original body node (not a clone) is stored and swapped back on toggle.
 * Event listeners and JS state on the original body are preserved; dynamic DOM
 * mutations made after the overlay activates are not (intentional tradeoff).
 * No iframes, no network requests, no auth — instant DOM swap.
 *
 * The article payload (already extracted by popup.js before injecting this
 * script) is read from window.__sediArticle__.
 */

(function () {
  const ACTIVE_ATTR = 'data-sedi-reader-active';
  const THEME_KEY   = '__sedi_theme__';
  const SIZE_KEY    = '__sedi_size__';

  // ── Toggle off if already active ──────────────────────────────────────────
  if (document.documentElement.hasAttribute(ACTIVE_ATTR)) {
    document.documentElement.removeAttribute(ACTIVE_ATTR);
    const saved = window.__sediOriginalBody__;
    if (saved) {
      document.body.replaceWith(saved);
      window.__sediOriginalBody__ = null;
    }
    document.getElementById('__sedi_style__')?.remove();
    document.removeEventListener('keydown', window.__sediEscHandler__);
    window.__sediSpyCleanup__?.();
    window.__sediSpyCleanup__ = null;
    return;
  }

  const article = window.__sediArticle__;
  if (!article) return;

  // ── Persist theme + font size across sessions ─────────────────────────────
  const THEMES = ['light', 'dark', 'true-black'];
  let currentTheme = (function () {
    try { return sessionStorage.getItem(THEME_KEY) || 'light'; } catch { return 'light'; }
  })();
  let currentSize  = (function () {
    try { return sessionStorage.getItem(SIZE_KEY) || 'medium'; } catch { return 'medium'; }
  })();

  // ── Save original body (reference, not clone — preserves event listeners) ──
  window.__sediOriginalBody__ = document.body;
  document.documentElement.setAttribute(ACTIVE_ATTR, '');

  // ── CSS: all vars + layout + typography ──────────────────────────────────
  const style = document.createElement('style');
  style.id = '__sedi_style__';
  style.textContent = `
    /* ── Theme variable sets ── */
    [${ACTIVE_ATTR}] {
      --bg:      #fffef7;
      --bg2:     #faf9f5;
      --bg3:     #f5f4f0;
      --fg:      #1a1a1a;
      --fg2:     #4a4a4a;
      --fgm:     #666666;
      --fgf:     #999999;
      --bd:      #e5e4e0;
      --bds:     #eeede9;
      --ac:      #3d46c2;
      --ach:     #2d36b2;
      --prg:     #f97316;
      --prgh:    4px;
    }
    [${ACTIVE_ATTR}].sedi-dark {
      --bg:      #0d0d0d;
      --bg2:     #141414;
      --bg3:     #1a1a1a;
      --fg:      #e0e0e0;
      --fg2:     #b0b0b0;
      --fgm:     #737373;
      --fgf:     #525252;
      --bd:      #2a2a2a;
      --bds:     #1f1f1f;
      --ac:      #6b73e8;
      --ach:     #8b91f0;
      --prg:     #6b73e8;
      --prgh:    3px;
    }
    [${ACTIVE_ATTR}].sedi-true-black {
      --bg:      #000000;
      --bg2:     #0a0a0a;
      --bg3:     #111111;
      --fg:      #e5e5e5;
      --fg2:     #a3a3a3;
      --fgm:     #737373;
      --fgf:     #404040;
      --bd:      #262626;
      --bds:     #171717;
      --ac:      #6b73e8;
      --ach:     #8b91f0;
      --prg:     #6b73e8;
      --prgh:    3px;
    }

    /* ── Page reset ── */
    [${ACTIVE_ATTR}] body {
      margin: 0 !important;
      padding: 0 !important;
      background: var(--bg) !important;
      color: var(--fg) !important;
      font-family: Inter, system-ui, -apple-system, sans-serif !important;
      font-size: 1.0625rem !important;
      line-height: 1.7 !important;
      letter-spacing: 0.01em !important;
      -webkit-font-smoothing: antialiased !important;
    }

    /* ── Text selection ── */
    [${ACTIVE_ATTR}] ::selection {
      background-color: rgba(29, 78, 216, 0.7);
      color: white;
    }
    [${ACTIVE_ATTR}] ::-moz-selection {
      background-color: rgba(29, 78, 216, 0.7);
      color: white;
    }

    /* ── Progress bar ── */
    #__sedi_progress_track__ {
      position: fixed;
      top: 0; left: 0; right: 0;
      height: var(--prgh, 4px);
      background: var(--bds);
      z-index: 1001;
    }
    #__sedi_progress_bar__ {
      height: 100%;
      width: 0%;
      background: var(--prg);
      transition: width 150ms;
    }

    /* ── Sticky navbar ── */
    #__sedi_nav__ {
      position: fixed;
      top: 0; left: 0; right: 0;
      z-index: 1000;
      transform: translateY(0);
      transition: transform 300ms;
      padding-top: var(--prgh, 4px);
    }
    #__sedi_nav__.nav-hidden { transform: translateY(-110%); }
    #__sedi_nav_inner__ {
      max-width: 42rem;
      margin: 0 auto;
      padding: 10px 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    /* ── Navbar buttons — mirror Reader.tsx exactly ── */
    /* rounded-none border border-[color-border] bg-[color-bg-secondary]
       text-[color-text-primary] text-xs px-1.5 py-0.5 hover:border-[color-accent] */
    .sedi-btn {
      font-size: 0.75rem;
      line-height: 1rem;
      letter-spacing: 0.2px;
      padding: 2px 6px;
      border: 1px solid var(--bd);
      background: var(--bg2);
      color: var(--fg);
      cursor: pointer;
      border-radius: 0;
      white-space: nowrap;
      transition: border-color 0.2s, color 0.2s;
      display: flex;
      align-items: center;
    }
    .sedi-btn:hover { border-color: var(--ac); }

    /* Icon-only nav button (theme toggle) — p-2 text-[color-text-muted] */
    .sedi-icon-btn {
      background: transparent;
      border: none;
      padding: 8px;
      cursor: pointer;
      color: var(--fgm);
      display: flex;
      align-items: center;
      justify-content: center;
      transition: color 0.2s;
    }
    .sedi-icon-btn:hover { color: var(--fg); }

    /* Font size A/A/A buttons — w-5 h-5 / w-6 h-6, no letter-spacing */
    #__sedi_font_controls__ {
      display: flex;
      align-items: center;
      gap: 2px;
    }
    .sedi-sz-btn {
      background: var(--bg2);
      border: 1px solid var(--bd);
      color: var(--fgm);
      cursor: pointer;
      width: 22px;
      height: 22px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s, color 0.15s;
      border-radius: 0;
    }
    .sedi-sz-btn.active {
      background: var(--bd);
      color: var(--fg);
    }
    .sedi-sz-btn:hover { color: var(--fg); }

    /* Save button — same style as esc/nav buttons */
    #__sedi_save__ {
      font-size: 0.75rem;
      line-height: 1rem;
      letter-spacing: 0.2px;
      padding: 2px 6px;
      border: 1px solid var(--bd);
      background: var(--bg2);
      color: var(--fg);
      cursor: pointer;
      border-radius: 0;
      white-space: nowrap;
      transition: border-color 0.2s, color 0.2s;
      display: flex;
      align-items: center;
    }
    #__sedi_save__:hover:not(:disabled) { border-color: var(--ac); }
    #__sedi_save__:disabled { opacity: 0.5; cursor: not-allowed; }
    #__sedi_save__.saved { border-color: #2e7d52 !important; color: #2e7d52 !important; }

    /* Nav right cluster */
    #__sedi_nav_right__ {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    /* ── TOC sidebar — mirrors Reader.tsx exactly ── */
    /* font-mono tracking-tighter gap-1.5, opacity-based active state */
    #__sedi_toc__ {
      display: none;
      position: fixed;
      left: 2rem;
      top: 8rem;
      width: 16rem;
      max-height: calc(100vh - 16rem);
      overflow-y: auto;
      z-index: 20;
      scrollbar-width: none;
    }
    @media (min-width: 1280px) { #__sedi_toc__ { display: block; } }
    #__sedi_toc__::-webkit-scrollbar { display: none; }
    #__sedi_toc__ nav {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 16px;
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
      letter-spacing: -0.05em;
    }
    #__sedi_toc__ a {
      font-size: 0.9rem;
      font-weight: 400;
      line-height: 1.2;
      color: #6b7280;
      text-decoration: none;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      transition: all 500ms ease, opacity 500ms ease;
      display: block;
      padding: 2px 0;
      opacity: 0.8;
    }
    #__sedi_toc__ a:hover { color: var(--fg); opacity: 1; }
    #__sedi_toc__ a.active {
      color: var(--ac) !important;
      font-weight: 500;
      transform: translateX(4px);
      opacity: 1;
    }

    /* ── Layout ── */
    #__sedi_reader__ {
      max-width: 42rem;
      margin: 0 auto;
      padding: 72px 24px 120px;
    }

    /* ── Article header ── */
    #__sedi_meta__ {
      margin-bottom: 36px;
      padding-bottom: 24px;
      border-bottom: 1px solid var(--bd);
    }
    #__sedi_title__ {
      font-family: "Libre Caslon Text", Georgia, serif;
      font-size: 2.25rem;
      font-weight: 400;
      line-height: 1.2;
      letter-spacing: -0.02em;
      color: var(--fg);
      margin: 0 0 10px;
    }
    #__sedi_byline__ {
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
      font-size: 11px;
      color: var(--fgm);
      letter-spacing: 0.08em;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
    }
    #__sedi_byline__ span { display: flex; align-items: center; gap: 4px; }
    #__sedi_byline__ a { color: var(--ac); text-decoration: none; }
    #__sedi_byline__ a:hover { text-decoration: underline; }

    /* ── Body typography ── */
    #reader-content {
      font-family: Inter, system-ui, -apple-system, sans-serif;
      font-size: 1.0625rem;
      line-height: 1.7;
      letter-spacing: 0.01em;
      color: var(--fg2);
    }
    #reader-content p {
      margin-bottom: 1.5em !important;
      text-align: left !important;
      hyphens: auto !important;
      -webkit-hyphens: auto !important;
    }
    #reader-content h2 {
      font-family: "Libre Caslon Text", Georgia, serif !important;
      font-size: 1.75rem !important;
      font-weight: 500 !important;
      letter-spacing: -0.02em !important;
      color: var(--fg) !important;
      margin-top: 2.5rem !important;
      margin-bottom: 1rem !important;
      line-height: 1.3 !important;
      scroll-margin-top: 5rem !important;
    }
    #reader-content h3 {
      font-family: "Libre Caslon Text", Georgia, serif !important;
      font-size: 1.4rem !important;
      font-weight: 500 !important;
      letter-spacing: -0.01em !important;
      color: var(--fg) !important;
      margin-top: 2rem !important;
      margin-bottom: 0.75rem !important;
      line-height: 1.3 !important;
      scroll-margin-top: 5rem !important;
    }
    #reader-content h4 {
      font-family: "Libre Caslon Text", Georgia, serif !important;
      font-size: 1.15rem !important;
      font-weight: 500 !important;
      letter-spacing: 0 !important;
      color: var(--fg) !important;
      margin-top: 1.5rem !important;
      margin-bottom: 0.5rem !important;
      line-height: 1.4 !important;
      scroll-margin-top: 5rem !important;
    }
    #reader-content > h2:first-child,
    #reader-content > h3:first-child,
    #reader-content > h4:first-child { margin-top: 0 !important; }
    #reader-content h2 + p,
    #reader-content h3 + p,
    #reader-content h4 + p { margin-top: 0 !important; }
    #reader-content a {
      color: var(--ac) !important;
      text-decoration: underline !important;
      text-decoration-thickness: 1px !important;
      text-underline-offset: 2px !important;
    }
    #reader-content a:hover { opacity: 0.7 !important; }
    #reader-content strong, #reader-content b {
      font-weight: 600 !important;
      color: var(--fg) !important;
    }
    #reader-content em, #reader-content i { font-style: italic !important; }
    #reader-content ul, #reader-content ol {
      margin-top: 1em !important;
      margin-bottom: 1.5em !important;
      padding-left: 1.75em !important;
    }
    #reader-content ul { list-style-type: disc !important; }
    #reader-content ol { list-style-type: decimal !important; }
    #reader-content li {
      margin-bottom: 0.5em !important;
      padding-left: 0.25em !important;
      line-height: 1.7 !important;
    }
    #reader-content blockquote {
      margin: 1.5em 0 !important;
      padding: 0.75em 1.25em !important;
      border-left: 3px solid var(--ac) !important;
      background: var(--bg2) !important;
      font-style: italic !important;
      color: var(--fg2) !important;
    }
    #reader-content blockquote p { margin-bottom: 0.75em !important; }
    #reader-content blockquote p:last-child { margin-bottom: 0 !important; }
    #reader-content pre {
      margin: 1.5em 0 !important;
      padding: 1em 1.25em !important;
      background: var(--bg2) !important;
      border: 1px solid var(--bd) !important;
      border-radius: 4px !important;
      overflow-x: auto !important;
      font-family: 'Courier New', monospace !important;
      font-size: 0.9em !important;
    }
    #reader-content code {
      font-family: 'Courier New', monospace !important;
      font-size: 0.9em !important;
      background: var(--bg2) !important;
      padding: 0.2em 0.4em !important;
      border-radius: 3px !important;
    }
    #reader-content pre code { background: transparent !important; padding: 0 !important; }
    #reader-content figure { margin: 2em 0 !important; text-align: center !important; }
    #reader-content img {
      max-width: 100% !important;
      height: auto !important;
      border-radius: 4px !important;
      display: block !important;
      margin: 0 auto !important;
    }
    #reader-content figcaption {
      font-family: 'Courier New', monospace !important;
      font-size: 0.8em !important;
      color: var(--fgm) !important;
      margin-top: 0.5em !important;
    }
    #reader-content hr {
      border: none !important;
      border-top: 1px solid var(--bd) !important;
      margin: 2.5em 0 !important;
    }

    /* ── Font size overrides ── */
    .sedi-sz-small #reader-content { font-size: 0.9375rem !important; line-height: 1.6 !important; }
    .sedi-sz-small #reader-content p { font-size: 0.9375rem !important; line-height: 1.6 !important; }
    .sedi-sz-small #reader-content h2 { font-size: 1.5rem !important; line-height: 1.2 !important; }
    .sedi-sz-small #reader-content h3 { font-size: 1.2rem !important; line-height: 1.2 !important; }
    .sedi-sz-small #reader-content h4 { font-size: 1.05rem !important; line-height: 1.2 !important; }
    .sedi-sz-large #reader-content { font-size: 1.1875rem !important; line-height: 1.8 !important; }
    .sedi-sz-large #reader-content p { font-size: 1.1875rem !important; line-height: 1.8 !important; }
    .sedi-sz-large #reader-content h2 { font-size: 1.95rem !important; line-height: 1.2 !important; }
    .sedi-sz-large #reader-content h3 { font-size: 1.55rem !important; line-height: 1.2 !important; }
    .sedi-sz-large #reader-content h4 { font-size: 1.3rem !important; line-height: 1.2 !important; }

    /* ── Fade in ── */
    @keyframes __sedi_in__ {
      from { opacity: 0; transform: translateY(4px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    [${ACTIVE_ATTR}] body { animation: __sedi_in__ 0.15s ease; }
  `;
  document.head.appendChild(style);

  // ── Apply theme + size class to <html> ────────────────────────────────────
  function applyTheme(t) {
    document.documentElement.classList.remove('sedi-dark', 'sedi-true-black');
    if (t === 'dark') document.documentElement.classList.add('sedi-dark');
    if (t === 'true-black') document.documentElement.classList.add('sedi-true-black');
  }
  function applySize(s) {
    document.documentElement.classList.remove('sedi-sz-small', 'sedi-sz-large');
    if (s === 'small') document.documentElement.classList.add('sedi-sz-small');
    if (s === 'large') document.documentElement.classList.add('sedi-sz-large');
  }
  applyTheme(currentTheme);
  applySize(currentSize);

  // ── Build reader body ──────────────────────────────────────────────────────
  const newBody = document.createElement('body');

  // Progress bar
  const progressTrack = document.createElement('div');
  progressTrack.id = '__sedi_progress_track__';
  const progressBar = document.createElement('div');
  progressBar.id = '__sedi_progress_bar__';
  progressTrack.appendChild(progressBar);
  newBody.appendChild(progressTrack);

  // ── Navbar ────────────────────────────────────────────────────────────────
  const nav = document.createElement('div');
  nav.id = '__sedi_nav__';

  const navInner = document.createElement('div');
  navInner.id = '__sedi_nav_inner__';

  // Left: esc button
  const escBtn = document.createElement('button');
  escBtn.className = 'sedi-btn';
  escBtn.textContent = '← esc';
  escBtn.addEventListener('click', toggle);
  navInner.appendChild(escBtn);

  // Right: font size + theme toggle + save
  const navRight = document.createElement('div');
  navRight.id = '__sedi_nav_right__';

  // Font size buttons
  const fontControls = document.createElement('div');
  fontControls.id = '__sedi_font_controls__';

  const sizeLabels = [
    { s: 'small',  label: 'A', textSize: '10px' },
    { s: 'medium', label: 'A', textSize: '13px' },
    { s: 'large',  label: 'A', textSize: '16px' },
  ];
  sizeLabels.forEach(({ s, label, textSize }) => {
    const btn = document.createElement('button');
    btn.className = 'sedi-sz-btn' + (currentSize === s ? ' active' : '');
    btn.dataset.size = s;
    btn.title = `${s.charAt(0).toUpperCase() + s.slice(1)} text`;
    const span = document.createElement('span');
    span.style.fontSize = textSize;
    span.style.fontFamily = 'Inter, system-ui, -apple-system, sans-serif';
    span.style.fontWeight = '500';
    span.textContent = label;
    btn.appendChild(span);
    btn.addEventListener('click', () => {
      currentSize = s;
      try { sessionStorage.setItem(SIZE_KEY, s); } catch {}
      applySize(s);
      fontControls.querySelectorAll('.sedi-sz-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.size === s);
      });
      updateProgress();
    });
    fontControls.appendChild(btn);
  });
  navRight.appendChild(fontControls);

  // Theme toggle
  const themeBtn = document.createElement('button');
  themeBtn.className = 'sedi-icon-btn';
  themeBtn.title = 'Toggle theme';
  function themeIcon(t) {
    if (t === 'light') {
      // Moon (→ dark)
      return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>`;
    } else if (t === 'dark') {
      // Filled circle (→ true-black)
      return `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="9"/></svg>`;
    } else {
      // Sun (→ light)
      return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>`;
    }
  }
  themeBtn.innerHTML = themeIcon(currentTheme);
  themeBtn.addEventListener('click', () => {
    const idx = THEMES.indexOf(currentTheme);
    currentTheme = THEMES[(idx + 1) % THEMES.length];
    try { sessionStorage.setItem(THEME_KEY, currentTheme); } catch {}
    applyTheme(currentTheme);
    themeBtn.innerHTML = themeIcon(currentTheme);
  });
  navRight.appendChild(themeBtn);

  // Save button
  const saveBtn = document.createElement('button');
  saveBtn.id = '__sedi_save__';
  saveBtn.textContent = 'Save to sed.i';
  saveBtn.addEventListener('click', () => handleSave(saveBtn, article));
  navRight.appendChild(saveBtn);

  navInner.appendChild(navRight);
  nav.appendChild(navInner);
  newBody.appendChild(nav);

  // TOC sidebar
  const tocEl = document.createElement('div');
  tocEl.id = '__sedi_toc__';
  const tocNav = document.createElement('nav');
  tocEl.appendChild(tocNav);
  newBody.appendChild(tocEl);

  // Reader container
  const container = document.createElement('div');
  container.id = '__sedi_reader__';

  // Header: title + byline
  const meta = document.createElement('div');
  meta.id = '__sedi_meta__';

  const titleEl = document.createElement('h1');
  titleEl.id = '__sedi_title__';
  titleEl.textContent = article.title || document.title;
  meta.appendChild(titleEl);

  const wordCount = (article.html || '').replace(/<[^>]+>/g, ' ').trim().split(/\s+/).filter(Boolean).length;
  const readMins = Math.max(1, Math.round(wordCount / 200));
  const byline = document.createElement('div');
  byline.id = '__sedi_byline__';

  const bylineParts = [];
  if (article.author) bylineParts.push(article.author);
  bylineParts.push(`${readMins} min read`);
  if (article.publishedDate) {
    try {
      const d = new Date(article.publishedDate);
      if (!isNaN(d.getTime())) {
        bylineParts.push(d.toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' }));
      }
    } catch {}
  }

  bylineParts.forEach((part, i) => {
    if (i > 0) {
      const dot = document.createElement('span');
      dot.textContent = '·';
      dot.style.color = 'var(--bd)';
      byline.appendChild(dot);
    }
    const span = document.createElement('span');
    span.textContent = part;
    byline.appendChild(span);
  });

  if (article.url) {
    try {
      const dot = document.createElement('span');
      dot.textContent = '·';
      dot.style.color = 'var(--bd)';
      byline.appendChild(dot);
      const link = document.createElement('a');
      link.href = article.url;
      link.textContent = '↗ ' + new URL(article.url).hostname.replace(/^www\./, '');
      link.target = '_blank';
      link.rel = 'noopener';
      byline.appendChild(link);
    } catch {}
  }

  meta.appendChild(byline);
  container.appendChild(meta);

  // Article body
  const articleBody = document.createElement('div');
  articleBody.id = 'reader-content';
  articleBody.innerHTML = article.html;
  container.appendChild(articleBody);

  newBody.appendChild(container);
  document.body.replaceWith(newBody);

  // ── Progress bar update ───────────────────────────────────────────────────
  const updateProgress = () => {
    const docHeight = document.documentElement.scrollHeight - window.innerHeight;
    const pct = docHeight > 0 ? Math.min(100, (window.scrollY / docHeight) * 100) : 0;
    progressBar.style.width = pct + '%';
  };

  // ── Navbar auto-hide ──────────────────────────────────────────────────────
  let lastScrollY = window.scrollY;
  const updateNav = () => {
    const delta = window.scrollY - lastScrollY;
    if (delta > 10 && window.scrollY > 100) nav.classList.add('nav-hidden');
    else if (delta < -10 || window.scrollY < 50) nav.classList.remove('nav-hidden');
    lastScrollY = window.scrollY;
    updateProgress();
  };
  window.addEventListener('scroll', updateNav, { passive: true });

  // ── Build TOC ─────────────────────────────────────────────────────────────
  const headingEls = Array.from(articleBody.querySelectorAll('h2, h3, h4'));
  const titleNorm  = (article.title || '').toLowerCase().trim();
  const seenIds    = new Map();
  const tocHeadings = [];

  headingEls.forEach(h => {
    const text = h.textContent?.trim() || '';
    if (text.toLowerCase() === titleNorm) return;
    let id = h.id;
    if (!id && text) id = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    if (!id || !text) return;
    const count = seenIds.get(id) ?? 0;
    seenIds.set(id, count + 1);
    const uniqueId = count === 0 ? id : `${id}-${count + 1}`;
    h.id = uniqueId;
    tocHeadings.push({ id: uniqueId, text, level: parseInt(h.tagName[1]) });
  });

  if (tocHeadings.length > 1) {
    const minLevel = Math.min(...tocHeadings.map(h => h.level));
    const maxLevel = Math.max(...tocHeadings.map(h => h.level));
    const range = maxLevel - minLevel + 1;
    tocHeadings.forEach(h => {
      h.level = range > 3
        ? 2 + Math.min(2, h.level - minLevel)
        : h.level - (minLevel - 2);
    });
  }

  tocHeadings.forEach(h => {
    const a = document.createElement('a');
    a.href = `#${h.id}`;
    a.textContent = h.text;
    a.style.paddingLeft = `${Math.max(0, h.level - 2) * 12}px`;
    a.style.fontSize = h.level === 2 ? '0.88rem' : '0.82rem';
    a.addEventListener('click', e => {
      e.preventDefault();
      const el = document.getElementById(h.id);
      if (el) {
        const top = el.getBoundingClientRect().top + window.scrollY - window.innerHeight * 0.3;
        window.scrollTo({ top, behavior: 'smooth' });
      }
    });
    tocNav.appendChild(a);
  });

  // Scroll spy
  if (tocHeadings.length > 0) {
    const spy = () => {
      const threshold = window.scrollY + window.innerHeight * 0.35;
      let activeId = tocHeadings[0].id;
      for (const h of tocHeadings) {
        const el = document.getElementById(h.id);
        if (el && el.getBoundingClientRect().top + window.scrollY <= threshold) activeId = h.id;
      }
      tocNav.querySelectorAll('a').forEach(a => {
        a.classList.toggle('active', a.getAttribute('href') === `#${activeId}`);
      });
    };
    window.addEventListener('scroll', spy, { passive: true });
    spy();
    window.__sediSpyCleanup__ = () => {
      window.removeEventListener('scroll', spy);
      window.removeEventListener('scroll', updateNav);
    };
  } else {
    window.__sediSpyCleanup__ = () => window.removeEventListener('scroll', updateNav);
  }

  // ── Esc to exit ───────────────────────────────────────────────────────────
  window.__sediEscHandler__ = (e) => { if (e.key === 'Escape') toggle(); };
  document.addEventListener('keydown', window.__sediEscHandler__);

  // ── Helpers ───────────────────────────────────────────────────────────────

  function toggle() {
    document.documentElement.removeAttribute(ACTIVE_ATTR);
    document.documentElement.classList.remove('sedi-dark', 'sedi-true-black', 'sedi-sz-small', 'sedi-sz-large');
    const saved = window.__sediOriginalBody__;
    if (saved) {
      document.body.replaceWith(saved);
      window.__sediOriginalBody__ = null;
    }
    document.getElementById('__sedi_style__')?.remove();
    document.removeEventListener('keydown', window.__sediEscHandler__);
    window.__sediSpyCleanup__?.();
    window.__sediSpyCleanup__ = null;
  }

  function handleSave(btn, art) {
    btn.disabled = true;
    btn.textContent = 'Saving...';
    chrome.runtime.sendMessage({
      action: 'saveContent',
      payload: {
        url:           art.url,
        html:          art.html,
        title:         art.title,
        author:        art.author,
        description:   art.description,
        thumbnail:     art.thumbnail,
        publishedDate: art.publishedDate,
      },
    }, (resp) => {
      if (resp?.ok) {
        btn.textContent = 'Saved ✓';
        btn.classList.add('saved');
      } else {
        btn.disabled = false;
        btn.textContent = 'Save failed — retry';
      }
    });
  }

})();
