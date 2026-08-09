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

One consequence worth knowing before writing a test: the marker is written by
`POST /api/cycles/{id}/feedback`, so a card inserted straight into the database
does not register participation. There is no other way to create a card through
the product, but a test that builds one at the document level has to set the
marker itself.

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

### Whole-ballot retraction before results open (issues #8, #21)
A member may withdraw their entire ballot with `DELETE /api/retros/{id}/votes`
only while the retro is in `vote` and results have never been visible. There is
no ballot `PUT` or `PATCH`: after a successful delete the member may use the
existing atomic `POST` to submit a fresh 1-to-3-vote ballot.

Results visibility is monotonic. The retro records when results first open
(because a successful ballot submission makes every then-current member voted,
or the facilitator advances to `discuss`); later membership changes neither
open nor close results and cannot re-enable retraction. If membership changes
leave the remaining voters ready, the facilitator may advance normally. This
preserves #8's anti-gaming boundary while giving a member a way to correct a
mistake before anyone can see the tally.

### Results visibility is a stored timestamp, not a count (issue #21)
`Retrospective.voting_results_opened_at`, set once by whichever comes first — a
ballot that completes the then-current membership, or the facilitator advancing
out of `vote` — and never cleared or moved. `results_are_open()` reads it.

It used to ask "has every current member voted?", which had two faults that only
appear when the member list moves. Removing the last non-voter silently
published the tally, and adding a member afterwards could take it away again.
Withdrawal needs a boundary that cannot move backwards, so the boundary is a
fact about the past rather than a question about the present.

### Withdrawal is one conditional write (issue #21)
`DELETE /api/retros/{id}/votes` ends in a single `update_one` filtered on the
results still being closed *and* this caller's ballot still being there. Two
simultaneous withdrawals therefore produce one `204` and one `404`, and a
withdrawal racing the submission that opens results loses — rather than both
reading a stale document and both succeeding. When the conditional write matches
nothing, the document decides which answer is right: `409` if results opened,
`404` if the ballot had already gone.

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

### Deleting a transcript is explicit, and works in every state (issue #25)
`DELETE /api/retros/{id}/transcript`, facilitator-only, `204`. It clears
`transcript` and `ai_suggestions` together — the drafts are derived from the
text and outliving it would defeat the point — while confirmed decisions and
actions stay, because those are the retro's own record in #9's arrays and not
the transcript's.

Explicit deletion rather than automatic expiry: expiry needs a scheduler this
stack does not have, and a silent deletion is worse than none if a team is
relying on it. An expiry policy can be added on top later.

It deliberately does **not** go through #20's writable-phase guard, and works on
a published retro with a closed cycle. This is retention, not a retrospective
write, and a retention control that stops working when the retro finishes is
useless exactly when it is wanted. It is refused only while an extraction is in
flight, `409`, because that task writes the drafts back in its `finally`.

Deleting twice is `204`, and so is deleting a retro that never had a
transcript. The caller asked for it to be gone; it is gone.

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
passed the guard before a concurrent close committed is #34's, below.

### Conditional writes, because this MongoDB is a standalone (issue #34)
Checked rather than assumed: `hello` on the configured server returns
`setName: None`, so it is a standalone and **multi-document transactions are not
available**. The strategy is therefore the other one — put the authority for
"may this still be written?" inside the retro document, so every write's
condition and its data are in the same document, where a single-document update
is atomic.

`Retrospective.writes_closed_at` is that authority. `save_retro()` in
`app/services/concurrency.py` replaces every `retro.save()` with a conditional
replace on it still being null, and answers a lost condition with #20's own
`400` and detail — losing the race and being refused by the sequential guard are
the same event to a caller.

`close_retro_writes()` is the only thing that sets it, in one atomic update, so
two concurrent closes produce one winner. Both closing paths call it **before**
touching the cycle: closing the cycle first would let a mutation that had
already passed #20's guard commit afterwards, which is exactly the hole this
issue exists to fill. Publish additionally makes `phase: discuss` part of the
same condition and sets `phase: done` in the same write, so "only from discuss"
and "only once" stop being a read followed by a write.

