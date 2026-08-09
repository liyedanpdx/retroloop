import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { isAxiosError } from "axios";

import { useAuth } from "../auth/AuthProvider";
import { emailError, passwordError } from "../auth/validation";

const GENERIC_FAILURE = "Something went wrong. Please try again.";
const BAD_CREDENTIALS = "Invalid email or password";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const found = {
      email: emailError(email) ?? undefined,
      password: passwordError(password) ?? undefined,
    };
    setErrors(found);
    setFailure(null);
    if (found.email || found.password) {
      return;
    }

    setPending(true);
    try {
      await login(email.trim(), password);
      navigate("/projects", { replace: true });
    } catch (error) {
      // 401 is the one thing worth naming. Everything else becomes one generic
      // line: a raw response body here is how an upstream URL or a stack trace
      // ends up on a login screen.
      setFailure(
        isAxiosError(error) && error.response?.status === 401 ? BAD_CREDENTIALS : GENERIC_FAILURE
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-gray-50">
      <form onSubmit={onSubmit} noValidate className="w-full max-w-sm space-y-4 p-6">
        <h1 className="text-2xl font-bold text-gray-900">Log in to RetroLoop</h1>

        {failure && (
          <p role="alert" className="text-red-700">
            {failure}
          </p>
        )}

        <div>
          <label htmlFor="email">Email</label>
          <input
            id="email"
            name="email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={errors.email ? true : undefined}
            aria-describedby={errors.email ? "email-error" : undefined}
            className="w-full border rounded px-3 py-2"
          />
          {errors.email && (
            <p id="email-error" role="alert" className="text-red-700">
              {errors.email}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={errors.password ? true : undefined}
            aria-describedby={errors.password ? "password-error" : undefined}
            className="w-full border rounded px-3 py-2"
          />
          {errors.password && (
            <p id="password-error" role="alert" className="text-red-700">
              {errors.password}
            </p>
          )}
        </div>

        <button
          type="submit"
          disabled={pending}
          className="w-full bg-gray-900 text-white rounded py-2 disabled:opacity-50"
        >
          {pending ? "Logging in…" : "Log in"}
        </button>

        <p>
          No account yet? <Link to="/register">Register</Link>
        </p>
      </form>
    </main>
  );
}
