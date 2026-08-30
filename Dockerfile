# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Node is only needed for the MCP filesystem server, which we install at build
# time so the container needs no network access at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @modelcontextprotocol/server-filesystem@2026.7.10 \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py bash_mcp_server.py ./

# The agent reads/writes and runs code under /project (bind-mount your code
# there). Generated code is confined to /project/workspace.
RUN mkdir /project && useradd --create-home --uid 1000 agent \
    && chown -R agent:agent /app /project
USER agent
WORKDIR /project

ENV OLLAMA_HOST=http://host.docker.internal:11434 \
    CP_PROJECT_DIR=/project \
    CP_FS_SERVER_CMD=mcp-server-filesystem \
    CP_AUTO_PULL=1 \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/app/main.py"]
