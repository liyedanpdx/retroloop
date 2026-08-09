/**
 * The bearer header, and the refresh-once-retry-once rule (#13).
 *
 * These are the tests that would still matter if the pages were rewritten: an
 * interceptor that refreshes twice, or loops, or sends five refreshes for five
 * concurrent failures, breaks the whole app rather than one screen.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  authClient,
  getAccessToken,
  refreshAccessToken,
  setAccessToken,
  setSessionLostHandler,
} from "./client";
import { callsTo, installHttp } from "../test-utils/http";

beforeEach(() => {
  setAccessToken(null);
  setSessionLostHandler(() => {});
});

describe("the request interceptor", () => {
  it("attaches the in-memory token, and nothing when there is none", async () => {
    const calls = installHttp({ "GET /api/projects": { status: 200, data: [] } });

    await api.get("/api/projects");
    expect(calls[0].authorization).toBeUndefined();

    setAccessToken("token-1");
    await api.get("/api/projects");
    expect(calls[1].authorization).toBe("Bearer token-1");
  });

  it("sends cookies on both clients", async () => {
    const calls = installHttp({
      "GET /api/projects": { status: 200 },
      "POST /api/auth/login": { status: 200, data: { access_token: "t" } },
    });

    await api.get("/api/projects");
    await authClient.post("/api/auth/login", {});

    expect(calls.every((call) => call.withCredentials)).toBe(true);
  });

  it("never puts a token on the auth client", async () => {
    setAccessToken("token-1");
    const calls = installHttp({ "POST /api/auth/refresh": { status: 200, data: { access_token: "t" } } });

    await refreshAccessToken();

    expect(calls[0].authorization).toBeUndefined();
  });
});

describe("a 401 on a protected request", () => {
  it("refreshes once, retries once, and returns the retried response", async () => {
    setAccessToken("stale");
    const calls = installHttp({
      "GET /api/projects": (_call, seen) =>
        seen === 0 ? { status: 401 } : { status: 200, data: ["a project"] },
      "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
    });

    const response = await api.get("/api/projects");

    expect(response.data).toEqual(["a project"]);
    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
    const attempts = callsTo(calls, "GET", "/api/projects");
    expect(attempts.map((call) => call.authorization)).toEqual(["Bearer stale", "Bearer fresh"]);
    expect(getAccessToken()).toBe("fresh");
  });

  it("shares one refresh between concurrent failures", async () => {
    setAccessToken("stale");
    const calls = installHttp({
      "GET /api/a": (_call, seen) => (seen === 0 ? { status: 401 } : { status: 200 }),
      "GET /api/b": (_call, seen) => (seen === 0 ? { status: 401 } : { status: 200 }),
      "GET /api/c": (_call, seen) => (seen === 0 ? { status: 401 } : { status: 200 }),
      "POST /api/auth/refresh": async () => {
        await new Promise((resolve) => setTimeout(resolve, 5));
        return { status: 200, data: { access_token: "fresh" } };
      },
    });

    const responses = await Promise.all([
      api.get("/api/a"),
      api.get("/api/b"),
      api.get("/api/c"),
    ]);

    expect(responses.map((response) => response.status)).toEqual([200, 200, 200]);
    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
    for (const url of ["/api/a", "/api/b", "/api/c"]) {
      expect(callsTo(calls, "GET", url)[1].authorization).toBe("Bearer fresh");
    }
  });

  it("gives up when the refresh fails, without looping", async () => {
    setAccessToken("stale");
    const lost = vi.fn();
    setSessionLostHandler(lost);
    const calls = installHttp({
      "GET /api/projects": { status: 401 },
      "POST /api/auth/refresh": { status: 401 },
    });

    await expect(api.get("/api/projects")).rejects.toMatchObject({ response: { status: 401 } });

    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
    expect(callsTo(calls, "GET", "/api/projects")).toHaveLength(1);
    expect(lost).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
  });

  it("gives up when the retry is refused too", async () => {
    setAccessToken("stale");
    const lost = vi.fn();
    setSessionLostHandler(lost);
    const calls = installHttp({
      "GET /api/projects": { status: 401 },
      "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
    });

    await expect(api.get("/api/projects")).rejects.toMatchObject({ response: { status: 401 } });

    expect(callsTo(calls, "GET", "/api/projects")).toHaveLength(2);
    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
    expect(lost).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
  });

  it("does not intercept the refresh call itself", async () => {
    const calls = installHttp({ "POST /api/auth/refresh": { status: 401 } });

    expect(await refreshAccessToken()).toBeNull();
    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
  });

  it("leaves other failures alone", async () => {
    setAccessToken("token-1");
    const calls = installHttp({ "GET /api/projects": { status: 403 } });

    await expect(api.get("/api/projects")).rejects.toMatchObject({ response: { status: 403 } });
    expect(callsTo(calls, "GET", "/api/projects")).toHaveLength(1);
    expect(getAccessToken()).toBe("token-1");
  });
});

describe("token storage", () => {
  it("is never written to a browser store", async () => {
    const local = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(document, "cookie", "set");
    installHttp({
      "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
      "GET /api/projects": { status: 200 },
    });

    await refreshAccessToken();
    await api.get("/api/projects");

    expect(local).not.toHaveBeenCalled();
    expect(cookie).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    local.mockRestore();
    cookie.mockRestore();
  });
});
