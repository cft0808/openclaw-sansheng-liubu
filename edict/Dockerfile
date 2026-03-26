FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    curl \
    git \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# 安装 OpenClaw CLI（官方 local-prefix 安装方式，避免依赖宿主机 Node）
RUN curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install-cli.sh \
    | bash -s -- --prefix /opt/openclaw

ENV PATH="/opt/openclaw/bin:${PATH}" \
    HOME=/app \
    OPENCLAW_HOME=/app \
    OPENCLAW_STATE_DIR=/app/.openclaw \
    OPENCLAW_CONFIG_PATH=/app/.openclaw/openclaw.json

# 安装 Python 依赖
COPY edict/backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 复制后端代码
COPY edict/backend/ /app/

# 复制 Alembic 配置
COPY edict/alembic.ini /app/alembic.ini
COPY edict/migration/ /app/migration/

# 注入 OpenClaw 的最小状态目录和 agent workspace 骨架
COPY docker/demo_data/openclaw.json /app/.openclaw/openclaw.json
COPY agents/ /tmp/agents/
RUN set -eu; \
    for soul in /tmp/agents/*/SOUL.md; do \
        agent="$(basename "$(dirname "$soul")")"; \
        workspace="/app/.openclaw/workspace-${agent}"; \
        mkdir -p "${workspace}/skills"; \
        cp "$soul" "${workspace}/SOUL.md"; \
    done; \
    rm -rf /tmp/agents

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
