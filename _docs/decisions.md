# Decisions

Calls already made. Read this before grooming or implementing.
Do not reopen a decision without changing it here first.

## Auth

### Token storage (issue #13)
Access token in memory (React ref/state), refresh token in httpOnly cookie.
localStorage is vulnerable to XSS. Page refresh loses the access token but
the silent refresh flow recovers it automatically.

### The refresh token is never in a body, either direction (issue #30)
`POST /api/auth/login` returns only the access token and sets the refresh token
in an httpOnly cookie named `refresh_token`, scoped to `/api/auth`.
`POST /api/auth/refresh` reads that cookie; there is no `refresh_token` request
field and no `TokenResponse` carrying one.

Returning it in the login body and asking the client not to store it would be a
convention, not a boundary — the XSS that "Token storage" exists to survive can
read a response body as easily as `localStorage`.

### Logout is unauthenticated (issue #30)
`POST /api/auth/logout` expires the cookie and returns `204`, with no token
required and no error on a repeat call. Requiring a valid access token would
make logging out impossible in the situation it matters most, a session that has
already gone wrong; and the worst a forged call achieves is expiring a cookie
the caller already had.

### Cookie flags and the allowed origin are settings (issue #30)
`COOKIE_SECURE`, `COOKIE_SAMESITE` and `FRONTEND_ORIGIN`, defaulting to the
development values (`false`, `lax`, `http://localhost:3000`). `Secure` cannot be
hard-coded on, because a browser drops a `Secure` cookie over plain HTTP and
localhost could then never log in; it cannot be hard-coded off either. CORS
names that one origin with `allow_credentials=True` and never `*` — a browser
rejects a credentialed response carrying a wildcard, so the safe configuration
is also the only working one.

### Axios interceptor handles refresh (issue #13)
When any API call returns 401, the interceptor tries POST /api/auth/refresh
once. If refresh succeeds, the original request is retried. If refresh fails,
redirect to /login. Auth logic stays out of individual components.

### The frontend talks to a same-origin `/api` (issue #13)
Every request in `src/api` is a relative path. `frontend/nginx.conf` already
proxies `/api/` to the backend in the built image, and the Vite dev server now
proxies it the same way, so the refresh cookie is first-party in development and
in production without a second configuration to keep in step.

### `axios` and `react-router-dom`, and nothing else (issue #13)
The two runtime dependencies the issue authorises. `@testing-library/user-event`
was tried for the form tests and removed again: `fireEvent`, already present, is
enough, and a test-only convenience is not worth a dependency `AGENTS.md`
requires approval for.

## Feedback

### Anonymous cards cannot be edited or deleted (issue #5)
`author_id` is `None` on an anonymous card, so the database has no way to tell
who wrote it and no endpoint can check "are you the author?". Anonymous cards are
therefore write-once: `PATCH` and `DELETE /api/feedback/{id}` return 403 for any
caller.

The alternative was a hidden owner reference used only for permission checks.
Rejected: `_docs/outdated/architecture.md` states anonymous means "the document
literally doesn't know who wrote it", and `plan.md` rejects facilitator-visible
authorship because hidden administrator access discourages honest feedback. A
hidden field is that same access wearing a different name.

