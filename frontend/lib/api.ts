// API base URL - matches your FastAPI backend
// Use environment variable for production, fallback to localhost for development
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Connections panel types ──────────────────────────────────────────────────

export interface HighlightArticleConnection {
  article_id: string;
  article_title: string;
  article_author: string | null;
  article_domain: string;
  shared_tags: string[];
  passages: string[];
  passage_highlight_ids: string[];
  connection_score: number;
}

export interface ConnectionsForHighlightResponse {
  source_note: string | null;
  connections: HighlightArticleConnection[];
}

export interface HighlightWithConnections {
  highlight_id: string;
  highlight_text: string;
  connections: HighlightArticleConnection[];
}

export class APIError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    public readonly body: Record<string, any> | null = null,
  ) {
    super(detail);
    this.name = "APIError";
  }
}

// Auth is httpOnly-cookie-based (see app/core/auth_cookies.py on the
// backend) — the access and refresh tokens are never readable by JS, so
// there is nothing to read/store/clear here. The browser attaches the
// cookies automatically on every request to the same registrable domain
// (www.read-sedi.com / api.read-sedi.com) as long as `credentials: "include"`
// is set. Only the CSRF token cookie is deliberately non-httpOnly, since the
// double-submit pattern requires the frontend to read it and echo it back.

const CSRF_COOKIE_NAME = "sedi_csrf_token";
const CSRF_HEADER_NAME = "X-CSRF-Token";

const getCsrfToken = (): string | null => {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${CSRF_COOKIE_NAME}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : null;
};

// Exchanges the httpOnly refresh-token cookie for a new access/refresh pair
// via POST /auth/refresh (no body needed — the backend reads the refresh
// token from the cookie). Refresh tokens rotate server-side on every use, so
// the backend sets fresh cookies on the response; nothing to store here.
// Concurrent 401s share one in-flight refresh instead of each racing their
// own — the backend revokes the old refresh token immediately on use, so a
// second concurrent call with the same (now-stale) cookie would fail.
let refreshInFlight: Promise<boolean> | null = null;

const refreshAccessToken = async (): Promise<boolean> => {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = (async () => {
    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        headers[CSRF_HEADER_NAME] = csrfToken;
      }
      const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers,
        body: JSON.stringify({}),
      });
      return response.ok;
    } catch {
      return false;
    }
  })();

  try {
    return await refreshInFlight;
  } finally {
    refreshInFlight = null;
  }
};

// Helper function to make authenticated requests
//
// suppressRedirect: when true, an unrecovered 401 throws APIError instead of
// navigating to /login. Needed for AuthContext's mount-time getCurrentUser()
// call — with httpOnly cookies, JS can't check "is there a token" before
// deciding whether to call the backend (unlike the old localStorage check),
// so that call now fires on every page load including anonymous visits to
// public pages (homepage, /login itself). A 401 there means "not logged in
// yet," not "session expired mid-action" — it must not force-navigate away
// from whatever public page the visitor is on. Every other call site keeps
// the default (unset = redirect), which is the case this behavior was
// actually designed for: an authenticated user's session expiring mid-use.
const fetchWithAuth = async (
  url: string,
  options: RequestInit = {},
  isRetry = false,
  suppressRedirect = false,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): Promise<any> => {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  // Spread existing headers if they exist
  if (options.headers) {
    Object.assign(headers, options.headers);
  }

  // CSRF token required on mutating requests once auth is cookie-based —
  // GET/HEAD are exempt server-side, but sending it unconditionally is
  // harmless and simpler than tracking method here too.
  const csrfToken = getCsrfToken();
  if (csrfToken) {
    headers[CSRF_HEADER_NAME] = csrfToken;
  }

  const response = await fetch(url, {
    ...options,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    if (response.status === 401 && !isRetry) {
      // Access token expired or invalid — try refreshing via the httpOnly
      // refresh cookie before giving up and sending the user to /login.
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        return fetchWithAuth(url, options, true, suppressRedirect);
      }

      if (typeof window !== "undefined" && !suppressRedirect) {
        window.location.href = "/login";
      }
    }

    const errorData = await response.json().catch(() => null);
    // If detail is a JSON-encoded string (e.g. structured 409 bodies), parse it
    // so callers can read err.body.existing_id etc. directly.
    let parsedBody = errorData;
    if (typeof errorData?.detail === "string") {
      try {
        parsedBody = JSON.parse(errorData.detail);
      } catch {
        // detail is a plain string, not JSON — leave parsedBody as-is
      }
    }
    const detail: string =
      (typeof parsedBody?.message === "string" ? parsedBody.message : null) ||
      (typeof errorData?.detail === "string" ? errorData.detail : null) ||
      (typeof errorData === "string" ? errorData : null) ||
      (response.status === 429
        ? "Too many requests. Please slow down."
        : null) ||
      `Request failed (${response.status}).`;
    throw new APIError(response.status, detail, parsedBody);
  }

  return response.status === 204 ? null : response.json();
};

