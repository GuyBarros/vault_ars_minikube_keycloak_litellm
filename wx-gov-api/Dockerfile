# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

ARG TARGETPLATFORM
ARG TARGETARCH

WORKDIR /app

RUN if [ "$TARGETARCH" != "amd64" ]; then \
        echo "Unsupported Docker target: ${TARGETPLATFORM}. ibm-watsonx-gov currently publishes Linux wheels only for x86_64, so this image can only be built for linux/amd64." >&2; \
        exit 1; \
    fi

COPY pyproject.toml README.md ./

RUN python -m pip install --no-cache-dir .

COPY ./*.py ./

EXPOSE 8000

CMD ["sh", "-c", "uvicorn ai_guardrails_api:app --host 0.0.0.0 --port ${PORT}"]
