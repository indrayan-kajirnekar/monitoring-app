/**
 * ForceChangePw.jsx — Full-screen mandatory password change overlay.
 *
 * Shown when user.must_change_password === true immediately after login.
 * The overlay cannot be dismissed — the user MUST set a new password before
 * they can access any part of the application.
 *
 * On success:
 *  1. Calls clearMustChangePw() from AuthContext to unblock the UI locally.
 *  2. The backend already cleared the flag in the DB via change-password endpoint.
 */
import React, { useState } from "react";
import axios from "axios";
import { useAuth } from "./AuthContext";

export default function ForceChangePw() {
  const { user, logout, clearMustChangePw } = useAuth();

  const [cur,    setCur]    = useState("");
  const [next,   setNext]   = useState("");
  const [confirm,setConfirm]= useState("");
  const [showPw, setShowPw] = useState(false);
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");

    if (next.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (next !== confirm) {
      setError("New passwords do not match.");
      return;
    }
    if (next === cur) {
      setError("New password must be different from the current password.");
      return;
    }

    setBusy(true);
    try {
      await axios.post("/api/auth/change-password", {
        current_password: cur,
        new_password:     next,
      });
      // Unblock the UI — no re-login required
      clearMustChangePw();
    } catch (err) {
      setError(
        err?.response?.data?.detail ||
        "Password change failed. Please try again."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    // Full-screen overlay — no background click to close, no escape
    <div className="fixed inset-0 bg-slate-950/95 z-[9999] flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-slate-800 border border-amber-600 rounded-2xl shadow-2xl overflow-hidden">

        {/* Warning header */}
        <div className="bg-amber-600 px-6 py-5 flex items-center gap-3">
          <svg className="w-6 h-6 text-white shrink-0" fill="none" viewBox="0 0 24 24"
               stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
                  d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
          </svg>
          <div>
            <p className="text-white font-bold text-base leading-tight">
              Password Change Required
            </p>
            <p className="text-amber-100 text-xs mt-0.5">
              You must set a new password before continuing.
            </p>
          </div>
        </div>

        {/* Form body */}
        <form onSubmit={submit} className="px-6 py-6 space-y-4">
          <p className="text-slate-400 text-sm">
            Welcome, <span className="text-white font-semibold">{user?.username}</span>.
            An administrator has required you to change your password.
          </p>

          {/* Error */}
          {error && (
            <div className="bg-red-900/40 border border-red-700 text-red-300
                            rounded-lg px-4 py-3 text-sm">
              {error}
            </div>
          )}

          {/* Current password */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Current Password
            </label>
            <input
              type={showPw ? "text" : "password"}
              value={cur}
              onChange={(e) => setCur(e.target.value)}
              required
              autoFocus
              placeholder="Your current password"
              className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                         px-4 py-2.5 text-sm placeholder-slate-500
                         focus:outline-none focus:ring-2 focus:ring-amber-500 transition"
            />
          </div>

          {/* New password */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              New Password <span className="text-slate-500">(min 8 characters)</span>
            </label>
            <div className="relative">
              <input
                type={showPw ? "text" : "password"}
                value={next}
                onChange={(e) => setNext(e.target.value)}
                required
                placeholder="New password"
                className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                           px-4 py-2.5 text-sm pr-11 placeholder-slate-500
                           focus:outline-none focus:ring-2 focus:ring-amber-500 transition"
              />
              <button type="button" tabIndex={-1}
                      onClick={() => setShowPw((v) => !v)}
                      className="absolute inset-y-0 right-0 px-3 text-slate-400 hover:text-slate-200">
                {showPw
                  ? <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round"
                            d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
                    </svg>
                  : <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round"
                            d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                      <path strokeLinecap="round" strokeLinejoin="round"
                            d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
                    </svg>
                }
              </button>
            </div>
          </div>

          {/* Confirm new password */}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Confirm New Password
            </label>
            <input
              type={showPw ? "text" : "password"}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              required
              placeholder="Repeat new password"
              className={`w-full bg-slate-700 border text-white rounded-lg px-4 py-2.5
                          text-sm placeholder-slate-500
                          focus:outline-none focus:ring-2 transition
                          ${confirm && next !== confirm
                            ? "border-red-500 focus:ring-red-500"
                            : "border-slate-600 focus:ring-amber-500"}`}
            />
            {confirm && next !== confirm && (
              <p className="text-xs text-red-400 mt-1">Passwords do not match.</p>
            )}
          </div>

          {/* Password strength hints */}
          <ul className="text-xs text-slate-500 space-y-0.5 list-disc list-inside">
            <li className={next.length >= 8          ? "text-green-400" : ""}>At least 8 characters</li>
            <li className={/[A-Z]/.test(next)        ? "text-green-400" : ""}>At least one uppercase letter</li>
            <li className={/[0-9]/.test(next)        ? "text-green-400" : ""}>At least one number</li>
            <li className={/[^A-Za-z0-9]/.test(next) ? "text-green-400" : ""}>At least one special character</li>
          </ul>

          {/* Actions */}
          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={logout}
              className="text-sm text-slate-500 hover:text-slate-300 transition"
            >
              Sign out instead
            </button>
            <button
              type="submit"
              disabled={busy || !cur || !next || !confirm || next !== confirm}
              className="px-5 py-2.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-50
                         disabled:cursor-not-allowed text-white font-semibold rounded-lg
                         text-sm transition"
            >
              {busy ? "Saving…" : "Set New Password"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
