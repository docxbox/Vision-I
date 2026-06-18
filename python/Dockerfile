# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="Vision-I"
LABEL description="Vision-I Python Intelligence API"

WORKDIR /app

# Install curl for Docker healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# Non-root user for security — needs write access to HuggingFace model cache
RUN adduser --disabled-password --gecos "" visionuser \
 && mkdir -p /home/visionuser/.cache/huggingface \
 && chown -R visionuser:visionuser /app /home/visionuser/.cache
USER visionuser

# FastAPI runs on port 8000 (internal only — not exposed publicly)
EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HF_HOME=/home/visionuser/.cache/huggingface

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
