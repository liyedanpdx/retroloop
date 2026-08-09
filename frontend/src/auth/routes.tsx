/**
 * The two route guards, and the one rule they share (#13).
 *
 * Neither decides anything while `status` is `loading`. A guard that treated
 * "not yet known" as "not logged in" would bounce every reload through
 * `/login`, and the reverse would flash protected content at somebody who
 * turns out not to be allowed to see it.
 */
import { Navigate, Outlet } from "react-router-dom";

import { useAuth } from "./AuthProvider";

function Bootstrapping() {
  return (
    <div role="status" aria-live="polite">
      Restoring your session…
    </div>
  );
}

/** `/projects` and everything under it. */
export function ProtectedRoute() {
  const { status } = useAuth();
  if (status === "loading") {
    return <Bootstrapping />;
  }
  return status === "authenticated" ? <Outlet /> : <Navigate to="/login" replace />;
}

/** `/login` and `/register`, which a signed-in user has no business seeing. */
export function PublicOnlyRoute() {
  const { status } = useAuth();
  if (status === "loading") {
    return <Bootstrapping />;
  }
  return status === "authenticated" ? <Navigate to="/projects" replace /> : <Outlet />;
}
