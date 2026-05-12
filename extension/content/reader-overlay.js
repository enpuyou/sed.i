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
  const ACTIVE_ATTR   = 'data-sedi-reader-active';
  const THEME_KEY     = '__sedi_theme__';
  const SETTINGS_KEY  = '__sedi_reader_settings__';

  // ── Toggle off if already active ──────────────────────────────────────────
  if (document.documentElement.hasAttribute(ACTIVE_ATTR)) {
    document.documentElement.removeAttribute(ACTIVE_ATTR);
    document.documentElement.classList.remove(
      'sedi-dark', 'sedi-sz-small', 'sedi-sz-large',
      'sedi-lh-compact', 'sedi-lh-spacious', 'sedi-ls-tight', 'sedi-ls-wide',
      'sedi-w-narrow', 'sedi-w-wide', 'sedi-font-serif'
    );
    const saved = window.__sediOriginalBody__;
    if (saved) { document.body.replaceWith(saved); window.__sediOriginalBody__ = null; }
    document.getElementById('__sedi_style__')?.remove();
    document.removeEventListener('keydown', window.__sediEscHandler__);
    window.__sediSpyCleanup__?.();
    window.__sediSpyCleanup__ = null;
    return;
  }

  const article = window.__sediArticle__;
  if (!article) return;

  // ── Persist theme + reading settings ──────────────────────────────────────
  let currentTheme = 'light';
  try { const t = sessionStorage.getItem(THEME_KEY); if (t === 'dark' || t === 'light') currentTheme = t; } catch {}

  const DEF = { size: 'medium', font: 'sans', lineHeight: 'comfortable', letterSpacing: 'normal', width: 'medium' };
  let rs = { ...DEF };
  try { const s = sessionStorage.getItem(SETTINGS_KEY); if (s) rs = { ...DEF, ...JSON.parse(s) }; } catch {}
  function saveRS() { try { sessionStorage.setItem(SETTINGS_KEY, JSON.stringify(rs)); } catch {} }

  // ── Save original body ─────────────────────────────────────────────────────
  window.__sediOriginalBody__ = document.body;
  document.documentElement.setAttribute(ACTIVE_ATTR, '');

  // ── CSS ────────────────────────────────────────────────────────────────────
  const style = document.createElement('style');
  style.id = '__sedi_style__';
  style.textContent = `
    /* Hard-reset html + body so page styles can't leak in */
    [${ACTIVE_ATTR}],
    [${ACTIVE_ATTR}] body {
      all:initial!important;
      display:block!important;
    }
    [${ACTIVE_ATTR}] {
      --bg:#fffef7; --bg2:#faf9f5; --fg:#1a1a1a; --fg2:#4a4a4a; --fgm:#666666; --fgt:#999999;
      --bd:#e5e4e0; --bds:#eeede9; --ac:#3d46c2; --ach:#2d36b2;
      --prg:#f97316; --prgh:4px; --rmw:42rem;
    }
    [${ACTIVE_ATTR}].sedi-dark {
      --bg:#0d0d0d; --bg2:#141414; --fg:#e0e0e0; --fg2:#b0b0b0; --fgm:#737373; --fgt:#525252;
      --bd:#2a2a2a; --bds:#1f1f1f; --ac:#6b73e8; --ach:#8b91f0;
      --prg:#6b73e8; --prgh:3px;
    }
    [${ACTIVE_ATTR}].sedi-w-narrow { --rmw:32rem; }
    [${ACTIVE_ATTR}].sedi-w-wide   { --rmw:48rem; }
    [${ACTIVE_ATTR}] body {
      margin:0!important; padding:0!important;
      background:var(--bg)!important; color:var(--fg)!important;
      font-family:Inter,system-ui,-apple-system,sans-serif!important;
      font-size:1.0625rem!important; line-height:1.7!important;
      letter-spacing:0.01em!important; -webkit-font-smoothing:antialiased!important;
      box-sizing:border-box!important;
    }
    [${ACTIVE_ATTR}] ::selection { background:rgba(29,78,216,0.7); color:#fff; }
    [${ACTIVE_ATTR}] ::-moz-selection { background:rgba(29,78,216,0.7); color:#fff; }

    /* Progress */
    #__sedi_pt__ { position:fixed;top:0;left:0;right:0;height:var(--prgh);background:var(--bds);z-index:1001; }
    #__sedi_pb__ { height:100%;width:0%;background:var(--prg);transition:width 150ms; }

    /* Navbar */
    #__sedi_nav__ { position:fixed;top:0;left:0;right:0;z-index:1000;transform:translateY(0);transition:transform 300ms;padding-top:var(--prgh); }
    #__sedi_nav__.nav-hidden { transform:translateY(-110%); }
    #__sedi_navi__ { max-width:var(--rmw);margin:0 auto;padding:10px 16px;display:flex;align-items:center;justify-content:space-between;gap:8px; }

    /* Nav buttons */
    .sedi-btn {
      font-size:0.75rem;line-height:1rem;letter-spacing:0.2px;
      padding:2px 8px;border:1px solid var(--bd);background:var(--bg2);color:var(--fg);
      cursor:pointer;border-radius:0;white-space:nowrap;
      transition:border-color 0.2s,color 0.2s;display:flex;align-items:center;gap:5px;
    }
    .sedi-btn:hover { border-color:var(--ac); }
    .sedi-btn.active { border-color:var(--ac);color:var(--ac); }
    .sedi-icon-btn { background:transparent;border:none;padding:8px;cursor:pointer;color:var(--fgm);display:flex;align-items:center;transition:color 0.2s; }
    .sedi-icon-btn:hover { color:var(--fg); }
    #__sedi_navr__ { display:flex;align-items:center;gap:6px; }

    /* TOC — mirrors Reader.tsx exactly */
    #__sedi_toc__ {
      display:none; position:fixed; left:2rem; top:8rem;
      width:16rem; max-height:calc(100vh - 16rem); overflow-y:auto;
      z-index:20; scrollbar-width:none;
      opacity:0; animation:__sedi_toc_in__ 0.5s ease 0.5s forwards;
    }
    @keyframes __sedi_toc_in__ { to { opacity:1; } }
    @media (min-width:1280px) { #__sedi_toc__ { display:block; } }
    #__sedi_toc__::-webkit-scrollbar { display:none; }
    #__sedi_toc__ nav {
      display:flex;flex-direction:column;gap:6px;margin-top:16px;
      font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
      letter-spacing:-0.05em;
    }
    /* Base: all links muted, truncated */
    #__sedi_toc__ a {
      font-size:0.9rem; font-weight:400; line-height:1.2;
      color:#6b7280; text-decoration:none;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
      display:block; padding:2px 0; opacity:0.8;
      transition:all 500ms ease, opacity 500ms ease;
    }
    #__sedi_toc__ a:hover { color:var(--fg); opacity:1; }
    /* Active: accent, bold, translated, full opacity */
    #__sedi_toc__ a.active {
      color:var(--ac)!important; font-weight:500;
      transform:translateX(4px); opacity:1;
    }
    /* Idle mode: non-active links fade out */
    #__sedi_toc__.idle a:not(.active) { opacity:0; pointer-events:none; }
    /* Idle + active: allow wrapping */
    #__sedi_toc__.idle a.active { white-space:normal; word-break:break-word; line-height:1.4; }

    /* Layout */
    #__sedi_reader__ { max-width:var(--rmw);margin:0 auto;padding:72px 24px 120px; }

    /* Header */
    #__sedi_meta__ { margin-bottom:3rem;padding-bottom:1rem;border-bottom:1px solid var(--bd); }
    #__sedi_title__ {
      font-family:"Libre Caslon Text",Georgia,serif;font-size:2.25rem;font-weight:400;
      line-height:1.2;letter-spacing:-0.02em;color:var(--fg);margin:0 0 0.75rem;
    }
    /* Byline: font-mono text-xs tracking-tight — matches app exactly */
    #__sedi_byline__ {
      font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace;
      font-size:0.75rem;letter-spacing:-0.025em;line-height:1.5;
      display:flex;flex-direction:column;gap:0;
    }
    /* Zone 2: attribution — single flex-wrap row; multi-author gets basis-full to push date to next wrapped line */
    .sedi-z2 {
      display:flex;flex-wrap:wrap;align-items:baseline;column-gap:8px;row-gap:2px;
      color:var(--fgm);
    }
    .sedi-author { color:var(--fg2); }
    .sedi-author.multi { flex-basis:100%; }
    .sedi-faint { color:var(--fgt); }
    /* Zone 3: reader info */
    .sedi-z3 {
      padding-top:4px;
      display:flex;flex-wrap:wrap;align-items:center;column-gap:8px;row-gap:2px;
      color:var(--fgt);
    }
    #__sedi_byline__ a { color:var(--ac);text-decoration:none; }
    #__sedi_byline__ a:hover { text-decoration:underline; }

    /* Body */
    #reader-content { font-family:Inter,system-ui,-apple-system,sans-serif;font-size:1.0625rem;line-height:1.7;letter-spacing:0.01em;color:var(--fg2); }
    #reader-content p { margin-bottom:1.5em!important;hyphens:auto!important;-webkit-hyphens:auto!important;text-rendering:optimizeLegibility!important; }
    #reader-content h2 { font-family:"Libre Caslon Text",Georgia,serif!important;font-size:1.75rem!important;font-weight:500!important;letter-spacing:-0.02em!important;color:var(--fg)!important;margin-top:2.5rem!important;margin-bottom:1rem!important;line-height:1.3!important;scroll-margin-top:5rem!important; }
    #reader-content h3 { font-family:"Libre Caslon Text",Georgia,serif!important;font-size:1.4rem!important;font-weight:500!important;letter-spacing:-0.01em!important;color:var(--fg)!important;margin-top:2rem!important;margin-bottom:0.75rem!important;line-height:1.3!important;scroll-margin-top:5rem!important; }
    #reader-content h4 { font-family:"Libre Caslon Text",Georgia,serif!important;font-size:1.15rem!important;font-weight:500!important;letter-spacing:0!important;color:var(--fg)!important;margin-top:1.5rem!important;margin-bottom:0.5rem!important;line-height:1.4!important;scroll-margin-top:5rem!important; }
    #reader-content > h2:first-child,#reader-content > h3:first-child,#reader-content > h4:first-child { margin-top:0!important; }
    #reader-content h2+p,#reader-content h3+p,#reader-content h4+p { margin-top:0!important; }
    #reader-content a { color:var(--ac)!important;text-decoration:underline!important;text-decoration-thickness:1px!important;text-underline-offset:2px!important; }
    #reader-content a:hover { opacity:0.7!important; }
    #reader-content strong,#reader-content b { font-weight:600!important;color:var(--fg)!important; }
    #reader-content em,#reader-content i { font-style:italic!important; }
    #reader-content ul,#reader-content ol { margin-top:1em!important;margin-bottom:1.5em!important;padding-left:1.75em!important; }
    #reader-content ul { list-style-type:disc!important; }
    #reader-content ol { list-style-type:decimal!important; }
    #reader-content li { margin-bottom:0.5em!important;line-height:1.7!important; }
    #reader-content blockquote { margin:1.5em 0!important;padding:0.75em 1.25em!important;border-left:3px solid var(--ac)!important;background:var(--bg2)!important;font-style:italic!important;color:var(--fg2)!important; }
    #reader-content blockquote p { margin-bottom:0.75em!important; }
    #reader-content blockquote p:last-child { margin-bottom:0!important; }
    #reader-content pre { margin:1.5em 0!important;padding:1em 1.25em!important;background:var(--bg2)!important;border:1px solid var(--bd)!important;border-radius:4px!important;overflow-x:auto!important;font-family:ui-monospace,monospace!important;font-size:0.9em!important; }
    #reader-content code { font-family:ui-monospace,monospace!important;font-size:0.9em!important;background:var(--bg2)!important;padding:0.2em 0.4em!important;border-radius:3px!important; }
    #reader-content pre code { background:transparent!important;padding:0!important; }
    #reader-content figure { margin:2em 0!important;text-align:left!important; }
    #reader-content img { max-width:100%!important;height:auto!important;border-radius:4px!important;display:block!important; }
    #reader-content figcaption { font-family:ui-monospace,monospace!important;font-size:0.8em!important;color:var(--fgm)!important;margin-top:0.5em!important;text-align:left!important; }
    #reader-content hr { border:none!important;border-top:1px solid var(--bd)!important;margin:2.5em 0!important; }

    /* Font size */
    .sedi-sz-small #reader-content,.sedi-sz-small #reader-content p { font-size:0.9375rem!important;line-height:1.6!important; }
    .sedi-sz-small #reader-content h2 { font-size:1.5rem!important; }
    .sedi-sz-small #reader-content h3 { font-size:1.2rem!important; }
    .sedi-sz-small #reader-content h4 { font-size:1.05rem!important; }
    .sedi-sz-large #reader-content,.sedi-sz-large #reader-content p { font-size:1.1875rem!important;line-height:1.8!important; }
    .sedi-sz-large #reader-content h2 { font-size:1.95rem!important; }
    .sedi-sz-large #reader-content h3 { font-size:1.55rem!important; }
    .sedi-sz-large #reader-content h4 { font-size:1.3rem!important; }

    /* Font family */
    .sedi-font-serif #reader-content,.sedi-font-serif #reader-content p,.sedi-font-serif #reader-content li {
      font-family:"Libre Caslon Text",Georgia,serif!important;
    }

    /* Line height */
    .sedi-lh-compact #reader-content p,.sedi-lh-compact #reader-content li { line-height:1.4!important; }
    .sedi-lh-spacious #reader-content p,.sedi-lh-spacious #reader-content li { line-height:2!important; }

    /* Letter spacing */
    .sedi-ls-tight #reader-content p,.sedi-ls-tight #reader-content li { letter-spacing:-0.01em!important; }
    .sedi-ls-wide #reader-content p,.sedi-ls-wide #reader-content li { letter-spacing:0.05em!important; }

    /* Focus mode — dim all paragraphs, highlight only the one at 30% viewport */
    #reader-content p[data-para-index],
    #reader-content h2[data-para-index],
    #reader-content h3[data-para-index],
    #reader-content h4[data-para-index],
    #reader-content blockquote[data-para-index],
    #reader-content ul[data-para-index],
    #reader-content ol[data-para-index] { transition:opacity 0.4s ease; }
    .sedi-focus-active #reader-content p,
    .sedi-focus-active #reader-content h2,
    .sedi-focus-active #reader-content h3,
    .sedi-focus-active #reader-content h4,
    .sedi-focus-active #reader-content blockquote,
    .sedi-focus-active #reader-content ul,
    .sedi-focus-active #reader-content ol { opacity:0.15; }
    .sedi-focus-active #reader-content [data-para-index].focused { opacity:1; color:var(--fg)!important; }
    .sedi-focus-active #reader-content [data-para-index].near-focused { opacity:0.3; }

    /* Settings panel */
    #__sedi_sp__ {
      position:fixed;bottom:52px;right:20px;z-index:998;
      background:var(--bg2);border:1px solid var(--bd);
      padding:12px;width:230px;
      display:flex;flex-direction:column;gap:10px;
      transform-origin:bottom right;
      transform:scale(0.95);opacity:0;pointer-events:none;
      transition:transform 0.15s,opacity 0.15s;
    }
    #__sedi_sp__.open { transform:scale(1);opacity:1;pointer-events:auto; }
    #__sedi_sb__ {
      position:fixed;bottom:18px;right:20px;z-index:999;
      background:var(--bg2);border:1px solid var(--bd);color:var(--fgm);
      cursor:pointer;padding:5px 7px;
      transition:border-color 0.2s,color 0.2s;display:flex;align-items:center;
    }
    #__sedi_sb__:hover,#__sedi_sb__.open { border-color:var(--ac);color:var(--ac); }

    .sp-row { display:flex;flex-direction:column;gap:5px; }
    .sp-label {
      font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
      font-size:9px;text-transform:uppercase;letter-spacing:0.1em;
      color:var(--fgm);font-weight:600;
    }
    .sp-opts { display:flex;gap:3px;flex-wrap:nowrap; }
    .sp-opt {
      font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
      font-size:10px;letter-spacing:0.03em;
      padding:2px 7px;border:1px solid var(--bd);
      background:transparent;color:var(--fgm);
      cursor:pointer;border-radius:0;
      transition:border-color 0.15s,color 0.15s;
    }
    .sp-opt.active { border-color:var(--fg);color:var(--fg); }
    .sp-opt:hover { border-color:var(--ac);color:var(--ac); }

    @keyframes __sedi_in__ { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:translateY(0)} }
    [${ACTIVE_ATTR}] body { animation:__sedi_in__ 0.15s ease; }
  `;
  document.head.appendChild(style);

  // ── Apply classes ─────────────────────────────────────────────────────────
  function applyTheme(t) { document.documentElement.classList.toggle('sedi-dark', t === 'dark'); }
  function applySettings() {
    const h = document.documentElement;
    h.classList.toggle('sedi-sz-small', rs.size === 'small');
    h.classList.toggle('sedi-sz-large', rs.size === 'large');
    h.classList.toggle('sedi-font-serif', rs.font === 'serif');
    h.classList.toggle('sedi-lh-compact', rs.lineHeight === 'compact');
    h.classList.toggle('sedi-lh-spacious', rs.lineHeight === 'spacious');
    h.classList.toggle('sedi-ls-tight', rs.letterSpacing === 'tight');
    h.classList.toggle('sedi-ls-wide', rs.letterSpacing === 'wide');
    h.classList.toggle('sedi-w-narrow', rs.width === 'narrow');
    h.classList.toggle('sedi-w-wide', rs.width === 'wide');
  }
  applyTheme(currentTheme);
  applySettings();

  // ── Build body ────────────────────────────────────────────────────────────
  const newBody = document.createElement('body');

  // Progress bar
  const pt = document.createElement('div'); pt.id = '__sedi_pt__';
  const pb = document.createElement('div'); pb.id = '__sedi_pb__';
  pt.appendChild(pb); newBody.appendChild(pt);

  // Navbar
  const nav = document.createElement('div'); nav.id = '__sedi_nav__';
  const navi = document.createElement('div'); navi.id = '__sedi_navi__';

  // Left: × esc
  const closeBtn = document.createElement('button');
  closeBtn.className = 'sedi-btn';
  closeBtn.innerHTML = `<svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="1" y1="1" x2="9" y2="9"/><line x1="9" y1="1" x2="1" y2="9"/></svg>esc`;
  closeBtn.title = 'Exit reader (Esc)';
  closeBtn.addEventListener('click', toggle);
  navi.appendChild(closeBtn);

  // Right controls
  const navr = document.createElement('div'); navr.id = '__sedi_navr__';

  // Theme toggle (light ↔ dark), left of Focus
  const themeBtn = document.createElement('button');
  themeBtn.className = 'sedi-icon-btn';
  themeBtn.title = 'Toggle theme';
  function moonSVG() { return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>`; }
  function sunSVG()  { return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>`; }
  themeBtn.innerHTML = currentTheme === 'light' ? moonSVG() : sunSVG();
  themeBtn.addEventListener('click', () => {
    currentTheme = currentTheme === 'light' ? 'dark' : 'light';
    try { sessionStorage.setItem(THEME_KEY, currentTheme); } catch {}
    applyTheme(currentTheme);
    themeBtn.innerHTML = currentTheme === 'light' ? moonSVG() : sunSVG();
  });
  navr.appendChild(themeBtn);

  // Focus button
  let focusActive = false;
  const focusBtn = document.createElement('button');
  focusBtn.className = 'sedi-btn';
  focusBtn.textContent = 'Focus';
  focusBtn.title = 'Focus mode — dims non-active paragraphs';
  focusBtn.addEventListener('click', () => {
    focusActive = !focusActive;
    document.documentElement.classList.toggle('sedi-focus-active', focusActive);
    focusBtn.classList.toggle('active', focusActive);
    if (focusActive) setupFocusMode(); else teardownFocusMode();
  });
  navr.appendChild(focusBtn);

  // Save button
  const saveBtn = document.createElement('button');
  saveBtn.className = 'sedi-btn';
  saveBtn.textContent = 'Save to sed.i';
  saveBtn.addEventListener('click', () => handleSave(saveBtn, article));
  navr.appendChild(saveBtn);

  navi.appendChild(navr);
  nav.appendChild(navi);
  newBody.appendChild(nav);

  // TOC
  const tocEl = document.createElement('div'); tocEl.id = '__sedi_toc__';
  const tocNav = document.createElement('nav');
  tocEl.appendChild(tocNav);
  newBody.appendChild(tocEl);

  // Reader container
  const container = document.createElement('div'); container.id = '__sedi_reader__';
  const meta = document.createElement('div'); meta.id = '__sedi_meta__';

  const titleEl = document.createElement('h1'); titleEl.id = '__sedi_title__';
  titleEl.textContent = article.title || document.title;
  meta.appendChild(titleEl);

  const wc = (article.html || '').replace(/<[^>]+>/g, ' ').trim().split(/\s+/).filter(Boolean).length;
  const byline = document.createElement('div'); byline.id = '__sedi_byline__';

  const readTime = `${Math.max(1, Math.round(wc / 200))} min read`;
  const isMultiAuthor = article.author && (article.author.includes(',') || / and /i.test(article.author));

  let formattedDate = '';
  if (article.publishedDate) {
    try { const d = new Date(article.publishedDate); if (!isNaN(d)) formattedDate = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); } catch {}
  }

  // ── Zone 2: attribution (author + published date) ──────────────────────────
  const z2 = document.createElement('div'); z2.className = 'sedi-z2';

  if (article.author || formattedDate) {
    if (article.author) {
      const authorEl = document.createElement('span');
      authorEl.className = isMultiAuthor ? 'sedi-author multi' : 'sedi-author';
      authorEl.textContent = article.author;
      z2.appendChild(authorEl);
    }
    if (formattedDate) {
      // dot separator: only for single author
      if (article.author && !isMultiAuthor) {
        const dot = document.createElement('span'); dot.className = 'sedi-faint'; dot.textContent = '·';
        z2.appendChild(dot);
      }
      const dateEl = document.createElement('span');
      dateEl.textContent = `published ${formattedDate}`;
      z2.appendChild(dateEl);
    }
  }
  if (z2.hasChildNodes()) byline.appendChild(z2);

  // ── Zone 3: reader info (read time · domain) ───────────────────────────────
  const z3 = document.createElement('div'); z3.className = 'sedi-z3';

  const readEl = document.createElement('span'); readEl.textContent = readTime;
  z3.appendChild(readEl);

  if (article.url) {
    try {
      const dot = document.createElement('span'); dot.className = 'sedi-faint'; dot.textContent = '·';
      const a = document.createElement('a'); a.href = article.url;
      a.textContent = '↗ ' + new URL(article.url).hostname.replace(/^www\./, '');
      a.target = '_blank'; a.rel = 'noopener';
      z3.appendChild(dot);
      z3.appendChild(a);
    } catch {}
  }
  byline.appendChild(z3);

  meta.appendChild(byline);
  container.appendChild(meta);

  const articleBody = document.createElement('div'); articleBody.id = 'reader-content';
  const _tmp = document.createElement('div');
  _tmp.innerHTML = article.html;
  _tmp.querySelectorAll('iframe,frame,object,embed,script,link[rel="import"]').forEach(el => el.remove());
  while (_tmp.firstChild) articleBody.appendChild(_tmp.firstChild);
  container.appendChild(articleBody);
  newBody.appendChild(container);

  // ── Settings panel ────────────────────────────────────────────────────────
  const sp = document.createElement('div'); sp.id = '__sedi_sp__';
  const sb = document.createElement('button'); sb.id = '__sedi_sb__';
  sb.title = 'Reader settings';
  sb.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>`;

  function makeRow(label, opts, cur, onChange) {
    const row = document.createElement('div'); row.className = 'sp-row';
    const lbl = document.createElement('span'); lbl.className = 'sp-label'; lbl.textContent = label;
    row.appendChild(lbl);
    const optDiv = document.createElement('div'); optDiv.className = 'sp-opts';
    opts.forEach(({ v, t }) => {
      const btn = document.createElement('button'); btn.className = 'sp-opt' + (cur === v ? ' active' : '');
      btn.dataset.v = v; btn.textContent = t;
      btn.addEventListener('click', () => {
        optDiv.querySelectorAll('.sp-opt').forEach(b => b.classList.toggle('active', b.dataset.v === v));
        onChange(v);
      });
      optDiv.appendChild(btn);
    });
    row.appendChild(optDiv);
    return row;
  }

  sp.appendChild(makeRow('font', [{v:'sans',t:'sans'},{v:'serif',t:'serif'}], rs.font, v => { rs.font=v; saveRS(); applySettings(); }));
  sp.appendChild(makeRow('size', [{v:'small',t:'S'},{v:'medium',t:'M'},{v:'large',t:'L'}], rs.size, v => { rs.size=v; saveRS(); applySettings(); }));
  sp.appendChild(makeRow('line spacing', [{v:'compact',t:'compact'},{v:'comfortable',t:'normal'},{v:'spacious',t:'spacious'}], rs.lineHeight, v => { rs.lineHeight=v; saveRS(); applySettings(); }));
  sp.appendChild(makeRow('letter spacing', [{v:'tight',t:'tight'},{v:'normal',t:'normal'},{v:'wide',t:'wide'}], rs.letterSpacing, v => { rs.letterSpacing=v; saveRS(); applySettings(); }));
  sp.appendChild(makeRow('line width', [{v:'narrow',t:'narrow'},{v:'medium',t:'medium'},{v:'wide',t:'wide'}], rs.width, v => { rs.width=v; saveRS(); applySettings(); }));

  sb.addEventListener('click', e => { e.stopPropagation(); const o = sp.classList.toggle('open'); sb.classList.toggle('open', o); });
  document.addEventListener('click', () => { sp.classList.remove('open'); sb.classList.remove('open'); });
  sp.addEventListener('click', e => e.stopPropagation());

  newBody.appendChild(sp);
  newBody.appendChild(sb);
  document.body.replaceWith(newBody);

  // ── Progress + nav hide ───────────────────────────────────────────────────
  const updateProg = () => {
    const dh = document.documentElement.scrollHeight - window.innerHeight;
    pb.style.width = (dh > 0 ? Math.min(100, (window.scrollY / dh) * 100) : 0) + '%';
  };
  let lastY = window.scrollY;
  const onScroll = () => {
    const d = window.scrollY - lastY;
    if (d > 10 && window.scrollY > 100) nav.classList.add('nav-hidden');
    else if (d < -10 || window.scrollY < 50) nav.classList.remove('nav-hidden');
    lastY = window.scrollY;
    updateProg();
  };
  window.addEventListener('scroll', onScroll, { passive: true });

  // ── TOC ───────────────────────────────────────────────────────────────────
  const titleNorm = (article.title || '').toLowerCase().trim();
  const seenIds = new Map();
  const tocH = [];
  Array.from(articleBody.querySelectorAll('h2,h3,h4')).forEach(h => {
    const text = h.textContent?.trim() || '';
    if (text.toLowerCase() === titleNorm) return;
    let id = h.id;
    if (!id && text) id = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    if (!id || !text) return;
    const c = seenIds.get(id) ?? 0; seenIds.set(id, c + 1);
    const uid = c === 0 ? id : `${id}-${c + 1}`; h.id = uid;
    tocH.push({ id: uid, text, level: parseInt(h.tagName[1]) });
  });
  if (tocH.length > 1) {
    const mn = Math.min(...tocH.map(h => h.level)), mx = Math.max(...tocH.map(h => h.level));
    const rng = mx - mn + 1;
    tocH.forEach(h => { h.level = rng > 3 ? 2 + Math.min(2, h.level - mn) : h.level - (mn - 2); });
  }
  tocH.forEach(h => {
    const a = document.createElement('a');
    a.href = `#${h.id}`; a.textContent = h.text;
    a.style.paddingLeft = `${Math.max(0, h.level - 2) * 12}px`;
    a.style.fontSize = h.level === 2 ? '0.9rem' : '0.85rem';
    a.addEventListener('click', e => {
      e.preventDefault();
      const el = document.getElementById(h.id);
      if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - window.innerHeight * 0.3, behavior: 'smooth' });
    });
    tocNav.appendChild(a);
  });

  // TOC scroll spy + idle (mirrors Reader.tsx)
  if (tocH.length > 0) {
    let idleTimer = null;
    const spy = () => {
      const thr = window.scrollY + window.innerHeight * 0.35;
      let activeId = tocH[0].id;
      for (const h of tocH) { const el = document.getElementById(h.id); if (el && el.getBoundingClientRect().top + window.scrollY <= thr) activeId = h.id; }
      tocNav.querySelectorAll('a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === `#${activeId}`));
      // Idle: clear existing timer and restart
      tocEl.classList.remove('idle');
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => tocEl.classList.add('idle'), 5000);
    };
    window.addEventListener('scroll', spy, { passive: true });
    spy();
    window.__sediSpyCleanup__ = () => {
      window.removeEventListener('scroll', spy);
      window.removeEventListener('scroll', onScroll);
      clearTimeout(idleTimer);
    };
  } else {
    window.__sediSpyCleanup__ = () => window.removeEventListener('scroll', onScroll);
  }

  // ── Focus mode (mirrors ReaderArticle.tsx) ────────────────────────────────
  let focusCleanup = null;
  function setupFocusMode() {
    const sel = '#reader-content p,#reader-content h2,#reader-content h3,#reader-content h4,#reader-content blockquote,#reader-content ul,#reader-content ol';
    const paras = Array.from(document.querySelectorAll(sel));
    paras.forEach((p, i) => p.setAttribute('data-para-index', String(i)));

    const check = () => {
      const targetY = window.innerHeight * 0.3;
      let closest = -1, minDist = Infinity;
      paras.forEach((p, i) => {
        const r = p.getBoundingClientRect();
        const dist = r.top <= targetY && r.bottom >= targetY ? 0 : Math.min(Math.abs(r.top - targetY), Math.abs(r.bottom - targetY));
        if (dist < minDist) { minDist = dist; closest = i; }
      });
      if (closest !== -1) {
        paras.forEach((p, i) => {
          p.classList.remove('focused', 'near-focused');
          if (i === closest) p.classList.add('focused');
          else if (Math.abs(i - closest) <= 1) p.classList.add('near-focused');
        });
      }
    };
    window.addEventListener('scroll', check, { passive: true });
    check();
    focusCleanup = () => {
      window.removeEventListener('scroll', check);
      paras.forEach(p => p.classList.remove('focused', 'near-focused'));
    };
  }
  function teardownFocusMode() { focusCleanup?.(); focusCleanup = null; }

  // ── Esc ───────────────────────────────────────────────────────────────────
  window.__sediEscHandler__ = e => { if (e.key === 'Escape') toggle(); };
  document.addEventListener('keydown', window.__sediEscHandler__);

  // ── Helpers ───────────────────────────────────────────────────────────────
  function toggle() {
    teardownFocusMode();
    document.documentElement.removeAttribute(ACTIVE_ATTR);
    document.documentElement.classList.remove(
      'sedi-dark', 'sedi-sz-small', 'sedi-sz-large', 'sedi-font-serif',
      'sedi-lh-compact', 'sedi-lh-spacious', 'sedi-ls-tight', 'sedi-ls-wide',
      'sedi-w-narrow', 'sedi-w-wide', 'sedi-focus-active'
    );
    const saved = window.__sediOriginalBody__;
    if (saved) { document.body.replaceWith(saved); window.__sediOriginalBody__ = null; }
    document.getElementById('__sedi_style__')?.remove();
    document.removeEventListener('keydown', window.__sediEscHandler__);
    window.__sediSpyCleanup__?.(); window.__sediSpyCleanup__ = null;
  }

  function handleSave(btn, art) {
    btn.disabled = true; btn.textContent = 'Saving...';
    chrome.runtime.sendMessage({
      action: 'saveContent',
      payload: { url:art.url, html:art.html, title:art.title, author:art.author, description:art.description, thumbnail:art.thumbnail, publishedDate:art.publishedDate },
    }, resp => {
      if (resp?.ok) { btn.textContent = 'Saved ✓'; btn.style.borderColor = '#2e7d52'; btn.style.color = '#2e7d52'; }
      else { btn.disabled = false; btn.textContent = 'Save failed — retry'; }
    });
  }
})();
