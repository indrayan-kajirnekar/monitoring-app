/**
 * AuthContext.jsx — Global authentication state + axios interceptor.
 *
 * Provides:
 *   { user, permissions, isAuthenticated, login, logout }
 *
 * - Persists the JWT token in localStorage under "hm_token".
 * - Installs an axios request interceptor that attaches the Bearer token
 *   to every outgoing request automatically.
 * - Installs an axios response interceptor that logs out (clears state +
 *   storage) whenever the server returns 401 Unauthorized.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import axios from "axios";

// ── Storage key ───────────────────────────────────────────────────────────────
const TOKEN_KEY = "hm_token";
const USER_KEY  = "hm_user";

// ── Context ───────────────────────────────────────────────────────────────────
const AuthContext = createContext(null);

// ── Provider ──────────────────────────────────────────────────────────────────
export function AuthProvider({ children }) {
  // Rehydrate from localStorage on page load so a refresh keeps you logged in
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) || null);
  const [user,  setUser]  = useState(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || "null"); }
    catch { return null; }
  });

  // ── Derived values ────────────────────────────────────────────────────────
  const permissions = useMemo(
    () => user?.permissions ?? {},
    [user],
  );

  // True when the backend requires the user to set a new password before
  // proceeding.  Checked on every login + token rehydration.
  const mustChangePw = Boolean(user?.must_change_password);

  const isAuthenticated = Boolean(token && user);

  // ── Persist helpers ───────────────────────────────────────────────────────
  const _save = useCallback((tok, userObj) => {
    localStorage.setItem(TOKEN_KEY, tok);
    localStorage.setItem(USER_KEY, JSON.stringify(userObj));
    setToken(tok);
    setUser(userObj);
  }, []);

  const _clear = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
  }, []);

  // ── Axios interceptors ────────────────────────────────────────────────────
  useEffect(() => {
    // Request: inject Bearer token
    const reqId = axios.interceptors.request.use((config) => {
      const storedToken = localStorage.getItem(TOKEN_KEY);
      if (storedToken) {
        config.headers = config.headers ?? {};
        config.headers["Authorization"] = `Bearer ${storedToken}`;
      }
      return config;
    });

    // Response: 401 → force logout
    const resId = axios.interceptors.response.use(
      (res) => res,
      (err) => {
        if (err?.response?.status === 401) {
          _clear();
        }
        return Promise.reject(err);
      },
    );

    return () => {
      axios.interceptors.request.eject(reqId);
      axios.interceptors.response.eject(resId);
    };
  }, [_clear]);

  // ── Public API ────────────────────────────────────────────────────────────

  /**
   * login(username, password) — sends form-encoded POST to /api/auth/login,
   * stores the JWT and user object on success.
   * Returns the user info object or throws on failure.
   */
  const login = useCallback(async (username, password) => {
    // OAuth2PasswordRequestForm expects application/x-www-form-urlencoded
    const body = new URLSearchParams({ username, password });
    const { data } = await axios.post("/api/auth/login", body, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    _save(data.access_token, data.user);
    return data.user;
  }, [_save]);

  /**
   * logout() — clears local state and calls the server logout endpoint.
   */
  const logout = useCallback(async () => {
    try { await axios.post("/api/auth/logout"); } catch { /* ignore */ }
    _clear();
  }, [_clear]);

  /**
   * refreshMe() — re-fetches /api/auth/me and updates user/permissions in
   * state (e.g. after an admin changes your groups).
   */
  const refreshMe = useCallback(async () => {
    try {
      const { data } = await axios.get("/api/auth/me");
      setUser(data);
      localStorage.setItem(USER_KEY, JSON.stringify(data));
    } catch { /* 401 interceptor will handle it */ }
  }, []);

  /**
   * clearMustChangePw() — called after a successful forced password change so
   * the UI unblocks immediately without a full re-login.
   */
  const clearMustChangePw = useCallback(() => {
    setUser((prev) => {
      if (!prev) return prev;
      const updated = { ...prev, must_change_password: false };
      localStorage.setItem(USER_KEY, JSON.stringify(updated));
      return updated;
    });
  }, []);

  const value = useMemo(() => ({
    user, token, permissions, isAuthenticated, mustChangePw,
    login, logout, refreshMe, clearMustChangePw,
  }), [user, token, permissions, isAuthenticated, mustChangePw,
      login, logout, refreshMe, clearMustChangePw]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ── Hook ──────────────────────────────────────────────────────────────────────
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
