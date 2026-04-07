FROM python:3.14-slim

# Node.js (Claude CLI の実行に必要)
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Claude CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY templates/ templates/

RUN useradd -m -s /bin/bash takt

ENV PYTHONPATH=src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

USER takt

CMD ["uvicorn", "takt.server:app", "--host", "0.0.0.0", "--port", "8000"]