Consequence for the frontend (#15): the anonymous checkbox is a commitment. The
UI should say so before the card is created, because there is no undo.

### Participation is a marker on the cycle, not on the card (issue #28)
`Cycle.participants` holds one user id per member who submitted anything in that
cycle, anonymous or not. `submitted_feedback` is that list intersected with the
current member list, which is why a departed member can never push the count
above `total_members`.

It lives on the cycle rather than the card deliberately: a marker on an
anonymous card would be the author reference #5 erased, wearing a different
name. Nothing on the marker can be joined to a card — no card id, no category,
no text, no timestamp — and no endpoint returns it.

Two limits worth stating rather than discovering. In a cycle with exactly one
card, "this member participated" and "this member wrote that card" coincide;
that is the unavoidable cost of an exact count, and it is the same inference
the count alone would allow in a one-member project. And the marker is not
removed when a member deletes their cards, because an anonymous card carries
nothing to recount from — it records that they took part, which remains true.

### Cards freeze at reveal (issue #6)
Once the cycle leaves `collecting`, feedback cards are read-only: `PATCH` and
`DELETE /api/feedback/{id}` return 400. Creation is already blocked at that point.

Raised by QA on #5. Without this, a member can reword or delete a card after the
team has read it, and the retro is discussing something that no longer exists.
The rest of the retro is built on cards being stable — #7 assigns them to
clusters, #8 votes on those clusters — so a card changing underneath is not only
dishonest, it corrupts state that has already been derived from it.

The freeze belongs to #6 rather than a follow-up because #6 is what first makes
`retro` status reachable through the API. Shipping reveal without it would ship a
known hole.

### Making a card anonymous is one-way (issue #5)
`PATCH` with `is_anonymous: true` drops `author_id`. The card cannot be changed
back, and cannot be edited again afterwards, because the author is now unknown.

## Voting

### Vote submission is atomic (issue #8)
Users submit all votes (up to 3) in a single request, not one at a time.
Prevents partial voting and simplifies the "has everyone voted?" check.

### No vote retraction (issue #8)
Once submitted, votes cannot be changed. Keeps implementation simple and
prevents gaming. A "revote" feature can be added post-MVP if needed.

## Discussion

### Topic auto-creation (issue #9)
Topics are generated automatically when entering the discuss phase — the
facilitator does not manually create them. One topic per cluster, ranked
by vote count.

### Action owner permissions (issue #9)
Action owners can only update `status` and `due_date` on their own items,
not `description` or `owner_id`. Only the facilitator can reassign or
rewrite actions.

## AI extraction

### AI proxy, not direct OpenAI (issue #10)
Calls go through the project's OpenAI-compatible proxy (OPENAI_BASE_URL env
var), not directly to OpenAI. Allows swapping models without code changes.

### No audio/video in MVP (issue #10)
The plan mentions audio/video upload, but the MVP covers only pasted
transcript text. Audio/video can be a separate post-MVP issue.

### Suggestions are always drafts (issue #10, plan.md)
AI-extracted items are never auto-saved as confirmed decisions/actions. The
facilitator must explicitly confirm each one.

### The transcript and the AI drafts are the facilitator's (issue #26)
`transcript` and `ai_suggestions` are `null` on `GET /api/retros/{id}` for every
member who is not the facilitator, matching `GET /api/retros/{id}/suggestions`,
which #10 made facilitator-only. Nothing else about the payload changes: same
keys, same values, two fields redacted.

The alternative — leaving them visible, on the grounds that the team sat through
the meeting — was rejected for the reason #26 itself gives. #10 restricted the
dedicated endpoint on the reasoning that AI drafts are the facilitator's review
queue; leaving the same bytes one call away on a payload every member fetches
would have made that a gesture rather than a boundary. It is the argument #6
already accepted when it stripped ballots out of this same response for #8.

The transcript is also the most sensitive thing the product stores: a verbatim
record of who said what, including people who are not on the project. Deleting
it is #25; this decision is only about who may read it.

### httpx is the runtime HTTP client (issues #19, #10)
`httpx` moves from `[project.optional-dependencies] dev` to `[project]
dependencies` in `backend/pyproject.toml`. This is the dependency approval
`AGENTS.md` requires, granted once, for `httpx` only.

Raised by the PM on #7 and split into #19 as a blocker. Of the three options
recorded there, this is the first: it is already installed in the `newpython`
env, already pulled in transitively by Starlette, and already the client the
tests use — so promoting it adds no new package to the lockfile, it only makes
an existing one honest about where it is used. The `openai` SDK was rejected as
heavier than anything else the project depends on, for a single POST to one
endpoint whose response shape we control.

The approval does not generalise. Any other backend dependency still needs its
own decision here first.

### One model name, one setting (issues #19, #10)
The proxy model is a setting, `openai_model`, defaulting to `gpt-5.4`, not a
literal in the extraction code. `Settings` forbids extra keys, so a model name
in `.env` without a matching field is a hard startup failure — the field and
the `.env.example` line land in the same change.

### Owner matching is best-effort (issue #10)
AI extraction returns owner names as strings. The backend does a best-effort
match against project member display names. Unmatched owners are flagged for
the facilitator to resolve during confirmation.

## Closed cycles

### A closed cycle refuses every retrospective write (issue #20)
One shared guard in `app/services/access.py` — `require_writable_phase(retro,
phase)`, which is `require_phase` plus `require_cycle_open` — stands in front of
every phase-gated retro command: phase advance, clusters and card moves, the AI
cluster suggestion, ballots, every discussion mutation, both transcript writes.
No router carries its own copy of the check and it is not middleware.

