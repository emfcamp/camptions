# Dockerfile for EMF Camptions Server
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install dependencies first — cached unless pyproject.toml changes.
# Stub src/ lets pip resolve and install all external deps without the real source.
COPY pyproject.toml .
RUN mkdir -p src/camptions && touch src/camptions/__init__.py \
    && pip install --no-cache-dir ".[gpu]" \
    && rm -rf src/

# Copy real source — only these layers re-run on src/ changes, not the pip above.
COPY src/ src/
COPY static/ static/
COPY alembic/ alembic/
COPY alembic.ini .

# Install the local package only (no deps to download — all already installed above).
RUN pip install --no-cache-dir --no-deps .

# Pre-download the Whisper model into the image so startup is instant and
# the server works without internet access (e.g. at EMF Camp).
# The volume mount in docker-compose still takes precedence at runtime if present.
# HF_TOKEN is passed as a BuildKit secret so it is never stored in any image layer.
ARG WHISPER_MODEL=small
RUN --mount=type=secret,id=hf_token \
    HF_TOKEN=$(cat /run/secrets/hf_token 2>/dev/null || true) \
    python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}')"

# Create data directory
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "camptions.main:app", "--host", "0.0.0.0", "--port", "8000"]
