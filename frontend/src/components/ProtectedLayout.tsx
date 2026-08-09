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
    <div className="min-h-screen bg-gray-50">
      <nav className="flex items-center justify-between border-b bg-white px-6 py-3">
        <span className="font-bold text-gray-900">RetroLoop</span>
        <div className="flex items-center gap-4">
          {user && <span>{user.display_name}</span>}
          <button type="button" onClick={() => void logout()} className="underline">
            Logout
          </button>
        </div>
      </nav>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  );
}
