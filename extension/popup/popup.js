/**
 * popup.js — sed.i browser extension popup controller
 *
 * Flow:
 *   login  → user enters credentials + API URL
 *   ready  → shows page title, save options, save button
 *   saving → extracting content (overlay)
 *   preview → shows extraction signals + text snippet; user confirms or cancels
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
  // Use stored API base (set in settings drawer) or fall back to production default
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

  // Show current page title
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const titleEl = document.getElementById('page-title');
  if (tab?.title) titleEl.textContent = tab.title;
}

// Settings toggle
document.getElementById('btn-settings-toggle').addEventListener('click', () => {
  document.getElementById('settings-drawer').classList.toggle('hidden');
});

// Close button — just closes the popup window
document.getElementById('btn-close').addEventListener('click', () => {
  window.close();
});

// Logout (moved into settings drawer)
document.getElementById('btn-logout').addEventListener('click', async () => {
  await msg('clearToken');
  show('view-login');
});

// ─── Save / Extract ───────────────────────────────────────────────────────────

// Holds the pending payload between extraction and user confirmation
let _pendingPayload = null;
let _pendingTabTitle = '';

document.getElementById('btn-save').addEventListener('click', async () => {
  hideError('save-error');
  const withImages = document.getElementById('toggle-images').checked;

  show('view-saving');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = tab?.url;
    if (!url) throw new Error('Could not determine current page URL.');

    _pendingTabTitle = tab?.title || url;

    if (!withImages) {
      // Text-only: skip extraction preview, send immediately
      _pendingPayload = { url };
      await sendPayload();
      return;
    }

    // We now rely solely on activeTab permission to inject on demand.
    // This avoids the need for broad host_permissions in the manifest.
    let extracted = null;
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['lib/Readability.js', 'content/content.js'],
    });
    extracted = results?.[0]?.result;

    if (!extracted || extracted.error) {
      throw new Error(extracted?.error || 'Content extraction failed.');
    }

    _pendingPayload = {
      url,
      html:          extracted.html,
      title:         extracted.title,
      author:        extracted.author        || '',
      description:   extracted.description   || '',
      thumbnail:     extracted.thumbnail     || '',
      publishedDate: extracted.publishedDate || '',
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

  const { wordCount, debugInfo, html } = extracted;
  const signals = document.getElementById('preview-signals');
  signals.innerHTML = '';

  function sig(label, level) {
    const s = document.createElement('span');
    s.className = `signal ${level}`;
    s.textContent = label;
    signals.appendChild(s);
  }

  // Word count signal
  if (wordCount > 300) sig(`${wordCount} words`, 'ok');
  else if (wordCount > 50) sig(`${wordCount} words`, 'warn');
  else sig(`${wordCount} words — very short`, 'bad');

  // Paywall-specific selector signal
  if (debugInfo?.foundSpecific) {
    sig('subscriber content detected', 'ok');
  } else {
    sig('no subscriber selector found', 'warn');
  }

  // Plain text snippet (strip tags)
  const snippet = (html || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 400);
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
        // Open the frontend dashboard
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
