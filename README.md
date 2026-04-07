# Takt — Orchestrate your business with AI

A multi-tenant AI agent platform built on the Claude Agent SDK. Takt provides an "AI Chief of Staff" as a subscription service for small businesses and solo entrepreneurs.

**Built by [Sasara, Inc.](https://sasara.io)**

## What is Takt?

Takt turns Claude into a persistent, context-aware business assistant that lives inside your company. Unlike generic chatbots, Takt agents understand your business context, remember past decisions, and operate on your files and data — all within a secure, tenant-isolated environment.

### Pre-built agents

| Agent | What it does |
|---|---|
| **Morning Planner** | Reviews tasks, prioritizes by urgency/impact, generates a daily plan |
| **Weekly Review** | Aggregates progress, evaluates goals, proposes next week's focus |
| **Monthly Review** | Analyzes KPIs, compares to targets, produces an executive summary |
| **Deal Evaluator** | Scores new opportunities against a checklist, returns Go/No-Go |
| **Content Planner** | Researches trends, suggests topics, drafts content outlines |

## Architecture

```
Client (API)
  → Caddy (HTTPS)
    → FastAPI
      → API key auth → Tenant resolution (cwd isolation)
        → Model router (Haiku for simple queries, Sonnet for decisions)
          → Claude Agent SDK
            → Tenant-scoped file tools (path-restricted)
            → MCP servers (accounting, calendar, etc.)
          → Session & usage tracking (SQLite per tenant)
```

Each tenant gets an isolated data directory:

```
/data/tenants/{tenant_id}/
├── CLAUDE.md       # System prompt (business context & rules)
├── config.json     # Tenant configuration
├── sessions.db     # Sessions & usage metering
├── memory/         # Agent memory (decision logs, learnings)
├── tasks/          # Task management
└── daily/          # Daily plans
```

## Tech stack

- **Language**: Python 3.14
- **Agent engine**: Claude Agent SDK
- **API**: FastAPI + uvicorn
- **Tools**: FastMCP v3
- **Database**: SQLite (per tenant)
- **Proxy**: Caddy
- **Infra**: AWS EC2 + EBS

## Quick start

```bash
# Clone and configure
cp .env.example .env
# Set your ANTHROPIC_API_KEY in .env

# Run with Docker Compose
docker compose up -d

# Health check
curl http://localhost:8000/health
```

## API

### `POST /chat`

Chat with the tenant's AI agent.

```bash
curl -X POST http://localhost:8000/chat \
  -H "X-API-Key: your-tenant-key" \
  -H "Content-Type: application/json" \
  -d '{"message": "Prioritize my tasks for this week"}'
```

### `GET /files`

List files in the tenant's data directory.

### `GET /files/{path}` / `PUT /files/{path}`

Read or write files within the tenant's sandbox.

## Security

- **Tenant isolation**: Each tenant is restricted to its own data directory via path validation. Built-in file tools (Read/Write/Bash) are disabled; only sandboxed custom tools are available to the agent.
- **Authentication**: API key per tenant, passed via `X-API-Key` header.
- **Path traversal protection**: All file paths are resolved and validated against the tenant root.

## License

Proprietary. All rights reserved.
