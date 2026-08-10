import { Link, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";

// #41: one guide, linked from wherever a signed-in user already is, rather
// than only findable by someone who goes looking in the repository.
const USER_GUIDE_URL =
  "https://github.com/liyedanpdx/retroloop/blob/develop/docs/user-guide.md";

/**
 * The shell every signed-in route renders inside (#13).
 *
 * #14, #16 and #17 hang their pages off this `Outlet`, so the nav bar and the
 * logout button are written once rather than once per feature page. Nothing
 * here is reachable from `/login` or `/register` — those routes render
 * outside this layout entirely, which is what keeps the guide link (#41)
 * from showing up before there is anything to guide someone through.
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
          <a href={USER_GUIDE_URL} target="_blank" rel="noreferrer" className="btn-link">
            Guide
          </a>
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
