/**
 * Field checks that run before any request goes out (#13).
 *
 * Immediate feedback, not a security boundary — the backend validates
 * everything again, and the eight-character rule here is a courtesy so a user
 * finds out now rather than after a round trip.
 */

export const MIN_PASSWORD_LENGTH = 8;

// Deliberately loose. The only email address a client can actually verify is
// one that has received a message, and rejecting an unusual but valid address
// is a worse failure than accepting a typo the backend will refuse.
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function emailError(value: string): string | null {
  if (!value.trim()) {
    return "Email is required";
  }
  return EMAIL.test(value.trim()) ? null : "Enter a valid email address";
}

export function passwordError(value: string): string | null {
  if (!value) {
    return "Password is required";
  }
  return value.length < MIN_PASSWORD_LENGTH
    ? `Password must be at least ${MIN_PASSWORD_LENGTH} characters`
    : null;
}

export function displayNameError(value: string): string | null {
  return value.trim() ? null : "Display name is required";
}
