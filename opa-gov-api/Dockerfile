# syntax=docker/dockerfile:1.7

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt pyproject.toml README.md ./

RUN python -m pip install --no-cache-dir -r requirements.txt

COPY ./*.py ./

EXPOSE 8000

CMD ["sh", "-c", "uvicorn opa_gov_api:app --host 0.0.0.0 --port ${PORT}"]
