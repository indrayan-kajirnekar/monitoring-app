/**
 * Login.jsx — Full-page login form for HyperMonitor.
 *
 * Renders when the user is not authenticated (no valid JWT in localStorage).
 * On successful login, AuthContext stores the token and the parent App
 * re-renders to show the main dashboard.
 */
import React, { useState } from "react";
import { useAuth } from "./AuthContext";

export default function Login() {
  const { login } = useAuth();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPw,   setShowPw]   = useState(false);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    setError("");
    try {
      await login(username.trim(), password);
      // AuthContext updates → App re-renders → Login unmounts automatically
    } catch (err) {
      const msg =
        err?.response?.data?.detail ||
        err?.message ||
        "Login failed. Please check your credentials.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 flex flex-col items-center justify-center px-4">

      {/* ── Card ─────────────────────────────────────────────────────────── */}
      <div className="w-full max-w-sm bg-slate-800 rounded-2xl shadow-2xl overflow-hidden">

        {/* Header band */}
        <div className="bg-blue-600 px-8 py-7 text-center">
          <div className="flex justify-center mb-3">
            <svg className="w-10 h-10 text-white" fill="none" viewBox="0 0 24 24"
                 stroke="currentColor" strokeWidth={1.6}>
              <path strokeLinecap="round" strokeLinejoin="round"
                    d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">HyperMonitor</h1>
          <p className="text-blue-200 text-sm mt-1">Multi-Hypervisor Monitoring Platform</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="px-8 py-8 space-y-5">
          <p className="text-slate-400 text-sm text-center -mt-2 mb-1">
            Sign in to your account
          </p>

          {/* Error banner */}
          {error && (
            <div className="bg-red-900/40 border border-red-700 text-red-300 rounded-lg px-4 py-3 text-sm">
              {error}
            </div>
          )}

          {/* Username */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="root"
              autoComplete="username"
              autoFocus
              required
              className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                         px-4 py-2.5 text-sm placeholder-slate-500
                         focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                         transition"
            />
          </div>

          {/* Password */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Password
            </label>
            <div className="relative">
              <input
                type={showPw ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
                required
                className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                           px-4 py-2.5 text-sm pr-11 placeholder-slate-500
                           focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                           transition"
              />
              {/* Toggle visibility */}
              <button
                type="button"
                onClick={() => setShowPw((v) => !v)}
                className="absolute inset-y-0 right-0 px-3 flex items-center text-slate-400 hover:text-slate-200"
              >
                {showPw ? (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round"
                          d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round"
                          d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round"
                          d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                  </svg>
                )}
              </button>
            </div>
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={loading || !username.trim() || !password}
            className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed
                       text-white font-semibold rounded-lg py-2.5 text-sm transition"
          >
            {loading ? "Signing in…" : "Sign In"}
          </button>
        </form>

        {/* Footer hint */}
        <p className="text-center text-xs text-slate-600 pb-5">
          Default root password: <span className="text-slate-500 font-mono">Indrayan@123</span>
        </p>
      </div>

      <p className="text-slate-700 text-xs mt-8">HyperMonitor v4.0 · VMware ESXi · KVM · Hyper-V</p>
    </div>
  );
}
