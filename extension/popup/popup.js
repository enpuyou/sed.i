/**
 * popup.js — sed.i browser extension popup controller
 *
 * Flow:
 *   login  → user enters credentials
 *   ready  → shows page title + article meta, save button
 *   saving → extracting content (overlay)
 *   preview → shows extraction signals + scrollable text snippet; user confirms or cancels
 *   sending → posting to backend (overlay)
 *   result → shows success/error after saving
 */

const DEFAULT_API_BASE = 'https://api.read-sedi.com';
// The frontend app URL (separate from the API — hosted on Vercel)
const DEFAULT_FRONTEND_BASE = 'https://www.read-sedi.com';

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

  if (!token) {
    show('view-login');
    return;
  }

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
  btn.textContent = '▐ Connecting...';

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

async function setupReadyView() {
  show('view-ready');

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const titleEl = document.getElementById('page-title');
  if (tab?.title) titleEl.textContent = tab.title;

}

// Settings toggle
document.getElementById('btn-settings-toggle').addEventListener('click', () => {
  document.getElementById('settings-drawer').classList.toggle('hidden');
});

// Close button
document.getElementById('btn-close').addEventListener('click', () => {
  window.close();
});

// Logout
document.getElementById('btn-logout').addEventListener('click', async () => {
  await msg('clearToken');
  show('view-login');
});

// ─── Dev mode (long-press any logo ≥ 2s) ────────────────────────────────────

let _logoHoldTimer = null;

function attachLongPress(el, onTrigger) {
  function start() {
    // Cancel any in-flight timer before starting a new one (idempotent)
    if (_logoHoldTimer) { clearTimeout(_logoHoldTimer); }
    _logoHoldTimer = setTimeout(() => { _logoHoldTimer = null; onTrigger(); }, 2000);
  }
  function cancel() {
    if (_logoHoldTimer) { clearTimeout(_logoHoldTimer); _logoHoldTimer = null; }
  }
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

// Ready view: long-press logo-row in the header
attachLongPress(document.querySelector('#view-ready .logo-row'), async () => {
  document.getElementById('settings-drawer').classList.remove('hidden');
  await showDevFields('input-api-url', 'api-url-feedback', document.getElementById('dev-fields'));
});

// Login view: long-press the login logo-row
attachLongPress(document.getElementById('login-logo-row'), async () => {
  await showDevFields('input-login-api-url', 'login-api-url-feedback', document.getElementById('login-dev-fields'));
});

// Dev field actions — ready view
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

// Dev field actions — login view
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
  show('view-saving');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tab?.url;
    if (!url) throw new Error('Could not determine current page URL.');

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['lib/Readability.js', 'content/content.js'],
    });
    const extracted = results?.[0]?.result;

    if (!extracted || extracted.error) {
      throw new Error(extracted?.error || 'Content extraction failed.');
    }

    // Store payload in chrome.storage.session — the /read page fetches it via messaging
    await msg('setEphemeralArticle', {
      article: {
        url,
        html:           extracted.html,
        title:          extracted.title          || '',
        author:         extracted.author         || '',
        description:    extracted.description    || '',
        thumbnail:      extracted.thumbnail      || '',
        publishedDate:  extracted.publishedDate  || '',
      },
    });

    const frontendBase = DEFAULT_FRONTEND_BASE.replace(/\/$/, '');
    chrome.tabs.create({ url: `${frontendBase}/read` });
    window.close();

  } catch (err) {
    show('view-ready');
    showError('save-error', err.message);
  }
});

// ─── Save / Extract ───────────────────────────────────────────────────────────

let _pendingPayload = null;
let _pendingTabTitle = '';

