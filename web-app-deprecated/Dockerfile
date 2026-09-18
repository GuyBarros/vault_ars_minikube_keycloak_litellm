# syntax=docker/dockerfile:1
# Build for multiple architectures: linux/amd64, linux/arm64
# docker buildx build --platform linux/amd64,linux/arm64 -t <image>:<tag> --push .

# ── builder ────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency manifest first for layer caching
COPY requirements.txt ./

# Install production dependencies into /app/.venv
RUN uv venv .venv && \
    uv pip install --python .venv/bin/python --no-cache -r requirements.txt

# ── runtime ────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Copy the pre-built virtualenv
COPY --from=builder /app/.venv /app/.venv

# Copy application source
# Keep root-level modules together so Streamlit pages can import shared modules
# like config.py and observability.py at runtime.
COPY *.py ./
COPY pages/ ./pages/
COPY auth/ ./auth/
COPY components/ ./components/
COPY services/ ./services/
COPY styles/ ./styles/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
