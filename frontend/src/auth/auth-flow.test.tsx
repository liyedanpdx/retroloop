/**
 * The pages, the guards, and the session that survives a reload (#13).
 *
 * Everything is rendered through `AppRoutes` inside a `MemoryRouter`, so the
 * assertions are about what a user at a URL sees, not about which component was
 * called. HTTP is the fake adapter from `test-utils/http`, which leaves the real
 * clients and interceptors in place.
 */
import { StrictMode } from "react";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "../App";
import { getAccessToken, setAccessToken } from "../api/client";
import { ALICE, callsTo, installHttp, type Handler, type Reply } from "../test-utils/http";

const NO_SESSION: Record<string, Handler | Reply> = {
  "POST /api/auth/refresh": { status: 401 },
};

const LIVE_SESSION: Record<string, Handler | Reply> = {
  "POST /api/auth/refresh": { status: 200, data: { access_token: "fresh" } },
  "GET /api/auth/me": { status: 200, data: ALICE },
};

function renderAt(path: string, routes: Record<string, Handler | Reply>) {
  const calls = installHttp(routes);
  render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>
  );
  return calls;
}

beforeEach(() => {
  setAccessToken(null);
});

// --- bootstrap ---------------------------------------------------------------

describe("under StrictMode, which is how main.tsx actually mounts it", () => {
  /**
   * 这一组存在的原因是它抓到了一个真实的挂死。
   *
   * StrictMode 会 mount → unmount → mount。守卫用的 ref 会挺过那次假卸载,
   * 所以第二次挂载什么都不做;而如果第一次挂载的结果在 cleanup 里被丢掉,
   * 状态就永远停在 `loading`,页面永远显示「Restoring your session…」。
   *
   * 其余的测试都不用 StrictMode 渲染,所以全都看不到它 —— 而 `main.tsx` 用。
   */
  function renderStrict(path: string, routes: Record<string, Handler | Reply>) {
    const calls = installHttp(routes);
    render(
      <StrictMode>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </StrictMode>
    );
    return calls;
  }

  it("settles on the login page instead of restoring for ever", async () => {
    renderStrict("/projects", NO_SESSION);

    expect(
      await screen.findByRole("heading", { name: "Log in to RetroLoop" })
    ).toBeInTheDocument();
    expect(screen.queryByText(/Restoring your session/)).not.toBeInTheDocument();
  });

  it("still restores a live session, and still refreshes only once", async () => {
    const calls = renderStrict("/projects", LIVE_SESSION);

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
  });
});

