# Updated: 2026-03-08 (install build toolchain required by project pre-setup poetry installs)
FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    CODEX_HOME=/root/.codex

WORKDIR /app

# System deps + gh CLI
RUN set -euo pipefail \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      git \
      openssh-client \
      curl \
      nodejs \
      npm \
      procps \
      ca-certificates \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y gh \
    && if ! command -v yarn >/dev/null 2>&1; then npm install -g yarn; fi \
    && rm -rf /var/lib/apt/lists/*

# Poetry install
RUN pip install --no-cache-dir poetry

# Python deps
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-interaction --no-ansi --no-root

# CLI coding agents
RUN set -euo pipefail \
    && npm install -g @openai/codex \
    && npm install -g @google/gemini-cli \
    && npm install -g @anthropic-ai/claude-code

# App
COPY . .
RUN mkdir -p data workspaces projects

EXPOSE 8080
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