document.getElementById('btn-save').addEventListener('click', async () => {
  hideError('save-error');

  show('view-saving');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tab?.url;
    if (!url) throw new Error('Could not determine current page URL.');

    _pendingTabTitle = tab?.title || url;

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['lib/Readability.js', 'content/content.js'],
    });
    const extracted = results?.[0]?.result;

    if (!extracted || extracted.error) {
      throw new Error(extracted?.error || 'Content extraction failed.');
    }

    _pendingPayload = {
      url,
      html:             extracted.html,
      title:            extracted.title,
      author:           extracted.author        || '',
      description:      extracted.description   || '',
      thumbnail:        extracted.thumbnail     || '',
      publishedDate:    extracted.publishedDate || '',
      accessRestricted: extracted.accessRestricted || false,
    };
    showPreview(extracted);

  } catch (err) {
    show('view-ready');
    showError('save-error', err.message);
  }
});

// ─── Preview ──────────────────────────────────────────────────────────────────

function showPreview(extracted) {
  show('view-preview');
  hideError('preview-error');

  const { wordCount, debugInfo, html, accessRestricted, author, publishedDate } = extracted;
  const signals = document.getElementById('preview-signals');
  signals.innerHTML = '';

  function sig(label, level) {
    const s = document.createElement('span');
    s.className = `signal ${level}`;
    s.textContent = label;
    signals.appendChild(s);
  }

  // Access restriction — show first, it's the most important signal
  if (accessRestricted) {
    sig('access restricted', 'bad');
  }

  // Word count
  if (wordCount > 300) sig(`${wordCount} words`, 'ok');
  else if (wordCount > 80) sig(`${wordCount} words`, 'warn');
  else sig(`${wordCount} words`, 'bad');

  // Estimated reading time
  const readMins = Math.max(1, Math.round(wordCount / 200));
  sig(`~${readMins} min read`, 'ok');

  // Author
  if (author) sig(author.length > 25 ? author.slice(0, 25) + '…' : author, 'ok');

  // Published date — show just the year/month if available
  if (publishedDate) {
    try {
      const d = new Date(publishedDate);
      if (!isNaN(d.getTime())) {
        sig(d.toLocaleDateString('en', { month: 'short', year: 'numeric' }), 'ok');
      }
    } catch {}
  }

  // Extraction quality
  if (!accessRestricted) {
    if (debugInfo?.foundSpecific) {
      sig('subscriber content', 'ok');
    }
  }

  // Snippet — show more text (800 chars), scrollable via CSS
  const snippet = (html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 800);
  document.getElementById('preview-snippet').textContent = snippet || '(no text extracted)';
}

document.getElementById('btn-confirm-save').addEventListener('click', async () => {
  if (!_pendingPayload) return;
  hideError('preview-error');
  show('view-sending');
  await sendPayload();
});

document.getElementById('btn-preview-cancel').addEventListener('click', () => {
  _pendingPayload = null;
  setupReadyView();
});

// ─── Send ─────────────────────────────────────────────────────────────────────

async function sendPayload() {
  try {
    const result = await msg('saveContent', { payload: _pendingPayload });
    if (!result.ok) throw new Error(result.error || 'Save failed');
    _pendingPayload = null;
    showResult(true, _pendingTabTitle);
  } catch (err) {
    show('view-preview');
    showError('preview-error', err.message);
  }
}

// ─── Result view ──────────────────────────────────────────────────────────────

function showResult(success, pageTitle) {
  show('view-result');

  document.getElementById('result-icon').textContent = success ? '✓' : '✕';

  document.getElementById('result-message').textContent = success
    ? `"${pageTitle}" saved to your queue.`
    : 'Something went wrong.';

  const actionsEl = document.getElementById('result-actions');
  actionsEl.innerHTML = '';

  if (success) {
    const link = document.createElement('a');
    link.href = '#';
    link.textContent = 'Open sed.i →';
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const frontendBase = DEFAULT_FRONTEND_BASE.replace(/\/$/, '');
      chrome.tabs.create({ url: `${frontendBase}/dashboard` });
    });
    actionsEl.appendChild(link);
  }
}

// Back from result
document.getElementById('btn-back').addEventListener('click', async () => {
  _pendingPayload = null;
  await setupReadyView();
});

// ─── Boot ─────────────────────────────────────────────────────────────────────

init();