Two things this does **not** claim. Mutations still race each other with
last-write-wins on the retro document; #34 is about close-versus-write, and a
version field for write-versus-write would be its own issue. And the two-document
publish is still two writes — the retro is closed first and rolled back if the
cycle write fails, which is as close to atomic as a standalone allows.

The close also terminates an in-flight extraction in the same update, marking a
`processing` suggestion document `failed`. Its terminal write is about to lose
the same condition, and without this the status would sit at `processing`
forever while #17's poller spins — the "no falsely active job" the issue asks
for.

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

### The seven events #12 deferred, and the one family that has none (issue #29)
`vote_submitted` with `{user_id}` and nothing else; `decision_created`,
`decision_updated`, `decision_deleted`; `action_created`, `action_updated`,
`action_deleted`. Create and update carry the same response body their REST
call returns, so a client merges them by id exactly as it does #12's; delete
carries `{id}`.

`vote_submitted` deliberately carries no cluster ids. The room needs the
participation count to move, and handing it the choices would undo #8 in one
line.

**Feedback cards get no events, and cannot.** A card can only be created,
edited or deleted while the cycle is `collecting`, and a retrospective — and
therefore a room — does not exist until reveal. Once it does, #6 freezes the
cards. There is no window in which a feedback mutation has a room to broadcast
to, so the "feedback" half of this issue is answered by the phase model rather
than by an event. Moving a card between clusters is already #12's `card_moved`.

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

### Projects are archived, never deleted (issue #32)
`PATCH /api/projects/{id}/archive` with `{"archived": true|false}`, facilitator
only, reversible, idempotent both ways. There is no delete endpoint and no
cascade: a project's cycles, retrospectives, feedback and actions are the record
of what a team did, and nothing in this product destroys that. #32's own
constraint says not to build cascading deletion until retention and recovery are
decided, and archiving is what makes that decision unnecessary rather than
deferred.

Archiving requires no active cycle, `400` otherwise. That is what makes
"archived" mean something: with nothing in flight, the read-only rule is one
guard on the project's own write endpoints plus one on starting a new cycle,
rather than a check scattered through every feedback and retro path.

An archived project is read-only for everyone — rename, member add, member
remove, role change and new cycles all `400` — and readable by exactly whoever
could read it before.

### A project always has a facilitator (issue #32)
`PATCH /api/projects/{id}/members/{user_id}` with `{"role": ...}` promotes and
demotes, and any facilitator may use it, including on themselves. Demoting the
last facilitator is `400`: there is no administrator above a project to repair
one nobody can run, and its cycles could never be closed again.

Self-demotion is allowed once somebody else is a facilitator, so handing over
and stepping back does not need a third person.

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

### Liveness and readiness are two endpoints (issue #18)
`GET /api/health` says the process is up. `GET /api/ready` says Beanie is
initialised and Mongo answered a `ping`, and is `503 {"status":"not_ready"}`
otherwise. Compose health checks use readiness, and the frontend waits on it —
a backend that cannot reach its database must never get an app in front of it,
and there is no in-memory fallback to degrade to.

The `503` body is two words. A driver error carries the connection string, and
this endpoint is reachable from anywhere the app is. The reason goes to the
container log instead.

### The production Compose file is self-contained (issue #18)
`docker-compose.prod.yml` is a whole file rather than an override, so `-f` on it
cannot inherit a bind mount or a `--reload` from the development file by
accident.

### Required variables have no defaults (issue #18)
`MONGO_URL`, `MONGO_DB_NAME`, `JWT_SECRET` and `JWT_REFRESH_SECRET` use
Compose's `:?` form. Missing one stops the stack before anything starts, naming
the variable — better than a backend that silently came up against
`mongodb://localhost:27017`.

### Multi-stage frontend Dockerfile (issue #18)
Dev mode uses vite dev with hot reload (mounted volume). Prod mode builds
static files and serves via nginx. Compose defaults to dev mode.
