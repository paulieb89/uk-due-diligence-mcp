FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv

# Dependencies come from uv.lock — the image installs exactly what the lock
# resolves, transitives included. The previous hand-maintained pip list carried
# a "keep in sync with pyproject.toml" comment and had already drifted: it
# pinned mcpfleet-obs==0.1.0 while pyproject floated >=0.1.0, so the image and
# every other install could disagree. It also pinned only the 5 direct deps,
# leaving ~70 transitives (prometheus-client, starlette, uvicorn, anyio, ...)
# free to move between builds of the same commit.
#
# --no-install-project keeps this layer cached when only application code
# changes; the app is not imported as a package — CMD runs server.py from /app
# with its sibling modules alongside it.
#
# mcpfleet-obs must be published on PyPI before this image can build.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY . .

# Run out of the synced environment rather than the system interpreter.
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

CMD ["python", "server.py"]
