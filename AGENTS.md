# RetroLoop

## Commands

### Backend

- `cd backend && conda run -n newpython pip install -e ".[dev]"` - install dependencies
- `cd backend && conda run -n newpython pytest` - the whole suite
- `cd backend && conda run -n newpython pytest tests/test_health.py` - one test file
- `cd backend && conda run -n newpython uvicorn app.main:app --reload` - run dev server

### Frontend

- `cd frontend && npm install` - install dependencies
- `cd frontend && npx vitest run` - the whole suite
- `cd frontend && npx vitest run src/App.test.tsx` - one test file
- `cd frontend && npm run dev` - run dev server

### Docker

- `docker compose up` - run the full stack
- `docker compose build` - rebuild images

## Rules

- Backend dependencies are added in `backend/pyproject.toml`. Do not add one without asking.
- Frontend dependencies are added in `frontend/package.json`. Do not add one without asking.
- Backend uses Python 3.13 via conda env `newpython`. Never use the system Python.
- Always use `encoding='utf-8'` in Python `open()` calls.
- Never hardcode credentials in code. Use `.env` for secrets (see `.env.example`).
- Test scripts go in their respective `tests/` directories, never in the project root.