describe("starting the app", () => {
  it("shows a loading state, then the protected page after a silent refresh", async () => {
    const calls = renderAt("/projects", LIVE_SESSION);

    expect(screen.getByRole("status")).toHaveTextContent("Restoring your session");

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
    expect(callsTo(calls, "GET", "/api/auth/me")[0].authorization).toBe("Bearer fresh");
  });

  it("settles as logged out when the refresh cookie is gone, showing no protected view", async () => {
    renderAt("/projects", NO_SESSION);

    expect(await screen.findByRole("heading", { name: "Log in to RetroLoop" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Projects" })).not.toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });

  it("refreshes once for a page load, not once per navigation", async () => {
    const calls = renderAt("/login", { ...NO_SESSION });

    await screen.findByRole("heading", { name: "Log in to RetroLoop" });
    fireEvent.click(screen.getByRole("link", { name: "Register" }));
    expect(await screen.findByRole("heading", { name: "Create your account" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Log in" }));
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    expect(callsTo(calls, "POST", "/api/auth/refresh")).toHaveLength(1);
  });
});

// --- guards ------------------------------------------------------------------

describe("the route guards", () => {
  it("sends an anonymous visitor from /projects to /login", async () => {
    renderAt("/projects", NO_SESSION);
    expect(await screen.findByRole("heading", { name: "Log in to RetroLoop" })).toBeInTheDocument();
  });

  it("sends a signed-in visitor from /login and /register to /projects", async () => {
    renderAt("/login", LIVE_SESSION);
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();

    setAccessToken(null);
    renderAt("/register", LIVE_SESSION);
    expect(await screen.findAllByRole("heading", { name: "Projects" })).not.toHaveLength(0);
  });

  it("routes an unknown path by whether there is a session", async () => {
    renderAt("/nowhere", NO_SESSION);
    expect(await screen.findByRole("heading", { name: "Log in to RetroLoop" })).toBeInTheDocument();

    setAccessToken(null);
    renderAt("/nowhere", LIVE_SESSION);
    expect(await screen.findAllByRole("heading", { name: "Projects" })).not.toHaveLength(0);
  });
});

// --- logging in --------------------------------------------------------------

describe("the login form", () => {
  it("has labelled controls", async () => {
    renderAt("/login", NO_SESSION);
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log in" })).toBeInTheDocument();
  });

  it("checks the fields before sending anything", async () => {
    const calls = renderAt("/login", NO_SESSION);
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByText("Email is required")).toBeInTheDocument();
    expect(screen.getByText("Password is required")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "not-an-email" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByText("Enter a valid email address")).toBeInTheDocument();
    expect(
      screen.getByText("Password must be at least 8 characters")
    ).toBeInTheDocument();

    expect(callsTo(calls, "POST", "/api/auth/login")).toHaveLength(0);
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-describedby", "email-error");
  });

  it("logs in, loads the user and lands on /projects", async () => {
    const calls = renderAt("/login", {
      ...NO_SESSION,
      "POST /api/auth/login": { status: 200, data: { access_token: "fresh", token_type: "bearer" } },
      "GET /api/auth/me": { status: 200, data: ALICE },
    });
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret123" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    const login = callsTo(calls, "POST", "/api/auth/login")[0];
    expect(login.body).toEqual({ email: "alice@example.com", password: "secret123" });
    expect(login.withCredentials).toBe(true);
    expect(login.authorization).toBeUndefined();
    expect(screen.getByText("Alice")).toBeInTheDocument();
  });

  it("names a 401 and stays generic about everything else", async () => {
    let status = 401;
    renderAt("/login", {
      ...NO_SESSION,
      "POST /api/auth/login": () => ({ status, data: { detail: "Invalid credentials" } }),
    });
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrongpassword" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");

    status = 500;
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong")
    );
    // Nothing the server said is rendered.
    expect(screen.queryByText(/Invalid credentials/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Log in to RetroLoop" })).toBeInTheDocument();
  });

  it("disables the button while the request is in flight", async () => {
    let release: (reply: { status: number; data: unknown }) => void = () => {};
    const calls = renderAt("/login", {
      ...NO_SESSION,
      "POST /api/auth/login": () =>
        new Promise<{ status: number; data: unknown }>((resolve) => {
          release = resolve;
        }),
      "GET /api/auth/me": { status: 200, data: ALICE },
    });
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret123" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));

    const button = await screen.findByRole("button", { name: "Logging in…" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(callsTo(calls, "POST", "/api/auth/login")).toHaveLength(1);

    release({ status: 200, data: { access_token: "fresh" } });
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
  });
});

// --- registering -------------------------------------------------------------

describe("the register form", () => {
  it("sends a trimmed display name and goes to /login on 201", async () => {
    const calls = renderAt("/register", {
      ...NO_SESSION,
      "POST /api/auth/register": { status: 201, data: { id: "u1" } },
    });
    await screen.findByRole("heading", { name: "Create your account" });

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "  Alice  " } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret123" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Log in to RetroLoop" })).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/auth/register")[0].body).toEqual({
      display_name: "Alice",
      email: "alice@example.com",
      password: "secret123",
    });
  });

  it("checks every field before sending anything", async () => {
    const calls = renderAt("/register", NO_SESSION);
    await screen.findByRole("heading", { name: "Create your account" });

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Display name is required")).toBeInTheDocument();
    expect(screen.getByText("Email is required")).toBeInTheDocument();
    expect(screen.getByText("Password is required")).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/auth/register")).toHaveLength(0);
  });

  it("names a 409, keeps the other fields, and never echoes the password", async () => {
    let status = 409;
    renderAt("/register", {
      ...NO_SESSION,
      "POST /api/auth/register": () => ({ status, data: { detail: "Email already registered" } }),
    });
    await screen.findByRole("heading", { name: "Create your account" });

    fireEvent.change(screen.getByLabelText("Display name"), { target: { value: "Alice" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret123" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An account with this email already exists"
    );
    expect(screen.getByRole("heading", { name: "Create your account" })).toBeInTheDocument();
    expect(screen.getByLabelText("Display name")).toHaveValue("Alice");
    expect(screen.getByLabelText("Email")).toHaveValue("alice@example.com");
    expect(document.body.textContent).not.toContain("secret123");

    status = 503;
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong")
    );
    expect(screen.queryByText(/Email already registered/)).not.toBeInTheDocument();
  });
});

// --- the layout and logging out ----------------------------------------------

describe("the protected layout", () => {
  it("shows the display name and logs out", async () => {
    const calls = renderAt("/projects", {
      ...LIVE_SESSION,
      "POST /api/auth/logout": { status: 204 },
    });
    await screen.findByRole("heading", { name: "Projects" });
    expect(screen.getByText("Alice")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Logout" }));

    expect(await screen.findByRole("heading", { name: "Log in to RetroLoop" })).toBeInTheDocument();
    expect(callsTo(calls, "POST", "/api/auth/logout")[0].withCredentials).toBe(true);
    expect(getAccessToken()).toBeNull();
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();
  });

  it("logs out locally even when the logout request fails", async () => {
    renderAt("/projects", { ...LIVE_SESSION, "POST /api/auth/logout": { status: 500 } });
    await screen.findByRole("heading", { name: "Projects" });

    fireEvent.click(screen.getByRole("button", { name: "Logout" }));

    expect(await screen.findByRole("heading", { name: "Log in to RetroLoop" })).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
  });

  it("links to the user guide once signed in (#41)", async () => {
    renderAt("/projects", LIVE_SESSION);
    await screen.findByRole("heading", { name: "Projects" });

    const guide = screen.getByRole("link", { name: "Guide" });
    expect(guide).toHaveAttribute(
      "href",
      "https://github.com/liyedanpdx/retroloop/blob/develop/docs/user-guide.md"
    );
    expect(guide).toHaveAttribute("target", "_blank");
  });

  it("has no guide link before signing in (#41)", async () => {
    renderAt("/login", {});
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    expect(screen.queryByRole("link", { name: "Guide" })).not.toBeInTheDocument();
  });
});

// --- storage -----------------------------------------------------------------

describe("browser storage", () => {
  it("is never written during a whole login and logout", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const cookie = vi.spyOn(document, "cookie", "set");

    renderAt("/login", {
      ...NO_SESSION,
      "POST /api/auth/login": { status: 200, data: { access_token: "fresh" } },
      "GET /api/auth/me": { status: 200, data: ALICE },
      "POST /api/auth/logout": { status: 204 },
    });
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "alice@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret123" } });
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    await screen.findByRole("heading", { name: "Projects" });
    fireEvent.click(screen.getByRole("button", { name: "Logout" }));
    await screen.findByRole("heading", { name: "Log in to RetroLoop" });

    expect(setItem).not.toHaveBeenCalled();
    expect(cookie).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(window.location.search).toBe("");
    setItem.mockRestore();
    cookie.mockRestore();
  });
});
