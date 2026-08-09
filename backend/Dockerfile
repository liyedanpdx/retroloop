# The backend image, in three stages (#18).
#
# `base` installs only what `[project] dependencies` declares, from
# `pyproject.toml` alone, so a change to application source does not invalidate
# the dependency layer. `dev` and `prod` add the source and differ only in the
# command — same image contents, same declared dependencies, so what runs in
# development is what runs in production.
#
# No `.env` is ever copied in. `.dockerignore` excludes it, and Compose passes
# the values at runtime.

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, and by themselves: this layer is rebuilt only when
# pyproject.toml changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app


FROM base AS dev
# Reload is a development convenience and must not leak into prod below.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]


FROM base AS prod
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
