/**
 * popup.js — sed.i extension popup
 *
 * Flow:
 *   login  → credentials
 *   ready  → title shown, Read + Save available immediately
 *   (save clicked) → extracts inline, shows signals, button becomes "Send"
 *   (send clicked) → fires to service worker + closes popup
 *   result → shown only on error (popup stays open)
 */

const DEFAULT_API_BASE = 'https://api.read-sedi.com';
const DEFAULT_FRONTEND_BASE = 'https://www.read-sedi.com';

async function getFrontendBase() {
  const { apiBase } = await msg('getApiBase');
  const base = (apiBase || DEFAULT_API_BASE).replace(/\/$/, '');
  if (base.includes('localhost')) return base.replace(/:\d+$/, ':3000');
  return DEFAULT_FRONTEND_BASE;
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function msg(action, data = {}) {
  return new Promise((resolve) =>
    chrome.runtime.sendMessage({ action, ...data }, resolve)
  );
}

function show(id) {
  document.querySelectorAll('.view').forEach((el) => el.classList.add('hidden'));
  document.getElementById(id)?.classList.remove('hidden');
}

function showError(id, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.classList.remove('hidden');
}

function hideError(id) {
  document.getElementById(id)?.classList.add('hidden');
}

// ─── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  const { token } = await msg('getToken');
  if (!token) { show('view-login'); return; }
  await setupReadyView();
}

// ─── Login ────────────────────────────────────────────────────────────────────

