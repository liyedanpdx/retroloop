/**
 * The two HTTP clients, and the only place the access token exists (#13).
 *
 * `_docs/decisions.md`, "Token storage": the access token lives in memory. Not
 * in `localStorage`, not in a JavaScript cookie, and not in React state either —
 * a module-level variable in here, which no component can read. A reload loses
 * it and the silent refresh below gets it back.
 *
 * There are two clients on purpose. `api` carries the bearer token and the
 * refresh-and-retry interceptor; `authClient` carries neither. Login, register,
 * refresh and logout go through `authClient`, so a 401 from the refresh call
 * cannot re-enter the interceptor that made it and start refreshing forever.
 */
import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

type Retryable = InternalAxiosRequestConfig & { _retried?: boolean };

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** Requests that must never be intercepted: the auth endpoints themselves. */
export const authClient = axios.create({ withCredentials: true });

/** Everything else. Sends the bearer token, and refreshes once on a 401. */
export const api = axios.create({ withCredentials: true });

api.interceptors.request.use((config) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

/**
 * One refresh at a time, shared by every request waiting on it.
 *
 * Five protected requests failing together must produce one `POST /refresh`,
 * not five. Without the shared promise the extra four are not just wasteful:
 * they race, and four of them would be answered after the token they were
 * refreshing had already been replaced.
 */
let inFlightRefresh: Promise<string | null> | null = null;

export function refreshAccessToken(): Promise<string | null> {
  if (!inFlightRefresh) {
    inFlightRefresh = authClient
      .post("/api/auth/refresh")
      .then((response) => {
        const token: string = response.data.access_token;
        setAccessToken(token);
        return token;
      })
      .catch(() => {
        setAccessToken(null);
        return null;
      })
      .finally(() => {
        inFlightRefresh = null;
      });
  }
  return inFlightRefresh;
}

/**
 * What to do when the session is gone for good, set by the auth provider.
 *
 * The interceptor cannot navigate by itself — it is not a component and has no
 * router — so it calls this, and the provider clears the user and replaces the
 * route with `/login`.
 */
let onSessionLost: () => void = () => {};

export function setSessionLostHandler(handler: () => void): void {
  onSessionLost = handler;
}

api.interceptors.response.use(undefined, async (error: AxiosError) => {
  const original = error.config as Retryable | undefined;

  // Anything that is not a first 401 on a real request is somebody else's
  // problem, and is rejected unchanged so the caller still sees it.
  if (error.response?.status !== 401 || !original || original._retried) {
    return Promise.reject(error);
  }
  original._retried = true;

  const token = await refreshAccessToken();
  if (!token) {
    onSessionLost();
    return Promise.reject(error);
  }

  original.headers.Authorization = `Bearer ${token}`;
  try {
    return await api.request(original);
  } catch (retryError) {
    // Refreshed, retried, still refused: the session is over.
    if ((retryError as AxiosError).response?.status === 401) {
      setAccessToken(null);
      onSessionLost();
    }
    return Promise.reject(retryError);
  }
});
