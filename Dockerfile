# ⚔️ 三省六部 · Demo Dashboard
# docker run -p 7891:7891 cft0808/sansheng-demo
# Then open: http://localhost:7891

# Stage 1: 构建 React 前端
FROM --platform=${BUILDPLATFORM:-linux/amd64} node:20-alpine AS frontend-build
WORKDIR /build
COPY edict/frontend/package.json edict/frontend/package-lock.json ./
RUN npm ci --silent
COPY edict/frontend/ ./
# Build 输出到 /build/dist（vite.config 中 outDir 是相对路径，这里重写）
RUN npx vite build --outDir /build/dist

# Stage 2: 运行时
FROM --platform=${TARGETPLATFORM:-linux/amd64} python:3.11-slim

WORKDIR /app

# 复制看板核心文件
COPY dashboard/ ./dashboard/
COPY scripts/ ./scripts/

# 复制 React 构建产物
COPY --from=frontend-build /build/dist ./dashboard/dist/

# 注入演示数据（data目录由demo_data提供）
COPY docker/demo_data/ ./data/

# 创建 .openclaw 目录并注入骨架配置（Fix #155: sync_agent_config 依赖此文件）
RUN mkdir -p /app/.openclaw
COPY docker/demo_data/openclaw.json /app/.openclaw/openclaw.json
ENV HOME=/app

# 非 root 用户运行
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 7891

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7891/healthz')" || exit 1

CMD ["python3", "dashboard/server.py", "--host", "0.0.0.0", "--port", "7891"]
