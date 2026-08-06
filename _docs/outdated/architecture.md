# Architecture — Weekly Team Feedback Tool

## Tech Stack

- **Frontend:** React (Vite) + TypeScript + Tailwind CSS + shadcn/ui
- **Backend:** Python FastAPI (async)
- **Database:** MongoDB (existing instance) + Beanie ODM (async, Pydantic-native)
- **Real-time:** FastAPI WebSockets
- **Background tasks:** FastAPI BackgroundTasks (stdlib, no extra infra)
- **Auth:** JWT (access + refresh tokens)
- **AI:** OpenAI-compatible proxy — model `gpt-5.4` (clustering, transcript extraction)
- **Media:** Transcript paste only for MVP (audio transcription deferred)
- **Deploy:** Docker Compose (backend + frontend only — DB is external)

## Directory Structure

```
project-feedback/
├── _docs/                     # specifications, architecture, task docs
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app entry, CORS, lifespan
│   │   ├── config.py          # settings via pydantic-settings
│   │   ├── database.py        # Motor client, Beanie init
│   │   ├── models/            # Beanie Document models
│   │   │   ├── user.py
│   │   │   ├── project.py
│   │   │   ├── cycle.py
│   │   │   ├── feedback.py
│   │   │   ├── retro.py
│   │   │   └── action.py
│   │   ├── schemas/           # Pydantic request/response schemas (where different from models)
│   │   │   ├── auth.py
│   │   │   ├── feedback.py
│   │   │   └── retro.py
│   │   ├── api/               # route handlers
│   │   │   ├── auth.py
│   │   │   ├── projects.py
│   │   │   ├── cycles.py
│   │   │   ├── feedback.py
│   │   │   ├── retros.py
│   │   │   ├── actions.py
│   │   │   └── ws.py          # WebSocket consumers
│   │   ├── services/          # business logic
│   │   │   ├── auth.py
│   │   │   ├── ai.py          # OpenAI proxy calls (clustering, extraction)
│   │   └── deps.py            # FastAPI dependencies (get_current_user)
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/               # axios client, typed API calls
│   │   ├── hooks/             # useWebSocket, useAuth, useRetro
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx
│   │   │   ├── ProjectPage.tsx
│   │   │   ├── FeedbackFormPage.tsx
│   │   │   ├── RetroBoardPage.tsx
│   │   │   ├── UploadPage.tsx
│   │   │   └── SummaryPage.tsx
│   │   ├── components/
│   │   │   ├── FeedbackCard.tsx
│   │   │   ├── ClusterColumn.tsx
│   │   │   ├── VoteButton.tsx
│   │   │   ├── DiscussionTopic.tsx
│   │   │   └── ActionItemRow.tsx
│   │   └── store/             # zustand for client state
│   ├── index.html
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── Dockerfile
└── docker-compose.yml         # backend + frontend (DB is external)

```

## Data Model (MongoDB Collections + Beanie Documents)

