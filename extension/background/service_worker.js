/**
 * service_worker.js — sed.i browser extension background service worker
 *
 * Handles auth token + API base URL storage and the actual API call to the backend.
 * Uses chrome.storage.local exclusively (not localStorage, which is unavailable in MV3).
 */

const DEFAULT_API_BASE = 'https://api.read-sedi.com';

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function getStorage(...keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

async function setStorage(obj) {
  return new Promise((resolve) => chrome.storage.local.set(obj, resolve));
}

// ─── API call ─────────────────────────────────────────────────────────────────

async function handleSave(payload) {
  const { token, apiBase } = await getStorage('token', 'apiBase');
  const base = (apiBase || DEFAULT_API_BASE).replace(/\/$/, '');

  if (!token) throw new Error('Not authenticated');

  const body = {
    url: payload.url,
  };

  if (payload.html)          body.pre_extracted_html         = payload.html;
  if (payload.title)         body.pre_extracted_title        = payload.title;
  if (payload.author)        body.pre_extracted_author       = payload.author;
  if (payload.description)   body.pre_extracted_description  = payload.description;
  if (payload.thumbnail)     body.pre_extracted_thumbnail    = payload.thumbnail;
  if (payload.publishedDate) body.pre_extracted_published_date = payload.publishedDate;

  const response = await fetch(`${base}/content`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`API error ${response.status}: ${text.slice(0, 200)}`);
  }

  return response.json();
}

// ─── Message handler ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  switch (request.action) {
    case 'saveContent':
      handleSave(request.payload)
        .then((data) => sendResponse({ ok: true, data }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true; // async

    case 'getToken':
      getStorage('token').then(({ token }) => sendResponse({ token: token || null }));
      return true;

    case 'setToken':
      setStorage({ token: request.token }).then(() => sendResponse({ ok: true }));
      return true;

    case 'clearToken':
      setStorage({ token: null }).then(() => sendResponse({ ok: true }));
      return true;

    case 'getApiBase':
      getStorage('apiBase').then(({ apiBase }) =>
        sendResponse({ apiBase: apiBase || DEFAULT_API_BASE })
      );
      return true;

    case 'setApiBase':
      setStorage({ apiBase: request.apiBase }).then(() => sendResponse({ ok: true }));
      return true;

    default:
      break;
  }
});
