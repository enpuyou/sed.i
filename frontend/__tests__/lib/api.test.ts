/**
 * Tests for lib/api.ts — cookie-based auth + CSRF token handling.
 *
 * Auth moved from localStorage to httpOnly cookies (app/core/auth_cookies.py
 * on the backend) — these tests cover what's actually new/testable from the
 * frontend side: reading the CSRF cookie and attaching it as a header, and
 * that credentials: "include" is always set so the browser sends auth
 * cookies automatically. The httpOnly access/refresh cookies themselves
 * aren't testable from JS by design (that's the point of httpOnly).
 */

import api from "../../lib/api";

const originalFetch = global.fetch;

describe("fetchWithAuth CSRF handling", () => {
  beforeEach(() => {
    document.cookie = "sedi_csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ result: "ok" }),
    });
  });

  afterEach(() => {
    global.fetch = originalFetch;
    document.cookie = "sedi_csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("attaches X-CSRF-Token header when the csrf cookie is present", async () => {
    document.cookie = "sedi_csrf_token=test-csrf-value-123";

    await api.post("/content", { url: "https://example.com" });

    expect(global.fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-CSRF-Token": "test-csrf-value-123",
        }),
      }),
    );
  });

  it("does not attach X-CSRF-Token header when no csrf cookie is present", async () => {
    await api.get("/content");

    const call = (global.fetch as jest.Mock).mock.calls[0];
    const headers = call[1].headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("always sends credentials: include so auth cookies attach automatically", async () => {
    await api.get("/content");

    expect(global.fetch).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("reads the csrf cookie value correctly when other cookies are present", async () => {
    document.cookie = "unrelated_cookie=abc";
    document.cookie = "sedi_csrf_token=the-real-token";
    document.cookie = "another_cookie=xyz";

    await api.post("/content", { url: "https://example.com" });

    const call = (global.fetch as jest.Mock).mock.calls[0];
    const headers = call[1].headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("the-real-token");
  });
});

describe("fetchWithAuth 401 handling", () => {
  // The actual window.location.href = "/login" navigation on unrecovered 401
  // (pre-existing behavior, not new in this change) isn't asserted here —
  // jsdom's Location object doesn't support being mocked/redefined in a way
  // that's safe across jsdom versions. These tests cover what IS new and
  // testable: the refresh-retry request sequence and the eventual rejection.
  afterEach(() => {
    global.fetch = originalFetch;
    document.cookie = "sedi_csrf_token=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("retries once via /auth/refresh on a 401, then succeeds", async () => {
    let callCount = 0;
    global.fetch = jest.fn().mockImplementation((url: string) => {
      callCount += 1;
      if (typeof url === "string" && url.includes("/auth/refresh")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({}),
        });
      }
      if (callCount === 1) {
        // First call to the actual endpoint fails with 401.
        return Promise.resolve({
          ok: false,
          status: 401,
          json: async () => ({ detail: "Could not validate credentials" }),
        });
      }
      // Retry after refresh succeeds.
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({ items: [] }),
      });
    });

    const result = await api.get("/content");
    expect(result).toEqual({ items: [] });

    const urls = (global.fetch as jest.Mock).mock.calls.map((c) => c[0]);
    expect(urls.some((u: string) => u.includes("/auth/refresh"))).toBe(true);
  });

  it("throws APIError when refresh also fails, without an infinite retry loop", async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/auth/refresh")) {
        return Promise.resolve({
          ok: false,
          status: 401,
          json: async () => ({}),
        });
      }
      return Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Could not validate credentials" }),
      });
    });

    await expect(api.get("/content")).rejects.toThrow(
      "Could not validate credentials",
    );

    // Exactly one attempt on /content plus one refresh attempt — confirms
    // isRetry actually prevents a second retry loop after the refresh fails.
    const calls = (global.fetch as jest.Mock).mock.calls;
    const contentCalls = calls.filter(
      (c) => typeof c[0] === "string" && c[0].includes("/content"),
    );
    expect(contentCalls.length).toBe(1);
  });
});
