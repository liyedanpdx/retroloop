# Running RetroLoop on a box you can reach

This is the loop the project is meant to be developed in: bring the stack up on
a machine, look at it in a browser, file an issue for what is wrong, fix it,
bring it up again. Everything below is about making that turn quickly and
noticing when it did not work.

> **None of these commands has been run against a real Docker daemon.** The
> compose files were written for #18 and reviewed, not executed — the machine
> they were written on had no daemon. Treat the first run as part of #18: if a
> command here is wrong, the fix belongs in this file as much as in the yaml.

## What has to exist first

Docker with the Compose plugin (`docker compose version` — not the old
`docker-compose` binary), and a `.env` in the repository root. There is no
`docker-compose.override.yml`; the two files are complete on their own.

**MongoDB is not in the stack, on purpose.** `_docs/decisions.md` explains it:
a Mongo here would be a second database quietly diverging from the one the team
actually uses. `MONGO_URL` points at a real one. On a machine on the same LAN
that is just its address — `host.docker.internal` is for a Mongo running on the
*same* host as Docker, and using it for a LAN address will fail to resolve.

Four variables have no default and compose refuses to start without them:
`MONGO_URL`, `MONGO_DB_NAME`, `JWT_SECRET`, `JWT_REFRESH_SECRET`. That refusal
is the point — it names the missing variable instead of letting the backend come
up against something it guessed.

### The one that will bite you: `FRONTEND_ORIGIN`

It is the single browser origin allowed to send credentialed requests (#30), and
it must be **the address you type in the browser**, not the address the server
calls itself. Reaching the box at `http://192.168.1.42:3000` while `.env` says
`http://localhost:3000` gives you a page that loads, looks fine, and cannot log
in — the refresh cookie never gets sent and every API call is rejected by CORS.
It reads as a broken login, not a misconfiguration, so it is worth checking
before filing anything.

```
FRONTEND_ORIGIN=http://192.168.1.42:3000    # the host and port YOU open
```

Change the port here too if you set `FRONTEND_PORT`. And keep
`COOKIE_SECURE=false` while serving over plain http — a browser silently drops a
`Secure` cookie on an insecure origin, which looks exactly like a broken login
as well.

### Sharing a database with another machine

Two checkouts pointed at the same `MONGO_URL` with the same `MONGO_DB_NAME` also
share the test database: `conftest.py` uses `{MONGO_DB_NAME}_test` and wipes
every collection after each test. Two suites running at once delete each other's
fixtures, and the failures land on whichever tests happened to be mid-flight —
they look like real regressions in code nobody touched. Either give this machine
its own `MONGO_DB_NAME`, or do not run both suites at the same time.

## Bringing it up

Development — uvicorn with `--reload`, Vite dev server, source bind-mounted:

```bash
docker compose up -d --build
```

Production shape — uvicorn without reload, nginx serving `dist`, no bind mounts:

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

The two are separate files rather than a base and an override, so the production
run cannot inherit a bind mount or a reload flag by accident. They do collide on
ports, so bring one down before starting the other.

## Confirming it actually came up

`up -d` returning 0 means the containers were created, not that the app works.
The stack has healthchecks; read them:

```bash
docker compose ps
```

Both services should reach `healthy`. The frontend will not start at all until
the backend is healthy — `depends_on: service_healthy` is deliberate, because a
UI standing in front of a backend that cannot reach Mongo is the failure most
worth catching early.

The backend's probe is `/api/ready`, which checks the Mongo connection rather
than just answering:

```bash
curl -fsS http://localhost:8000/api/ready     # {"status":"ready"}
```

A backend stuck at `starting` and then `unhealthy` is almost always Mongo:
wrong address, credentials, or an Atlas IP allowlist that does not have this
machine on it. `docker compose logs backend` says which.

## After a fix

```bash
docker compose up -d --build          # rebuilds only what changed
```

In development this is often unnecessary — `backend/app` and `frontend` are
bind-mounted, so a `.py` edit reloads uvicorn and a `.tsx` edit hot-reloads Vite
without any rebuild. A rebuild *is* required when dependencies change
(`pyproject.toml`, `package.json`) or when a Dockerfile changes. In the
production stack every change needs a rebuild, because nothing is mounted.

To start from nothing:

```bash
docker compose down                   # never touches Mongo; it is not in the stack
docker compose up -d --build
```

`down` also drops the `frontend_node_modules` volume only if you pass `-v`.
Doing so forces a fresh `npm ci` on the next build, which is the fix when the
frontend fails on a dependency that was installed for a different platform.

## Before calling something a bug

The stack coming up is not the same as the change being right. Both suites run
outside Docker and are much faster there:

```bash
cd backend && pytest -q               # 612 tests
cd frontend && npm run build && npx vitest run
```

`npm run build` matters as much as the tests: it type-checks, and vitest does
not. A type error can pass every test and still break the build.

If the suites are green and the page is still wrong, that is worth an issue —
it means the behaviour was never covered, and the fix should arrive with the
test that would have caught it.
