# Decisions

Calls already made. Read this before grooming or implementing.
Do not reopen a decision without changing it here first.

## Auth

### Token storage (issue #13)
Access token in memory (React ref/state), refresh token in httpOnly cookie.
localStorage is vulnerable to XSS. Page refresh loses the access token but
the silent refresh flow recovers it automatically.

### Axios interceptor handles refresh (issue #13)
When any API call returns 401, the interceptor tries POST /api/auth/refresh
once. If refresh succeeds, the original request is retried. If refresh fails,
redirect to /login. Auth logic stays out of individual components.

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

### Owner matching is best-effort (issue #10)
AI extraction returns owner names as strings. The backend does a best-effort
match against project member display names. Unmatched owners are flagged for
the facilitator to resolve during confirmation.

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

### Multi-stage frontend Dockerfile (issue #18)
Dev mode uses vite dev with hot reload (mounted volume). Prod mode builds
static files and serves via nginx. Compose defaults to dev mode.