```
┌─ users ─────────────────────────────┐
│  _id:  ObjectId                     │
│  email: str (unique index)          │
│  name: str                          │
│  hashed_password: str               │
└─────────────────────────────────────┘

┌─ projects ──────────────────────────┐
│  _id:  ObjectId                     │
│  name: str                          │
│  created_by: Link[User]             │
│  members: [                         │
│    { user_id, role: facilitator |   │
│      member }                       │
│  ]                                  │
└─────────────────────────────────────┘

┌─ cycles ────────────────────────────┐
│  _id:  ObjectId                     │
│  project_id: Link[Project]          │
│  week_label: str                    │
│  status: collecting | retro | closed│
│  created_at: datetime               │
└─────────────────────────────────────┘

┌─ feedback_cards ────────────────────┐
│  _id:  ObjectId                     │
│  cycle_id: Link[Cycle]              │
│  author_id: Link[User] | None      │  ← None when anonymous
│  category: start | stop | continue  │
│  text: str                          │
│  is_anonymous: bool                 │
│  cluster_id: ObjectId | None        │
│  created_at: datetime               │
└─────────────────────────────────────┘

┌─ retrospectives ───────────────────────────────────────┐
│  _id:  ObjectId                                        │
│  cycle_id: Link[Cycle] (unique index)                  │
│  facilitator_id: Link[User]                            │
│  phase: reveal | cluster | vote | discuss | done       │
│  clusters: [                                           │
│    { id: ObjectId, name: str }                         │
│  ]                                                     │
│  votes: [                                              │
│    { user_id: ObjectId, cluster_id: ObjectId }         │
│  ]   (max 3 per user — validated in app logic)         │
│  topics: [                                             │
│    { id: ObjectId, cluster_id: ObjectId,               │
│      status: discussed | skipped | deferred,           │
│      notes: str }                                      │
│  ]                                                     │
│  decisions: [                                          │
│    { id: ObjectId, text: str, is_confirmed: bool }     │
│  ]                                                     │
│  actions: [                                            │
│    { id: ObjectId, description: str,                   │
│      owner_id: ObjectId, due_date: date | None,        │
│      status: open | done, topic_id: ObjectId }         │
│  ]                                                     │
│  meeting_transcript: str | None                        │
│  ai_suggestions: { decisions: [], actions: [] } | None │
│  published: bool                                       │
│  created_at: datetime                                  │
└────────────────────────────────────────────────────────┘
```

MongoDB advantage: clusters, votes, topics, decisions, and actions are embedded in the Retrospective document. One read loads the entire retro state — no joins needed.

## API Endpoints

### Auth
```
POST   /api/auth/register
POST   /api/auth/login          → { access_token, refresh_token }
POST   /api/auth/refresh
GET    /api/auth/me
```

### Projects
```
POST   /api/projects
GET    /api/projects
GET    /api/projects/{id}
POST   /api/projects/{id}/members       (invite)
DELETE /api/projects/{id}/members/{uid}
```

### Cycles
```
POST   /api/projects/{id}/cycles        (create new cycle)
GET    /api/projects/{id}/cycles
GET    /api/cycles/{id}
PATCH  /api/cycles/{id}                 (close cycle)
```

### Feedback
```
POST   /api/cycles/{id}/feedback        (submit card)
GET    /api/cycles/{id}/feedback         (own cards only during collecting)
PATCH  /api/feedback/{id}               (edit own card)
DELETE /api/feedback/{id}
```

### Retrospective
```
POST   /api/cycles/{id}/retro           (start retro — facilitator)
GET    /api/retros/{id}
PATCH  /api/retros/{id}/phase           (advance phase — facilitator)
```

### Clustering
```
POST   /api/retros/{id}/clusters/suggest   (AI suggest — facilitator)
POST   /api/retros/{id}/clusters           (create cluster)
PATCH  /api/clusters/{id}                  (rename)
DELETE /api/clusters/{id}                  (ungroup cards)
PATCH  /api/feedback/{id}/cluster          (move card to cluster)
```

### Voting
```
POST   /api/retros/{id}/votes              (submit up to 3 votes)
GET    /api/retros/{id}/votes/results      (only after voting closes)
```

### Discussion
```
PATCH  /api/topics/{id}                    (mark discussed/skipped/deferred, add notes)
POST   /api/retros/{id}/decisions          (add decision)
PATCH  /api/decisions/{id}                 (confirm/edit)
POST   /api/retros/{id}/actions            (add action item)
PATCH  /api/actions/{id}                   (update status, owner, due date)
```

### Transcript & AI Extraction
```
POST   /api/retros/{id}/transcript         (paste transcript text)
GET    /api/retros/{id}/suggestions         (AI-extracted decisions & actions)
POST   /api/retros/{id}/suggestions/confirm (facilitator approves)
```

### Summary
```
GET    /api/retros/{id}/summary
POST   /api/retros/{id}/summary/publish    (facilitator publishes)
```

## WebSocket Protocol

Single connection per retrospective session:

```
ws://host/ws/retro/{retro_id}?token={jwt}
```

