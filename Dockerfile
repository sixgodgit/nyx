FROM python:3.11-slim

LABEL org.opencontainers.image.title="NexSandglass"
LABEL org.opencontainers.image.description="沙漏记忆系统 — 零依赖 AI Agent 记忆引擎"
LABEL org.opencontainers.image.version="3.4.1"

ENV NEXSANDBASE_HOME=/data
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY nexsandglass/ ./nexsandglass/

RUN pip install --no-cache-dir . && \
    echo "NexSandglass ready"

VOLUME ["/data"]

EXPOSE 8765

CMD ["python", "-m", "nexsandglass.interfaces.sandglass_mcp"]
