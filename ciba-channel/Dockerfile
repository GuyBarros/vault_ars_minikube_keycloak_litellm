# syntax=docker/dockerfile:1
# Build for multiple architectures: linux/amd64, linux/arm64
# docker buildx build --platform linux/amd64,linux/arm64 -t <image>:<tag> --push .
FROM python:3.12-slim

WORKDIR /app
COPY server.py ./

ENV PYTHONUNBUFFERED=1 \
    CIBA_CHANNEL_HOST=0.0.0.0 \
    CIBA_CHANNEL_PORT=8093

EXPOSE 8093

CMD ["python", "server.py"]
