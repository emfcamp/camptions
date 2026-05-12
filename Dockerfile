FROM python:3.11-slim

WORKDIR /app

# Install dependencies — cached unless pyproject.toml changes.
# Stub src/ lets pip resolve all external deps without the real source.
COPY pyproject.toml .
RUN mkdir -p src/camptions && touch src/camptions/__init__.py \
    && pip install --no-cache-dir . \
    && rm -rf src/

# Copy source — only these layers re-run on src/ changes.
COPY src/ src/
COPY static/ static/
RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "camptions.main:app", "--host", "0.0.0.0", "--port", "8000"]
