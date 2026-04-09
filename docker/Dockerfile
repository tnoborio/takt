FROM python:3.14-slim

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Node.js (Claude CLI の実行に必要)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Claude CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# 依存インストール（キャッシュ効率のためpyproject.toml + uv.lockを先にコピー）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY templates/ templates/
COPY migrations/ migrations/
COPY alembic.ini .
COPY docker/entrypoint.sh .

RUN useradd -m -s /bin/bash takt

ENV PYTHONPATH=src
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

USER takt

CMD ["bash", "entrypoint.sh"]
