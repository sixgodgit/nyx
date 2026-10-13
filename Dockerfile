# Nyx 夜神 —— MCP 记忆服务（stdio，纯标准库）。
#
# MCP 走标准输入 / 输出，不监听端口：由 MCP 客户端启动容器，而不是后台常驻。
#   docker build -t nyx-memory .
#   docker run -i --rm -v nyx-data:/data nyx-memory          # MCP 客户端里配置成这条命令
# 带语义检索（本地多语言模型，镜像大很多；模型在首次运行时下载到数据卷）：
#   docker build -t nyx-memory --build-arg EXTRAS="[vector]" .
FROM python:3.11-slim

ARG EXTRAS=""
LABEL org.opencontainers.image.title="nyx-memory" \
      org.opencontainers.image.description="Nyx 夜神 — 单用户、本地的 agent 记忆（MCP 服务，stdio）" \
      org.opencontainers.image.source="https://github.com/sixgodgit/nyx" \
      org.opencontainers.image.licenses="MIT"

ENV NEXSANDBASE_HOME=/data \
    NYX_EMBED_CACHE=/data/models \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md MANIFEST.in LICENSE ./
COPY nexsandglass/ ./nexsandglass/
COPY nexsandglass_hermes/ ./nexsandglass_hermes/
RUN pip install --no-cache-dir ".${EXTRAS}"

VOLUME ["/data"]
CMD ["python", "-m", "nexsandglass.interfaces.sandglass_mcp"]
