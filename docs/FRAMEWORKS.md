# FRAMEWORKS.md

## Technologies Catalog

### Runtime & Language

| Technology | Version | Role |
|------|---------|------|
| **Python** | 3.12 | Language runtime |
| **uv** | latest | Package manager & virtual environment tool |
| **FastAPI** | latest | Web framework (ASGI) — now with user_id middleware |
| **Uvicorn** | latest | ASGI server (dependency of FastAPI) |
| **PostgreSQL** | 16-alpine | Database for checkpointer persistence + multi-tenant DBs |

### AI / Agent Frameworks

| Technology | Version | Role |
|------|---------|------|
| **DeepAgents** | latest | Core agent framework. `create_deep_agent()` with per-user isolation |
| **LangChain** | >=1.0,<2.0 | Base framework. DeepAgents depends on this |
| **langchain-core** | >=1.0,<2.0 | Shared base types & interfaces |
| **LangGraph** | >=1.0,<2.0 | Orchestration layer (transitive dep of deepagents) |
| **LangSmith** | >=0.3.0 | Tracing and observability for LLM calls |

### Database & Persistence

| Technology | Version | Role |
|------|---------|------|
| **langgraph-checkpoint-postgres** | compatible with langgraph 1.0 | **PostgresSaver** checkpointer. **Now per-tenant**: each tenant creates its own saver pointing to `deepagent_{user_id}` DB |
| **psycopg** | >=3.0 | PostgreSQL async connection library. Now used for **dynamic tenant DB creation** via `AsyncConnection` and `CREATE DATABASE` |
| **pgvector** | latest | PostgreSQL extension for vector similarity search |

### Security & Validation

| Technology | Version | Role |
|------|---------|------|
| **Pydantic** | bundled with fastapi | Data validation. Now used for `user_id` field validation (pattern, min_length, max_length, blocklist) |
| **pydantic-settings** | bundled with fastapi | Environment variables.  settings |

### HTTP & Streaming

| Technology | Version | Role |
|------|---------|------|
| **sse-starlette** | latest | SSE streaming |
| **FastAPI Middleware** | built-in | `UserMiddleware` (user_id_extractor.py) extracts user_id from headers/query/cookies |

### Tooling & Quality

| Technology | Version | Role |
|------|---------|------|
| **ruff** | latest | Linting |
| **ty** | latest | Type checking |
| **pytest** | latest | Testing |
| **pytest-asyncio** | latest | Async test support |
| **httpx** | latest | HTTP client testing |
| **asyncer** | latest | Concurrency management |

### Docker

| Technology | Version | Role |
|------|---------|------|
| **Docker** | latest | Container runtime |
| **Docker Compose** | >=3.8 | Multi-container orchestration |

---

## Dependencies (pyproject.toml)

```toml
[project]
dependencies = [
    # --- DeepAgents Core ---
    "deepagents",
    "langchain>=1.0,<2.0",
    "langchain-core>=1.0,<2.0",
    "langsmith>=0.3.0",

    # --- Persistence ---
    "langgraph-checkpoint-postgres",
    "psycopg[binary]>=3.0",   # already present; used for tenant DB creation now

    # --- Web ---
    "fastapi[standard]>=0.115.0",
    "sse-starlette",

    # --- Dev ---
    "ruff",
    "pytest",
    "httpx",
    "asyncer",
]
```

**No new external dependencies needed.** Everything is already in `pyproject.toml`.

---

## Multi-Tenancy Configuration

### New Environment Variables (`.env.example`)



### User ID Validation Rules

```python
# Format: ^[a-zA-Z0-9_-]+$
# Length: 1-53 characters (63 max PG DB name - 10 for "deepagent_" prefix)
# Blocklist: postgres, template0, template1, any starting with "pg_"
# Source: X-User-ID header (primary) → user_id query param (fallback) → cookie (fallback)
```

---

## Environment Variables Required

```bash
# PostgreSQL
POSTGRES_USER=postgres
POSTGRES_PASSWORD=devpass
POSTGRES_DB=chatdb
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# LLM Provider (choose one)
OPENAI_API_KEY=sk-proj-xxx
# OR
ANTHROPIC_API_KEY=sk-ant-xxx

# Model Selection
LLM_MODEL=claude-sonnet-4-5-20250929

# LangSmith (optional)
LANGSMITH_API_KEY=ls-proj-xxx
LANGSMITH_PROJECT=deepagent-postgres

# Chat Service
CHAT_SERVICE_PORT=8000
MAX_MESSAGES_PER_THREAD=100

```

---

## Known Risks & Considerations (Updated)

| Risk | Details | Mitigation |
|------|--|------------|
| **DeepAgents version instability** | Pin to tested version in production | `DEEPAGENTS>=0.x,<1.0` |
| **PostgresSaver `.setup()` on already-initialized DB** | Wrap in try/except | Already handled by existing logic |
| **Memory exhaustion from many tenants** | TTL eviction + max cache size | `TENANT_DEFAULT_TTL_SECONDS` + `TENANT_MAX_CACHE_SIZE` |
| **SQL injection via user_id** | user_id validated at middleware | Regex pattern + blocklist + PG max name enforcement |
| **Race conditions on first use** | Two requests from same user creating DB simultaneously | `asyncio.Lock` + double-check locking |
| **No authentication** | All endpoints open | Add API key or JWT middleware in future |
| **Privilege escalation via user_id** | user_id maps directly to DB name | Physical DB isolation is strong enough; admin DB user is always `postgres` |
| **Resource exhaustion from orphaned DBs** | DBs created but never cleaned up | TTL eviction + manual DELETE endpoint + periodic cleanup task |
| **FilesystemBackend not for production** | DeepAgents' FilesystemBackend allows arbitrary file access | Use StateBackend in web context; never mount FilesystemBackend |
