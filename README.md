# RetroLoop

A team retrospective tool: collect Start/Stop/Continue feedback, reveal it,
cluster it, vote on it, discuss it, and publish a summary.

## Before you start

You need two things:

1. **Docker Compose** (Docker Desktop, or Docker Engine with the Compose plugin).
2. **A MongoDB you can reach.** This stack does not run one. That is a
   deliberate call recorded in `_docs/decisions.md`: a bundled Mongo would be a
   second database that quietly diverges from the one the team actually uses.

> `docker compose down` stops containers. It does not stop, delete, reset or
> otherwise manage your MongoDB — the stack never issues a drop, a seed or a
> migration against it.

## Configure

```sh
cp .env.example .env
```

Then edit `.env`. It is ignored by git at every depth and must never be
committed.

**`MONGO_URL`** — an Atlas SRV URL, or a Mongo on your own machine. Inside a
container `localhost` means *the container*, so a host Mongo is
`mongodb://host.docker.internal:27017`; both Compose files map
`host.docker.internal` to the host gateway, so that works on Linux as well as
Docker Desktop. With Atlas, add your IP under **Network Access** first.

**`JWT_SECRET` and `JWT_REFRESH_SECRET`** — two *different* long random
strings:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL`** — the project's
OpenAI-compatible proxy. With the placeholder key the app runs and every
non-AI flow works; transcript extraction and cluster suggestions do not, and
should not be reported as working.

`MONGO_URL`, `MONGO_DB_NAME`, `JWT_SECRET` and `JWT_REFRESH_SECRET` have no
defaults. Leave one out and Compose stops before starting anything, naming the
variable.

## Run it

### Development — hot reload

```sh
docker compose up --build
```

- Backend: <http://localhost:8000> (`BACKEND_PORT` to change it)
- Frontend: <http://localhost:3000> (`FRONTEND_PORT` to change it)

Editing a file under `backend/app` reloads uvicorn. Editing a file under
`frontend/src` updates the page. Neither needs a rebuild — the source is bind
mounted. `node_modules` lives in a container-only volume, so the host mount
cannot replace the Linux dependencies the image installed.

The frontend waits for the backend to be *healthy*, not merely started, so a
backend that cannot reach Mongo never gets an app in front of it.

### Production images, locally

```sh
docker compose -f docker-compose.prod.yml up --build
```

Same source and same declared dependencies, different stage: uvicorn without
`--reload`, and nginx serving the built `dist`. No bind mounts, no dev server,
no `node_modules` in the image. It reaches the same external MongoDB.

Deploying this anywhere public — TLS, secret management, scaling, recovery — is
tracked in [#33](https://github.com/liyedanpdx/retroloop/issues/33) and is not
what these files do.

## Checking it works

Development:

```sh
docker compose config --quiet                       # validates; prints nothing
docker compose up --build -d
docker compose ps                                   # both services healthy
curl -s http://localhost:8000/api/health            # {"status":"ok"}
curl -s http://localhost:8000/api/ready             # {"status":"ready"}
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/projects/example
curl -s http://localhost:3000/api/health            # proxied through the frontend
docker compose logs backend --tail 50
docker compose down
```

Production:

```sh
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml up --build -d
docker compose -f docker-compose.prod.yml ps
curl -s http://localhost:8000/api/ready
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/projects/example
docker compose -f docker-compose.prod.yml logs frontend --tail 50
docker compose -f docker-compose.prod.yml down
```

`/projects/example` returning `200` with the app — rather than an nginx `404` —
is the check that client-side routing survives a refresh. `/api/*` and `/ws/*`
are deliberately outside that fallback: a mistyped API path stays a backend
`404` instead of quietly returning HTML.

**Health and readiness are different questions.** `/api/health` says the process
is up. `/api/ready` says Beanie is initialised and Mongo answered a ping; if it
cannot, the answer is `503 {"status":"not_ready"}` and nothing else — no
connection string, no credential, no stack trace.

### If something is wrong

- `docker compose ps` shows `backend` unhealthy → check `MONGO_URL`, and that
  the host is reachable *from a container*. `docker compose logs backend` has
  the driver's own message.
- Compose exits naming a variable → it is missing from `.env`.
- The frontend never starts → it is waiting on the backend's health check.
  Fix the backend first.

## Working on it directly

```sh
cd backend && conda run -n newpython pytest        # the backend suite
cd frontend && npm ci && npx vitest run            # the frontend suite
cd frontend && npm run build                       # the production bundle
```

The backend suite runs against a real MongoDB, in a database named
`{MONGO_DB_NAME}_test`, and empties it between tests. Point `MONGO_DB_NAME` at
something disposable before running it.

## Where things are written down

- `_docs/decisions.md` — the calls already made, and why
- `_docs/process.md` — how work is organised
- `AGENTS.md` — the rules for changing this repository
