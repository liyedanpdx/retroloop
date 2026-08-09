import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { isAxiosError } from "axios";

import { authClient } from "../api/client";
import { displayNameError, emailError, passwordError } from "../auth/validation";

const GENERIC_FAILURE = "Something went wrong. Please try again.";
const TAKEN = "An account with this email already exists";

export function RegisterPage() {
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{
    displayName?: string;
    email?: string;
    password?: string;
  }>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const found = {
      displayName: displayNameError(displayName) ?? undefined,
      email: emailError(email) ?? undefined,
      password: passwordError(password) ?? undefined,
    };
    setErrors(found);
    setFailure(null);
    if (found.displayName || found.email || found.password) {
      return;
    }

    setPending(true);
    try {
      await authClient.post("/api/auth/register", {
        display_name: displayName.trim(),
        email: email.trim(),
        password,
      });
      navigate("/login", { replace: true });
    } catch (error) {
      // The typed fields survive a failure — retyping an email address because
      // it was already taken is a small insult. The password is not echoed
      // anywhere, including into this message.
      setFailure(
        isAxiosError(error) && error.response?.status === 409 ? TAKEN : GENERIC_FAILURE
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center">
      <form onSubmit={onSubmit} noValidate className="w-full max-w-sm space-y-4 p-6">
        <h1>Create your account</h1>

        {failure && (
          <p role="alert">
            {failure}
          </p>
        )}

        <div>
          <label htmlFor="display_name">Display name</label>
          <input
            id="display_name"
            name="display_name"
            type="text"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            aria-invalid={errors.displayName ? true : undefined}
            aria-describedby={errors.displayName ? "display_name-error" : undefined}
            className="w-full border rounded px-3 py-2"
          />
          {errors.displayName && (
            <p id="display_name-error" role="alert">
              {errors.displayName}
            </p>
          )}
        </div>

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
            <p id="email-error" role="alert">
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
            <p id="password-error" role="alert">
              {errors.password}
            </p>
          )}
        </div>

        <button
          type="submit"
          disabled={pending}
          className="btn btn-primary w-full justify-center"
        >
          {pending ? "Creating…" : "Create account"}
        </button>

        <p>
          Already registered? <Link to="/login">Log in</Link>
        </p>
      </form>
    </main>
  );
}
