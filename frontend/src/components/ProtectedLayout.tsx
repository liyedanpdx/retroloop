import { Link, Outlet } from "react-router-dom";

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
        {/* 字标是回家的路。之前它只是一段文字,于是从回顾板没有任何
            办法回到项目列表。 */}
        <Link to="/projects" className="wordmark" style={{ borderBottom: "none" }}>
          RetroLoop
        </Link>
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