export const api = {
  get: (url: string) => fetchWithAuth(`${API_BASE_URL}${url}`),
  post: (url: string, data: unknown) =>
    fetchWithAuth(`${API_BASE_URL}${url}`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  put: (url: string, data: unknown) =>
    fetchWithAuth(`${API_BASE_URL}${url}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),
  delete: (url: string, data?: unknown) =>
    fetchWithAuth(`${API_BASE_URL}${url}`, {
      method: "DELETE",
      ...(data !== undefined && { body: JSON.stringify(data) }),
    }),
};

export default api;

// Auth API - matches your /auth endpoints
export const authAPI = {
  login: async (username: string, password: string) => {
    // OAuth2PasswordRequestForm expects application/x-www-form-urlencoded
    const formData = new URLSearchParams();
    formData.append("username", username);
    formData.append("password", password);

    const response = await fetch(`${API_BASE_URL}/auth/login`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: formData.toString(),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Login failed");
    }

    // The backend sets httpOnly auth cookies on this response (see
    // app/core/auth_cookies.py) — nothing to store client-side. The JSON
    // body still contains access_token/refresh_token for parity with the
    // extension/MCP-style clients that authenticate via Bearer token
    // instead, but the web frontend ignores those fields.
    return response.json();
  },

  register: async (
    fullName: string,
    email: string,
    password: string,
    username: string,
  ) => {
    const response = await fetch(`${API_BASE_URL}/auth/register`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        email,
        password,
        username,
        full_name: fullName || null,
      }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || "Registration failed");
    }

    return response.json();
  },

  getCurrentUser: async () => {
    // suppressRedirect: called unconditionally on mount by AuthContext,
    // including on anonymous visits to public pages — a 401 here means "not
    // logged in yet," not "session expired," so it must not force-navigate
    // to /login. See fetchWithAuth's suppressRedirect doc comment above.
    return fetchWithAuth(`${API_BASE_URL}/auth/me`, {}, false, true);
  },

  logout: async () => {
    // Revoke server-side and clear cookies (see app/core/auth_cookies.py::
    // clear_auth_cookies) — the backend reads the refresh token from the
    // httpOnly cookie, nothing to send in the body. Best-effort: a failed
    // revoke shouldn't block navigating to /login.
    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        headers[CSRF_HEADER_NAME] = csrfToken;
      }
      await fetch(`${API_BASE_URL}/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers,
        body: JSON.stringify({}),
      });
    } catch {
      // Ignore — proceed to /login regardless.
    }

    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  },

  verifyEmail: async (token: string) => {
    const response = await fetch(
      `${API_BASE_URL}/auth/verify-email?token=${encodeURIComponent(token)}`,
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "Email verification failed");
    }
    return response.json();
  },

  forgotPassword: async (email: string) => {
    const response = await fetch(`${API_BASE_URL}/auth/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(
        error.detail || "Couldn't send password reset email. Try again.",
      );
    }
    return response.json();
  },

  resetPassword: async (token: string, newPassword: string) => {
    const response = await fetch(`${API_BASE_URL}/auth/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, new_password: newPassword }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || "Password reset failed");
    }
    return response.json();
  },
};

