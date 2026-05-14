/**
 * service_worker.js — sed.i browser extension background service worker
 *
 * Handles auth token + API base URL storage and the actual API call to the backend.
 * Uses chrome.storage.local exclusively (not localStorage, which is unavailable in MV3).
 *
 * Auth flow:
 *   - Login stores both access_token (24h) and refresh_token (90d)
 *   - On 401, silently exchanges refresh_token for a new pair and retries once
 *   - If refresh fails, clears both tokens and returns AUTH_REQUIRED sentinel
 *   - Concurrent 401s share a single in-flight refresh (no double-refresh race)
 */

const DEFAULT_API_BASE = 'https://api.read-sedi.com';
const DEFAULT_FRONTEND_BASE = 'https://www.read-sedi.com';

function deriveFrontendBase(apiBase) {
  const base = (apiBase || DEFAULT_API_BASE).replace(/\/$/, '');
  if (base.includes('localhost')) return base.replace(/:\d+$/, ':3000');
  return DEFAULT_FRONTEND_BASE;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

async function getStorage(...keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

async function setStorage(obj) {
  return new Promise((resolve) => chrome.storage.local.set(obj, resolve));
}

// ─── Token refresh ────────────────────────────────────────────────────────────

// Shared promise so concurrent 401s don't trigger parallel refreshes.
let _refreshPromise = null;

async function _doRefresh(base) {
  const { refreshToken } = await getStorage('refreshToken');
  if (!refreshToken) {
    await setStorage({ token: null, refreshToken: null });
    throw new Error('AUTH_REQUIRED');
  }

  const resp = await fetch(`${base}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!resp.ok) {
    await setStorage({ token: null, refreshToken: null });
    throw new Error('AUTH_REQUIRED');
  }

  const data = await resp.json();
  await setStorage({ token: data.access_token, refreshToken: data.refresh_token });
  return data.access_token;
}

function silentRefresh(base) {
  if (!_refreshPromise) {
    _refreshPromise = _doRefresh(base).finally(() => { _refreshPromise = null; });
  }
  return _refreshPromise;
}

// ─── API call ─────────────────────────────────────────────────────────────────

async function callSaveApi(base, token, payload) {
  const body = { url: payload.url };
  if (payload.html)             body.pre_extracted_html               = payload.html;
  if (payload.title)            body.pre_extracted_title              = payload.title;
  if (payload.author)           body.pre_extracted_author             = payload.author;
  if (payload.description)      body.pre_extracted_description        = payload.description;
  if (payload.thumbnail)        body.pre_extracted_thumbnail          = payload.thumbnail;
  if (payload.publishedDate)    body.pre_extracted_published_date     = payload.publishedDate;
  if (payload.accessRestricted) body.pre_extracted_access_restricted  = true;

  return fetch(`${base}/content`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
}

async function handleSave(payload) {
  const { token, apiBase } = await getStorage('token', 'apiBase');
  const base = (apiBase || DEFAULT_API_BASE).replace(/\/$/, '');

  if (!token) throw new Error('AUTH_REQUIRED');

  let response = await callSaveApi(base, token, payload);

  // Silent refresh + single retry on 401
  if (response.status === 401) {
    const newToken = await silentRefresh(base);
    response = await callSaveApi(base, newToken, payload);
  }

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
      // Legacy single-token path (login without refresh token)
      setStorage({ token: request.token }).then(() => sendResponse({ ok: true }));
      return true;

    case 'setTokenPair':
      setStorage({ token: request.token, refreshToken: request.refreshToken })
        .then(() => sendResponse({ ok: true }));
      return true;

    case 'clearToken':
      setStorage({ token: null, refreshToken: null }).then(() => sendResponse({ ok: true }));
      return true;

    case 'logout':
      // Revoke server-side then clear local storage
      (async () => {
        const { refreshToken, apiBase } = await getStorage('refreshToken', 'apiBase');
        const base = (apiBase || DEFAULT_API_BASE).replace(/\/$/, '');
        if (refreshToken) {
          fetch(`${base}/auth/logout`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken }),
          }).catch(() => {}); // best-effort — don't block local logout
        }
        await setStorage({ token: null, refreshToken: null });
        sendResponse({ ok: true });
      })();
      return true;

    case 'getApiBase':
      getStorage('apiBase').then(({ apiBase }) =>
        sendResponse({ apiBase: apiBase || DEFAULT_API_BASE })
      );
      return true;

    case 'setApiBase':
      setStorage({ apiBase: request.apiBase }).then(() => sendResponse({ ok: true }));
      return true;

    case 'getFrontendBase':
      getStorage('apiBase').then(({ apiBase }) =>
        sendResponse({ frontendBase: deriveFrontendBase(apiBase) })
      );
      return true;

    default:
      break;
  }
});
