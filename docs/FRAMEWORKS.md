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
| **pydantic-settings** | bundled with fastapi | Environment variables. Now used for `TENANT_*` settings |

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

```bash
# Multi-tenancy
TENANT_PREFIX=deepagent_                  # Prefix for per-user database names
TENANT_SUPERUSER_DB=postgres              # Base DB for CREATE DATABASE operations
TENANT_DEFAULT_TTL_SECONDS=3600           # Tenant cache TTL before eviction (1 hour)
TENANT_ENFORCE_USER_ID=true               # Reject requests without user_id (401)
TENANT_MAX_CACHE_SIZE=1000                # Maximum tenants held in memory cache
```

### User ID Validation Rules

```python
# Format: ^[a-zA-Z0-9_-]+$
# Length: 1-53 characters (63 max PG DB name - 10 for "deepagent_" prefix)
# Blocklist: postgres, template0, template1, any starting with "pg_"
# Source: X-User-ID header (primary) → user_id query param (fallback) → cookie (fallback)
```

---

## Architecture Overview (Multi-Tenant)

```
                    ┌─────────────────┐
                    │     Client      │
                    │  (Browser/App)  │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                     │
┌───┐   │ X-User-ID header   │ {user_id, msg}     │
│User│──►│ (middleware: user_   │ POST /chat         │
│Mid│◄──┘  id_extractor.py)     │ GET /chat/{tid}    │
└───┘   └────────────────────┘
        │    request.state.user_id injected
        ▼
┌───┐   ┌─────────────────────┐
│Router│──►  /chat  ──►  send_message(user_id, tid, msg)
│(chat)│   │                ──►  get_history(user_id, tid)
└───┘   └─────────┬───────────┘
                  │
        ┌─────────▼──────────┐
        │  Tenant Manager     │  (singleton per-request)
        │  ┌──┐  ┌──┐         │
        │  │Cache│Eviction│    │
        │  └──┘  └──┘         │
        │  GET or CREATE       │
        │  TenantStore         │
        └───┬─────┬───────────┘
            │     │
        ┌───▼──┐ ┌▼─────────┐
        │Tenant│ │CREATE DB │
        │Store │ │(if new)   │
        │Data  │ │          │
        │├──►checkpointer   │
        │├──►store          │
        │├──►backend         │
        │└──►agent           │
        └──────┴─────┬──────┘
                     │
         ┌───────────▼──────────┐
         │ Per-User DeepAgent   │
         │ (isolated instance)  │
         │                      │
         │ agent.ainvoke()      │
         └──────┬───────────────┘
                │
           ┌────▼────────────────────────────────┐
           │  Per-User PostgreSQL Database        │
           │  deepagent_{user_id}                 │
           │  (dynamically created, isolated)      │
           │                                       │
           │  ┌───► checkpoint tables              │
           │  ┌───► store tables                   │
           │  ┌───► backend/agent_files            │
           └──────────────────────────────────────┘
```

### Data Flow Steps

1. **Client sends request** with `user_id` in body (`ChatRequest.user_id`), or via `X-User-ID` header, or query param
2. **UserMiddleware** extracts, validates, and injects `user_id` into `request.state.user_id`
3. **Router** extracts `user_id` from `request.state` and passes it as first argument to service
4. **Service** calls `await get_agent_for_user(user_id)` → routes to TenantManager
5. **TenantManager**:
   - **Cache hit**: returns existing `TenantStore` with all resources
   - **Cache miss**: acquires lock → double-check cache → connect to `postgres` DB via superuser → `CREATE DATABASE deepagent_{user_id}` → build tenant URI → create checkpointer/store/backend → create DeepAgent → cache it → return
6. **Agent** processes with isolated state in `deepagent_{user_id}`
7. **Response** flows back: agent → service → router → client (JSON or SSE)

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

# ─── NEW: Multi-Tenancy ───
TENANT_PREFIX=deepagent_
TENANT_SUPERUSER_DB=postgres
TENANT_DEFAULT_TTL_SECONDS=3600
TENANT_ENFORCE_USER_ID=true
TENANT_MAX_CACHE_SIZE=1000
```

---

## Known Risks & Considerations (Updated)

| Risk | Details | Mitigation |
|------|--|------------|
| **DeepAgents version instability** | Pin to tested version in production | `DEEPAGENTS>=0.x,<1.0` |
| **PostgresSaver `.setup()` on already-initialized DB** | Wrap in try/except | Already handled by existing logic |
| **Memory exhaustion from many tenants** | TTL eviction + max cache size | `TENANT_DEFAULT_TTL_SECONDS` + `TENANT_MAX_CACHE_SIZE` |
| **SQL injection via user_id** | user_id validated at middleware | Regex pattern + blocklist + PG max name enforcement |
| **Tenant DB name length** | PostgreSQL max 63 chars | `max_length=53` on user_id + `deepagent_` prefix |
| **Race conditions on first use** | Two requests from same user creating DB simultaneously | `asyncio.Lock` + double-check locking |
| **No authentication** | All endpoints open | Add API key or JWT middleware in future |
| **Privilege escalation via user_id** | user_id maps directly to DB name | Physical DB isolation is strong enough; admin DB user is always `postgres` |
| **Resource exhaustion from orphaned DBs** | DBs created but never cleaned up | TTL eviction + manual DELETE endpoint + periodic cleanup task |
| **FilesystemBackend not for production** | DeepAgents' FilesystemBackend allows arbitrary file access | Use StateBackend in web context; never mount FilesystemBackend |