Server broadcasts to all participants in the retro room:

| Event                  | Direction     | Payload                              |
|------------------------|---------------|--------------------------------------|
| `phase_changed`        | server → all  | `{ phase }`                          |
| `cards_revealed`       | server → all  | `{ cards[] }`                        |
| `card_moved`           | server → all  | `{ card_id, cluster_id }`            |
| `cluster_created`      | server → all  | `{ cluster }`                        |
| `cluster_renamed`      | server → all  | `{ cluster_id, name }`               |
| `cluster_deleted`      | server → all  | `{ cluster_id }`                     |
| `voting_closed`        | server → all  | `{ results[] }`                      |
| `topic_status_changed` | server → all  | `{ topic_id, status }`               |
| `extraction_done`      | server → facilitator | `{ suggestions }`              |

Client sends actions through REST — the WebSocket is read-only for non-facilitator events. This avoids duplicating validation logic.

## AI Extraction Flow (FastAPI BackgroundTasks)

```
Facilitator pastes transcript text
  │
  ▼
[BackgroundTask: extract]  →  OpenAI proxy (gpt-5.4): pull decisions, actions, owners, dates
  │
  ▼
Store suggestions as draft in retro document  →  notify facilitator via WebSocket
```

Runs inside `BackgroundTasks` — no extra infra. Audio/video upload + Whisper transcription deferred to a future task.

## Auth Flow

1. User registers or logs in → server returns JWT access token (30 min) + refresh token (7 days)
2. Frontend stores tokens in memory (access) and httpOnly cookie (refresh)
3. Every API request includes `Authorization: Bearer {access_token}`
4. WebSocket auth via query param `?token={access_token}`
5. `deps.get_current_user` dependency decodes JWT and loads user from DB

## Permission Rules

| Action                        | Who                       |
|-------------------------------|---------------------------|
| Submit / edit / delete card   | Card author only          |
| See others' cards             | Nobody until reveal phase |
| Start retro, advance phases   | Facilitator only          |
| Trigger AI clustering         | Facilitator only          |
| Move cards between clusters   | Any participant           |
| Vote                          | Any participant (max 3)   |
| See vote results              | Nobody until voting closes|
| Upload meeting media          | Facilitator only          |
| Confirm AI suggestions        | Facilitator only          |
| Publish summary               | Facilitator only          |
| View published summary        | Any project member        |
| Update own action items       | Action owner              |

## Key Design Decisions

1. **Anonymous = `author_id is None`** — the document literally doesn't know who wrote it.

2. **WebSocket is broadcast-only** — mutations go through REST, which validates and persists, then broadcasts via the channel layer. Keeps validation in one place.

3. **AI suggestions are always drafts** — per plan.md, nothing AI-generated is saved until the facilitator confirms.

4. **Phases are linear** — reveal → cluster → vote → discuss → done. No going back. Simplifies state management for MVP.

5. **Votes embedded in retro document** — array of `{ user_id, cluster_id }`, max 3 per user validated in app logic. Stackable on one cluster.

6. **Retro is a single document** — clusters, votes, topics, decisions, actions all embedded. One read loads the full retro state, no joins.

7. **External services** — MongoDB and OpenAI proxy are pre-existing infrastructure, not managed by this project. Connection details come from .env.

## Infrastructure

**External (pre-existing, not managed by this project):**
- MongoDB at `MONGO_URL` (from .env)
- OpenAI-compatible proxy at `OPENAI_BASE_URL` (from .env)

**Docker Compose (this project):**
```yaml
services:
  backend:     # FastAPI on uvicorn, port 8000
  frontend:    # Vite dev server (dev) or nginx (prod), port 3000
```

Two containers only. DB and AI are external. `docker compose up` starts the app.

**.env.example:**
```
MONGO_URL=mongodb://...
MONGO_DB_NAME=team_feedback
OPENAI_BASE_URL=http://...:8100/v1
OPENAI_API_KEY=...
JWT_SECRET=...
```
