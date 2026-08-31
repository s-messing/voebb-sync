# syntax=docker/dockerfile:1

# Build the virtualenv with uv, then copy just the venv into a clean runtime.
# Both stages sit on the same trixie-slim Python, so the venv stays valid.
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, without the project: this layer only rebuilds when the
# lockfile changes, not on every source edit.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.14-slim-trixie

# tzdata because Loan.days_left is computed from the local date - without it a
# container defaults to UTC and rolls over an hour early in Berlin summer time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 voebb

ENV PATH=/app/.venv/bin:$PATH \
    TZ=Europe/Berlin \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=build /app/.venv /app/.venv

USER voebb
WORKDIR /home/voebb

# Credentials come from the environment (VOEBB_USER, VOEBB_PASSWORD,
# NEXTCLOUD_*); there is no .env inside the image on purpose.
ENTRYPOINT ["voebb-cli"]
CMD ["sync-calendar"]
