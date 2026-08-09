/**
 * Who is logged in, and the two operations that change it (#13).
 *
 * Components get the user, the bootstrap status, and `login` / `logout`. They
 * do not get the access token: it stays in `src/api/client.ts`, which is what
 * "in memory only" has to mean if it is to survive a component being careless.
 *
 * On mount the provider tries one silent refresh, because a page reload has
 * just thrown the access token away and the refresh cookie is the only thing
 * left that knows the session existed.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";

import {
  api,
  authClient,
  getAccessToken,
  refreshAccessToken,
  setAccessToken,
  setSessionLostHandler,
} from "../api/client";

export type User = {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
};

export type AuthStatus = "loading" | "authenticated" | "anonymous";

type AuthContextValue = {
  user: User | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used inside an AuthProvider");
  }
  return value;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const navigate = useNavigate();
  // StrictMode mounts, unmounts and mounts again in development, so without a
  // guard one page load sends two refreshes. The guard is a ref rather than
  // state because it has to survive that remount — which is also why the effect
  // below must not throw its result away on cleanup: the "unmount" is a
  // simulation, the ref remembers it already ran, and a discarded result would
  // leave the status on `loading` forever.
  const bootstrapped = useRef(false);

  useEffect(() => {
    setSessionLostHandler(() => {
      setUser(null);
      setStatus("anonymous");
      navigate("/login", { replace: true });
    });
  }, [navigate]);

  useEffect(() => {
    if (bootstrapped.current) {
      return;
    }
    bootstrapped.current = true;

    // 没有 cancelled 标记,故意的。这个 effect 一次页面加载只跑一次,由上面的
    // ref 保证;在卸载时丢弃它的结果,只会在 StrictMode 下把状态永远钉在
    // `loading`。真正卸载之后 setState 是无害的 no-op。
    void (async () => {
      const token = getAccessToken() ?? (await refreshAccessToken());
      if (!token) {
        setStatus("anonymous");
        return;
      }
      try {
        const me = await api.get<User>("/api/auth/me");
        setUser(me.data);
        setStatus("authenticated");
      } catch {
        setAccessToken(null);
        setStatus("anonymous");
      }
    })();
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const response = await authClient.post("/api/auth/login", { email, password });
    setAccessToken(response.data.access_token);
    const me = await api.get<User>("/api/auth/me");
    setUser(me.data);
    setStatus("authenticated");
  }, []);

  const logout = useCallback(async () => {
    try {
      await authClient.post("/api/auth/logout");
    } catch {
      // The cookie may already be gone, or the network may be. Either way the
      // user asked to be logged out, and locally they now are.
    }
    setAccessToken(null);
    setUser(null);
    setStatus("anonymous");
    navigate("/login", { replace: true });
  }, [navigate]);

  return (
    <AuthContext.Provider value={{ user, status, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