The refusal is `400` with the fixed detail `The retrospective's cycle is
closed`, not `409`. It is the same kind of refusal as the wrong phase — the
request is well-formed and the caller is entitled to make it, the retrospective
is simply not in a state that accepts it — so it shares that status code and is
told apart by the detail.

### Authorized reads are never closed-gated (issue #20)
No GET calls the guard. A published retro that could not be read afterwards
would defeat publishing it; every read keeps exactly the phase and role rules it
already had, including #10's facilitator-and-`discuss` restriction on
`GET /suggestions`.

### Publish checks before it closes (issue #20)
`POST /summary/publish` calls `require_cycle_open` while the cycle is still
open, so the first publish succeeds and every command after it is refused —
including a second publish, which now fails on the closed cycle as well as on
the `done` phase.

### The guard is a request-time check, not a transaction (issue #20)
It refuses every request that observes an already-closed cycle. A mutation that
passed the guard before a concurrent close committed is #34's, and is
deliberately not claimed here.

## Summary

### Summary is assembled on read (issue #11)
GET /summary aggregates data from the retro document on each request. No
separate summary document — keeps data consistent and avoids sync issues.

### Publish is one-way (issue #11)
Once published, the retro phase moves to done and the cycle closes. There is
no unpublish. If a mistake is found, edit decisions/actions before publishing.

## WebSocket

### JWT in query param (issue #12)
WebSocket browser API does not support custom headers. The token is passed as
a query parameter. Standard practice for WebSocket auth.

### Broadcast helper, not middleware (issue #12)
REST endpoints call a `broadcast(retro_id, event, data)` helper after
successful mutations. Keeps WebSocket logic decoupled from the REST layer.

### No event replay on reconnect (issue #12)
Missed events are not replayed. The client does a full state fetch via
GET /api/retros/{id} on reconnect. Simpler and more reliable than event
replay for MVP.

## Frontend

### dnd-kit for drag-and-drop (issue #16)
Chosen over react-beautiful-dnd (unmaintained) and native HTML5 drag (poor
mobile support). dnd-kit is actively maintained, accessible, and supports
touch devices.

### Phase indicator, not tabs (issue #16)
The retro board shows only the current phase, not tabs for all phases. The
facilitator advances phases linearly. Matches the plan's "no going back"
rule.

### Full state fetch on WebSocket reconnect (issue #16)
On reconnect, the client fetches the full retro state instead of replaying
missed events. Simpler and consistent with the no-replay backend decision.

### Polling for extraction status (issue #17)
Transcript extraction status uses polling (every 2 seconds) instead of a
dedicated WebSocket event. It's a one-time background task, not a real-time
collaborative feature.

### Edit before confirm (issue #17)
The facilitator can edit the text of a suggested decision/action before
confirming it. Avoids a separate "edit after confirm" flow.

### Inline editing for feedback cards (issue #15)
Cards are edited in place (click to edit, blur to save) rather than opening
a modal. Faster for short Start/Stop/Continue entries.

### No drag between feedback categories (issue #15)
A card's category is set on creation and cannot be changed by dragging.
Delete and recreate to recategorize. Avoids confusion with retro board
clustering drag.

### No project edit/delete in MVP (issue #14)
Projects can be created and members managed, but no rename or delete. Keeps
scope small.

## Infrastructure

### MongoDB external (issue #18)
The team uses MongoDB Atlas or a shared local instance. Including MongoDB in
compose would create a second database that diverges. The .env file points
to whatever MongoDB the team uses.

### Tests run against a real MongoDB, in their own database
There is no in-process Mongo. `tests/conftest.py` points at `MONGO_URL` and uses
the database named `{MONGO_DB_NAME}_test`, wiping its collections between tests.
Two consequences worth knowing before running the suite:

- The suite is slow — roughly three minutes for ~100 tests, because every test
  pays a round trip to init Beanie and another to clean up. Budget for it rather
  than assuming a hang.
- `MONGO_DB_NAME` must never name a database that holds anything you care about.
  The teardown empties every collection in `{name}_test`.

### Secrets live in an untracked .env, never in the repo
`.env` at the repo root (compose) and `backend/.env` (pytest and uvicorn, which
resolve `env_file` relative to the working directory). Both are covered by the
root `.gitignore` rule `.env`, which is pathless and so matches at any depth.
`.env.example` carries the key names with placeholder values and is the only one
of the three that is committed.

### Multi-stage frontend Dockerfile (issue #18)
Dev mode uses vite dev with hot reload (mounted volume). Prod mode builds
static files and serves via nginx. Compose defaults to dev mode.
