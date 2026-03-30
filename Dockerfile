FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "fastmcp>=2.0.0" \
        "httpx>=0.27.0" \
        "pydantic>=2.7.0" \
        "python-dotenv>=1.0.0"

# Copy application code
COPY . .

EXPOSE 8080

CMD ["python", "server.py"]