document.getElementById('form-login').addEventListener('submit', async (e) => {
  e.preventDefault();
  hideError('login-error');

  const email = document.getElementById('input-email').value.trim();
  const password = document.getElementById('input-password').value;
  const { apiBase: storedBase } = await msg('getApiBase');
  const apiBase = storedBase || DEFAULT_API_BASE;
  await msg('setApiBase', { apiBase });

  const btn = document.getElementById('btn-login');
  btn.disabled = true;
  btn.textContent = '▐ connecting...';

  try {
    const base = apiBase.replace(/\/$/, '');
    const formData = new URLSearchParams();
    formData.append('username', email);
    formData.append('password', password);

    const response = await fetch(`${base}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData.toString(),
    });

    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `Login failed (${response.status})`);
    }

    const data = await response.json();
    await msg('setToken', { token: data.access_token });
    await setupReadyView();
  } catch (err) {
    showError('login-error', err.message);
    btn.disabled = false;
    btn.textContent = 'Connect';
  }
});

// ─── Ready view ───────────────────────────────────────────────────────────────

let _tab = null;
let _savePhase = 'idle'; // idle | extracting | sending

async function setupReadyView() {
  show('view-ready');
  _savePhase = 'idle';

  // Reset save button
  const saveBtn = document.getElementById('btn-save');
  saveBtn.disabled = false;
  saveBtn.textContent = 'Send to sed.i';
  saveBtn.classList.remove('btn-sent');
  hideError('save-error');

  // Show title + source line immediately — no extraction needed
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  _tab = tab;

  document.getElementById('page-title').textContent = tab?.title || '';
  document.getElementById('page-thumbnail').classList.add('hidden');
  document.getElementById('page-desc').classList.add('hidden');
  document.getElementById('page-author').classList.add('hidden');
  document.getElementById('page-date').classList.add('hidden');

  // Source line: favicon + fallback domain
  try {
    const u = new URL(tab?.url || '');
    const domain = u.hostname.replace(/^www\./, '');
    const faviconEl = document.getElementById('page-favicon');
    if (tab?.favIconUrl) {
      faviconEl.src = tab.favIconUrl;
      faviconEl.style.display = 'inline';
    } else {
      faviconEl.style.display = 'none';
    }
    document.getElementById('page-domain').textContent = domain;
    document.getElementById('page-source').classList.remove('hidden');
  } catch {
    document.getElementById('page-source').classList.add('hidden');
  }

  // Inject a tiny inline function to read og/meta tags — instant, no Readability
  try {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const m = (s) => {
          for (const sel of s) {
            const v = document.querySelector(sel)?.getAttribute('content') || '';
            if (v.trim()) return v.trim();
          }
          return '';
        };
        return {
          siteName:    m(['meta[property="og:site_name"]']),
          description: m(['meta[property="og:description"]', 'meta[name="description"]']),
          thumbnail:   m(['meta[property="og:image"]', 'meta[name="twitter:image"]']),
          author:      m(['meta[name="author"]', 'meta[property="article:author"]', 'meta[name="twitter:creator"]']),
          published:   m(['meta[property="article:published_time"]']),
        };
      },
    });
    const meta = res?.result;
    if (meta) {
      if (meta.siteName) document.getElementById('page-domain').textContent = meta.siteName;

      if (meta.description) {
        const el = document.getElementById('page-desc');
        el.textContent = meta.description.length > 110 ? meta.description.slice(0, 110) + '…' : meta.description;
        el.classList.remove('hidden');
      }

      if (meta.thumbnail) {
        const el = document.getElementById('page-thumbnail');
        el.src = meta.thumbnail;
        el.classList.remove('hidden');
        el.onerror = () => el.classList.add('hidden');
      }

      if (meta.author) {
        const el = document.getElementById('page-author');
        el.textContent = meta.author;
        el.classList.remove('hidden');
      }
      if (meta.published) {
        try {
          const d = new Date(meta.published);
          if (!isNaN(d)) {
            const el = document.getElementById('page-date');
            el.textContent = d.toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' });
            el.classList.remove('hidden');
          }
        } catch {}
      }
    }
  } catch {}
}

// ─── Settings ─────────────────────────────────────────────────────────────────

document.getElementById('btn-settings-toggle').addEventListener('click', () => {
  document.getElementById('settings-drawer').classList.toggle('hidden');
});

document.getElementById('btn-close').addEventListener('click', () => window.close());

document.getElementById('btn-logout').addEventListener('click', async () => {
  await msg('clearToken');
  show('view-login');
});

// ─── Popup theme toggle ───────────────────────────────────────────────────────

const POPUP_THEME_KEY = '__sedi_popup_theme__';

function applyPopupTheme(theme, persist = true) {
  document.documentElement.setAttribute('data-popup-theme', theme);
  document.querySelectorAll('.theme-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.theme === theme);
  });
  if (persist) chrome.storage.local.set({ [POPUP_THEME_KEY]: theme });
}

chrome.storage.local.get([POPUP_THEME_KEY], (result) => {
  const saved = result[POPUP_THEME_KEY];
  if (saved === 'dark' || saved === 'light') applyPopupTheme(saved, false);
});

document.querySelectorAll('.theme-btn').forEach(btn => {
  btn.addEventListener('click', () => applyPopupTheme(btn.dataset.theme));
});

// ─── Dev mode (long-press logo ≥ 2s) ─────────────────────────────────────────

let _logoTimer = null;

function attachLongPress(el, onTrigger) {
  const start = () => {
    if (_logoTimer) clearTimeout(_logoTimer);
    _logoTimer = setTimeout(() => { _logoTimer = null; onTrigger(); }, 2000);
  };
  const cancel = () => { if (_logoTimer) { clearTimeout(_logoTimer); _logoTimer = null; } };
  el.addEventListener('mousedown', start);
  el.addEventListener('mouseup', cancel);
  el.addEventListener('mouseleave', cancel);
  el.addEventListener('touchstart', start, { passive: true });
  el.addEventListener('touchend', cancel);
  el.addEventListener('touchcancel', cancel);
  el.addEventListener('pointercancel', cancel);
}

async function showDevFields(inputId, feedbackId, revealEl) {
  revealEl.classList.remove('hidden');
  const { apiBase } = await msg('getApiBase');
  document.getElementById(inputId).value = apiBase || DEFAULT_API_BASE;
  document.getElementById(feedbackId).classList.add('hidden');
}

attachLongPress(document.querySelector('#view-ready .logo-row'), async () => {
  document.getElementById('settings-drawer').classList.remove('hidden');
  await showDevFields('input-api-url', 'api-url-feedback', document.getElementById('dev-fields'));
});

attachLongPress(document.getElementById('login-logo-row'), async () => {
  await showDevFields('input-login-api-url', 'login-api-url-feedback', document.getElementById('login-dev-fields'));
});

document.getElementById('btn-save-api-url').addEventListener('click', async () => {
  const val = document.getElementById('input-api-url').value.trim();
  if (!val) return;
  await msg('setApiBase', { apiBase: val });
  const fb = document.getElementById('api-url-feedback');
  fb.textContent = 'Saved. Re-login to apply.';
  fb.classList.remove('hidden');
});

document.getElementById('btn-reset-api-url').addEventListener('click', async () => {
  await msg('setApiBase', { apiBase: DEFAULT_API_BASE });
  document.getElementById('input-api-url').value = DEFAULT_API_BASE;
  const fb = document.getElementById('api-url-feedback');
  fb.textContent = 'Reset to production.';
  fb.classList.remove('hidden');
});

document.getElementById('btn-login-save-api-url').addEventListener('click', async () => {
  const val = document.getElementById('input-login-api-url').value.trim();
  if (!val) return;
  await msg('setApiBase', { apiBase: val });
  const fb = document.getElementById('login-api-url-feedback');
  fb.textContent = 'Saved.';
  fb.classList.remove('hidden');
});

document.getElementById('btn-login-reset-api-url').addEventListener('click', async () => {
  await msg('setApiBase', { apiBase: DEFAULT_API_BASE });
  document.getElementById('input-login-api-url').value = DEFAULT_API_BASE;
  const fb = document.getElementById('login-api-url-feedback');
  fb.textContent = 'Reset to production.';
  fb.classList.remove('hidden');
});

// ─── Read (ephemeral reader) ──────────────────────────────────────────────────

document.getElementById('btn-read').addEventListener('click', async () => {
  hideError('save-error');
  const readBtn = document.getElementById('btn-read');
  readBtn.disabled = true;
  readBtn.textContent = '...';

  try {
    if (!_tab?.id) throw new Error('No active tab.');

    await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      func: () => { window.__SEDI_SKIP_IMAGE_INLINE = true; },
    });
    const results = await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      files: ['lib/Readability.js', 'content/content.js'],
    });
    const extracted = results?.[0]?.result;
    if (!extracted || extracted.error) throw new Error(extracted?.error || 'Extraction failed.');

    const article = {
      url: _tab.url,
      html: extracted.html,
      title: extracted.title || '',
      author: extracted.author || '',
      description: extracted.description || '',
      thumbnail: extracted.thumbnail || '',
      publishedDate: extracted.publishedDate || '',
    };
    await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      func: (a) => { window.__sediArticle__ = a; },
      args: [article],
    });
    await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      files: ['content/reader-overlay.js'],
    });
    window.close();
  } catch (err) {
    readBtn.disabled = false;
    readBtn.textContent = 'Read';
    showError('save-error', err.message);
  }
});

// ─── Save ─────────────────────────────────────────────────────────────────────

const saveBtn = document.getElementById('btn-save');

// Parse structured API errors into human-readable messages.
function parseApiError(raw) {
  if (!raw) return "Couldn't save. Try again.";
  try {
    // Backend wraps detail as a JSON string on 409
    const outer = JSON.parse(raw.replace(/^API error \d+: /, ''));
    const detail = typeof outer.detail === 'string' ? JSON.parse(outer.detail) : outer.detail;
    if (detail?.message === 'Already in your library') {
      return detail.is_archived
        ? 'Already in your library (archived). Restore it from your queue.'
        : 'Already in your library.';
    }
    if (detail?.message) return detail.message;
    if (outer.detail) return String(outer.detail);
  } catch {}
  // Trim raw API error prefix for display
  return raw.replace(/^API error \d+: /, '').slice(0, 120);
}

let _dotTimer = null;
function startDots(btn) {
  const frames = ['sending', 'sending.', 'sending..', 'sending...'];
  let i = 0;
  btn.textContent = frames[0];
  _dotTimer = setInterval(() => { i = (i + 1) % frames.length; btn.textContent = frames[i]; }, 400);
}
function stopDots() { clearInterval(_dotTimer); _dotTimer = null; }

saveBtn.addEventListener('click', async () => {
  if (_savePhase !== 'idle') return;

  hideError('save-error');
  _savePhase = 'sending';
  saveBtn.disabled = true;
  startDots(saveBtn);

  try {
    if (!_tab?.id) throw new Error('No active tab.');

    const results = await chrome.scripting.executeScript({
      target: { tabId: _tab.id },
      files: ['lib/Readability.js', 'content/content.js'],
    });
    const extracted = results?.[0]?.result;
    if (!extracted || extracted.error) throw new Error(extracted?.error || 'Extraction failed.');

    const payload = {
      url:              _tab.url,
      html:             extracted.html,
      title:            extracted.title,
      author:           extracted.author        || '',
      description:      extracted.description   || '',
      thumbnail:        extracted.thumbnail     || '',
      publishedDate:    extracted.publishedDate || '',
      accessRestricted: extracted.accessRestricted || false,
    };

    chrome.runtime.sendMessage({ action: 'saveContent', payload }, (resp) => {
      stopDots();
      if (chrome.runtime.lastError || !resp?.ok) {
        _savePhase = 'idle';
        saveBtn.disabled = false;
        saveBtn.textContent = 'Send to sed.i';
        showError('save-error', parseApiError(resp?.error));
        return;
      }
      _savePhase = 'idle';
      saveBtn.disabled = true;
      saveBtn.textContent = 'sent ✓';
      saveBtn.classList.add('btn-sent');
    });

  } catch (err) {
    stopDots();
    _savePhase = 'idle';
    saveBtn.disabled = false;
    saveBtn.textContent = 'Send to sed.i';
    showError('save-error', err.message);
  }
});

// ─── Boot ─────────────────────────────────────────────────────────────────────

init();
