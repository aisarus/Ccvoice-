# Voice Shell — один контейнер: клиент, WebSocket и Claude Code рядом.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# claude-agent-sdk запускает CLI Claude Code, поэтому он нужен в образе.
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

WORKDIR /app
COPY daemon/requirements.txt daemon/requirements.txt
RUN pip install --no-cache-dir -r daemon/requirements.txt

COPY spec ./spec
COPY client ./client
COPY daemon ./daemon

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/daemon \
    WORKSPACE_DIR=/tmp/workspace \
    NOTE_PATH=/tmp/workspace/inbox.md \
    PORT=8787

EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

CMD ["python", "-m", "voice_claude"]
