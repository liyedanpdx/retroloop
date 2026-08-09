import { Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";

/**
 * The shell every signed-in route renders inside (#13).
 *
 * #14, #16 and #17 hang their pages off this `Outlet`, so the nav bar and the
 * logout button are written once rather than once per feature page.
 */
export function ProtectedLayout() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen">
      <nav className="topbar">
        <span className="wordmark">RetroLoop</span>
        <div className="flex items-center gap-4">
          {user && <span className="meta">{user.display_name}</span>}
          <button type="button" onClick={() => void logout()} className="btn-link">
            Logout
          </button>
        </div>
      </nav>
      <main className="page rise">
        <Outlet />
      </main>
    </div>
  );
}
