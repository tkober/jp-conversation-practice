# API-only image. The Angular bundle ships in its own nginx image, which also
# reverse-proxies /api and the /ws WebSocket here (see frontend/nginx.conf).
#
# Build context is the repository root, not backend/ — nothing outside this
# directory is needed today, but keeping it consistent with the other stacks
# means the workflow and this file agree.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/app ./app
# The built-in scenarios are baked in and seeded on first start. Anything the
# user edits afterwards lives in Postgres and survives an image update.
COPY backend/scenarios ./scenarios

# DB_URL / DB_USER / DB_PASSWORD / DB_OWNER_USER / DB_OWNER_PASSWORD /
# OPENAI_API_KEY / CORS_ORIGINS are read at runtime (app/config.py) — pass them
# via `docker run --env-file` / -e. The paths below default here but stay
# overridable.
ENV SCENARIOS_DIR=/app/scenarios \
    VOICE_SAMPLE_CACHE_DIR=/app/.voice-samples \
    HOST=0.0.0.0 \
    PORT=8000
EXPOSE 8000

# Shell form so $HOST/$PORT expand at runtime (exec'd via `sh -c`, so uvicorn
# still becomes PID 1's child and receives signals).
CMD ["sh", "-c", "exec uv run uvicorn app.main:app --host \"$HOST\" --port \"$PORT\""]
