FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
# Keep in sync with pyproject.toml [project.dependencies]
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "fastmcp==3.2.4" \
        "mcp==1.27.0" \
        "httpx==0.28.1" \
        "pydantic==2.13.0" \
        "python-dotenv==1.2.2" \
        "prometheus-client==0.24.1"

# Copy application code
COPY . .

EXPOSE 8080

CMD ["python", "server.py"]
