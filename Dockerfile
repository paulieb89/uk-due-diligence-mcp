FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
# Keep in sync with pyproject.toml [project.dependencies]
# mcpfleet-obs must be published on PyPI (Gate 1) before this image can build.
# prometheus-client is pulled in transitively via mcpfleet-obs (>=0.20) — no
# other module in this repo imports prometheus_client directly, so it is not
# listed here anymore.
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "fastmcp==3.2.4" \
        "mcp==1.27.0" \
        "httpx==0.28.1" \
        "pydantic==2.13.0" \
        "python-dotenv==1.2.2" \
        "mcpfleet-obs==0.1.0"

# Copy application code
COPY . .

EXPOSE 8080

CMD ["python", "server.py"]