// Content API - matches your /content endpoints
export const contentAPI = {
  // Get all content items (GET /content)
  getAll: async () => {
    return fetchWithAuth(`${API_BASE_URL}/content`);
  },

  // Get a single content item by ID (GET /content/{item_id})
  getById: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${id}`);
  },

  // Get full content with extracted text (GET /content/{item_id}/full)
  getFullById: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${id}/full`);
  },

  // Create a new content item (POST /content)
  create: async (data: {
    url: string;
    list_ids?: string[];
    pre_extracted_html?: string;
    pre_extracted_title?: string;
    pre_extracted_author?: string;
    pre_extracted_description?: string;
    pre_extracted_thumbnail?: string;
    pre_extracted_published_date?: string;
    initial_highlights?: Array<{
      text: string;
      note?: string;
      start_offset: number;
      end_offset: number;
      color?: string;
    }>;
  }) => {
    return fetchWithAuth(`${API_BASE_URL}/content`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  // Update a content item (PATCH /content/{item_id})
  update: async (
    id: string,
    data: {
      title?: string;
      description?: string;
      author?: string;
      published_date?: string | null;
      is_read?: boolean;
      is_archived?: boolean;
      is_public?: boolean;
      read_position?: number;
      tags?: string[];
      auto_tags?: string[];
      full_text?: string;
    },
  ) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  // Delete a content item (DELETE /content/{item_id})
  delete: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${id}`, {
      method: "DELETE",
    });
  },

  // Trigger summarization (POST /content/{item_id}/summary)
  summarize: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${id}/summary`, {
      method: "POST",
    });
  },

  // Get all unique tags with counts (GET /tags)
  getTags: async () => {
    return fetchWithAuth(`${API_BASE_URL}/content/tags`);
  },

  // Get content filtered by tag (GET /content?tag=x)
  filterByTag: async (tag: string, skip = 0, limit = 50) => {
    return fetchWithAuth(
      `${API_BASE_URL}/content?tag=${encodeURIComponent(tag)}&skip=${skip}&limit=${limit}`,
    );
  },

  // Accept auto-generated tags (POST /content/{item_id}/tags/accept)
  acceptTags: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${id}/tags/accept`, {
      method: "POST",
    });
  },

  // Dismiss auto-generated tags (POST /content/{item_id}/tags/dismiss)
  dismissTags: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${id}/tags/dismiss`, {
      method: "POST",
    });
  },

  // Get recommended content (GET /content/recommended)
  getRecommended: async (skip = 0, limit = 10, mood?: string) => {
    const params = new URLSearchParams();
    params.append("skip", skip.toString());
    params.append("limit", limit.toString());
    if (mood) params.append("mood", mood);
    return fetchWithAuth(
      `${API_BASE_URL}/content/recommended?${params.toString()}`,
    );
  },
};

// Lists API - matches your /lists endpoints (for future use)
export const listsAPI = {
  // Get all lists (GET /lists)
  getAll: async () => {
    return fetchWithAuth(`${API_BASE_URL}/lists`);
  },

  // Create a new list (POST /lists)
  create: async (data: {
    name: string;
    description?: string;
    is_shared?: boolean;
  }) => {
    return fetchWithAuth(`${API_BASE_URL}/lists`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  // Get a specific list (GET /lists/{list_id})
  getById: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${id}`);
  },

  // Update a list (PATCH /lists/{list_id})
  update: async (id: string, data: { name?: string; description?: string }) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  // Delete a list (DELETE /lists/{list_id})
  delete: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${id}`, {
      method: "DELETE",
    });
  },

  // Get content in a list (GET /lists/{list_id}/content)
  getContent: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${id}/content`);
  },

  // Add content to a list (POST /lists/{list_id}/content)
  addContent: async (listId: string, contentItemIds: string[]) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${listId}/content`, {
      method: "POST",
      body: JSON.stringify({ content_item_ids: contentItemIds }),
    });
  },

  // Remove content from a list (DELETE /lists/{list_id}/content)
  removeContent: async (listId: string, contentItemIds: string[]) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${listId}/content`, {
      method: "DELETE",
      body: JSON.stringify({ content_item_ids: contentItemIds }),
    });
  },

  // Get all highlights for all content in a list (GET /lists/{list_id}/highlights)
  getHighlights: async (listId: string) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${listId}/highlights`);
  },
};

// Drafts API — writing workspace
export const draftsAPI = {
  // Get draft for a list (GET /lists/{list_id}/draft)
  get: async (listId: string) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${listId}/draft`);
  },

  // Create draft (POST /lists/{list_id}/draft)
  create: async (
    listId: string,
    data: { content?: string; title?: string; word_count?: number },
  ) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${listId}/draft`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  // Update draft — autosave target; auto-creates if missing (PATCH /lists/{list_id}/draft)
  update: async (
    listId: string,
    data: { content?: string; title?: string; word_count?: number },
  ) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${listId}/draft`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  // Delete draft (DELETE /lists/{list_id}/draft)
  delete: async (listId: string) => {
    return fetchWithAuth(`${API_BASE_URL}/lists/${listId}/draft`, {
      method: "DELETE",
    });
  },

  getRelevantReads: async (
    listId: string,
  ): Promise<{ items: RelevantReadItem[] }> => {
    const resp = await fetchWithAuth(
      `${API_BASE_URL}/lists/${listId}/draft/relevant-reads`,
    );
    if (!resp.ok) return { items: [] };
    return resp.json();
  },
};

// Search API - matches your /search endpoints (for future use)
export const searchAPI = {
  // Find similar content (GET /search/{item_id}/similar)
  findSimilar: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/search/${id}/similar`);
  },

  // Record a search interaction event (fire-and-forget)
  postTelemetry: (payload: {
    surface: string;
    item_id: string;
    shared_tag?: string;
    action: "click" | "dismiss";
  }): void => {
    fetchWithAuth(`${API_BASE_URL}/search/telemetry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch(() => {});
  },

  // Semantic search (GET /search/semantic)
  semantic: async (
    query: string,
    opts: {
      limit?: number;
      offset?: number;
      after?: string;
      before?: string;
      mode?: "auto" | "full";
    } = {},
  ) => {
    const params = new URLSearchParams({ query });
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.offset !== undefined) params.set("offset", String(opts.offset));
    if (opts.mode) params.set("mode", opts.mode);
    // Date filters appended as typed operators the backend already understands
    let q = query;
    if (opts.after) q += ` after:${opts.after}`;
    if (opts.before) q += ` before:${opts.before}`;
    params.set("query", q);
    return fetchWithAuth(`${API_BASE_URL}/search/semantic?${params}`);
  },

  // Find connections for a single highlight, grouped by article (GET /search/connections/{highlight_id})
  findHighlightConnections: async (
    highlightId: string,
  ): Promise<ConnectionsForHighlightResponse> => {
    return fetchWithAuth(
      `${API_BASE_URL}/search/connections/${highlightId}`,
    ) as Promise<ConnectionsForHighlightResponse>;
  },

  // Find all highlights in an article with their connections — Mode 2 (GET /search/connections/article/{content_id}/highlights)
  findHighlightGroupedConnections: async (
    contentId: string,
  ): Promise<HighlightWithConnections[]> => {
    return fetchWithAuth(
      `${API_BASE_URL}/search/connections/article/${contentId}/highlights`,
    ) as Promise<HighlightWithConnections[]>;
  },

  // Get lazy insight for a highlight+article pair (GET /search/connections/{highlight_id}/insight/{article_id})
  getConnectionInsight: async (
    highlightId: string,
    articleId: string,
  ): Promise<{ insight: string | null }> => {
    return fetchWithAuth(
      `${API_BASE_URL}/search/connections/${highlightId}/insight/${articleId}`,
    ) as Promise<{ insight: string | null }>;
  },

  // Find all connections for an article's highlights — legacy article-level endpoint
  findArticleConnections: async (contentId: string) => {
    return fetchWithAuth(
      `${API_BASE_URL}/search/connections/article/${contentId}`,
    );
  },
};

// Analytics API - matches your /analytics endpoints
export const analyticsAPI = {
  // Get user statistics (GET /analytics/stats)
  getStats: async () => {
    return fetchWithAuth(`${API_BASE_URL}/analytics/stats`);
  },
};

// Highlights API - matches your /highlights endpoints
export const highlightsAPI = {
  // Create a highlight (POST /content/{content_id}/highlights)
  create: async (
    contentId: string,
    data: {
      text: string;
      start_offset: number;
      end_offset: number;
      color?: string;
      note?: string;
    },
  ) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${contentId}/highlights`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  // Get all highlights for content (GET /content/{content_id}/highlights)
  getByContent: async (contentId: string) => {
    return fetchWithAuth(`${API_BASE_URL}/content/${contentId}/highlights`);
  },

  // Update a highlight (PATCH /highlights/{highlight_id})
  update: async (
    highlightId: string,
    data: { note?: string; color?: string },
  ) => {
    return fetchWithAuth(`${API_BASE_URL}/highlights/${highlightId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  // Delete a highlight (DELETE /highlights/{highlight_id})
  delete: async (highlightId: string) => {
    return fetchWithAuth(`${API_BASE_URL}/highlights/${highlightId}`, {
      method: "DELETE",
    });
  },
};

// Vinyl API - matches your /vinyl endpoints
export const vinylAPI = {
  // Get all vinyl records (GET /vinyl)
  getAll: async (params?: {
    status?: string;
    sort_by?: string;
    sort_order?: string;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.append("status", params.status);
    if (params?.sort_by) searchParams.append("sort_by", params.sort_by);
    if (params?.sort_order)
      searchParams.append("sort_order", params.sort_order);
    const qs = searchParams.toString();
    return fetchWithAuth(`${API_BASE_URL}/vinyl${qs ? `?${qs}` : ""}`);
  },

  // Get a single vinyl record (GET /vinyl/{id})
  getById: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/vinyl/${id}`);
  },

  // Create from Discogs URL (POST /vinyl)
  create: async (discogsUrl: string) => {
    return fetchWithAuth(`${API_BASE_URL}/vinyl`, {
      method: "POST",
      body: JSON.stringify({ discogs_url: discogsUrl }),
    });
  },

  // Update user fields (PATCH /vinyl/{id})
  update: async (
    id: string,
    data: {
      title?: string;
      artist?: string;
      notes?: string;
      rating?: number;
      tags?: string[];
      status?: string;
      is_public?: boolean;
      cover_url?: string;
      genres?: string[];
      styles?: string[];
      videos?: { title?: string; uri: string; duration?: number }[];
    },
  ) => {
    return fetchWithAuth(`${API_BASE_URL}/vinyl/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  },

  // Soft delete (DELETE /vinyl/{id})
  delete: async (id: string) => {
    return fetchWithAuth(`${API_BASE_URL}/vinyl/${id}`, {
      method: "DELETE",
    });
  },
};

// Public API - unauthenticated routes for public profiles
export const publicAPI = {
  // Get public profile
  getProfile: async (username: string) => {
    const response = await fetch(`${API_BASE_URL}/public/u/${username}`);
    if (!response.ok) {
      if (response.status === 404) throw new Error("Profile not found");
      if (response.status === 403) throw new Error("Profile is private");
      throw new Error("Couldn't load profile.");
    }
    return response.json();
  },

  // Get public content for a user
  getPublicContent: async (username: string) => {
    const response = await fetch(
      `${API_BASE_URL}/public/u/${username}/content`,
    );
    if (!response.ok) throw new Error("Couldn't load public content.");
    return response.json();
  },

  // Get public vinyl records for a user
  getPublicVinyl: async (username: string) => {
    const response = await fetch(`${API_BASE_URL}/public/u/${username}/vinyl`);
    if (!response.ok) throw new Error("Couldn't load public crates.");
    return response.json();
  },

  // Get a single public content item by ID
  getPublicContentItem: async (username: string, itemId: string) => {
    const response = await fetch(
      `${API_BASE_URL}/public/u/${username}/content/${itemId}`,
    );
    if (!response.ok) throw new Error("Not found");
    return response.json();
  },
};

export interface RelevantReadItem {
  id: string;
  title: string | null;
  tags: string[];
  thumbnail_url: string | null;
}

export interface TopArticle {
  id: string;
  title: string | null;
  thumbnail: string | null;
}

export interface ReadingCluster {
  id: string;
  label: string;
  article_count: number;
  tag_labels: string[];
  top_articles: TopArticle[];
}

export const themesAPI = {
  getClusters: async (): Promise<{ clusters: ReadingCluster[] }> => {
    return fetchWithAuth(`${API_BASE_URL}/themes`);
  },
};
