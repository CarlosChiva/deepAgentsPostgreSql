# PROJECT_STRUCTURE.md

## Updated Structure (Post Multi-Tenancy)

```
deepagent-postgres/
├── docker-compose.yml                    # [MODIFIED] postgres + chat-api services, ensure init scripts include tenant setup
├── .env.example                          # [MODIFIED] TENANT_* env vars added
├── .env.production                       # Production secrets (gitignored)
├── .gitignore
├── README.md
├── docs/
│   ├── REQUIREMENTS.md                   
│   ├── PROJECT_STRUCTURE.md              
│   ├── PROJECT_STATE.md                  
│   └── FRAMEWORKS.md                     
├── skills-lock.json
├── agents/                               
├── services/
│   └── chat-api/                         
│       ├── pyproject.toml                # [unchanged, all deps already present]
│       ├── uv.lock                       
│       ├── Dockerfile                    
│       ├── .dockerignore                 
│       ├── README.md
│       ├── app/
│       │   ├── __init__.py               # [MODIFIED] export tenant functions
│       │   ├── main.py                   # [MODIFIED] lifespan: remove global singleton init, add tenant middleware
│       │   ├── config.py                 # [MODIFIED] add TENANT_* settings
│       │   ├── agent.py                  # [MODIFIED HEAVILY] per-user agent factory, no global singleton
│       │   ├── database.py               # [MODIFIED HEAVILY] tenant DB creation, remove singletons, add DB creation helpers
│       │   ├── backends.py               # [MODIFIED] tenant-aware backend factory, per-tenant PostgresConfig
│       │   ├── health.py                 # [MODIFIED] add tenant-level health probes
│       │   ├── middleware/               # [NEW DIRECTORY]
│       │   │   ├── __init__.py
│       │   │   └── user_id_extractor.py  # user_id extraction middleware (header → query → cookie)
│       │   ├── tenant_manager.py         # [NEW] core multi-tenancy logic: TenantStore, TenantManager, get_or_create_agent
│       │   ├── routers/
│       │   │   ├── __init__.py
│       │   │   └── chat.py               # [MODIFIED] user_id extraction from request, pass-through to service
│       │   ├── models/
│       │   │   ├── __init__.py
│       │   │   ├── chat.py               # [MODIFIED] user_id field added to ChatRequest/ChatResponse
│       │   │   └── history.py            
│       │   └── services/
│       │       ├── __init__.py
│       │       └── chat_service.py        # [MODIFIED] user_id propagated through layer
│       └── tests/
│           ├── __init__.py
│           ├── conftest.py               # [MODIFIED] add tenant test fixtures
│           ├── test_chat.py              # [MODIFIED] add user_id to every test call
│           ├── test_agent.py             # [MODIFIED] per-user agent tests
│           ├── test_chat_service.py      # [MODIFIED] user_id parameter tests
│           ├── test_tenant_manager.py    # [NEW] TenantManager tests: create, cache, eviction, race conditions
│           ├── test_tenant_isolation.py  # [NEW] verify user data isolation
│           ├── test_user_id_extraction.py  # [NEW] middleware tests: header, query, cookie, validation, blocklist
│           └── test_tenant_db_lifecycle.py  # [NEW] DB creation, initialization, eviction
└── infrastructure/
    └── postgres/
        ├── Dockerfile                    
        └── init/
            ├── 01-init.sql              # [unchanged]: CREATE EXTENSION vector
            └── 02-tenant-setup.sh       # [NEW]: ALTER USER postgres CREATEDB;
```

---

## File-by-File Descriptions (New and Modified)

### NEW FILES

#### `services/chat-api/app/tenant_manager.py` — Core Multi-Tenancy Logic

The most important new module. Contains:

- **`TenantStore`** (dataclass/Pydantic model): Holds per-user state: `user_id`, `tenant_db_name` (format: `deepagent_{user_id}`), `database_url`, `agent`, `checkpointer`, `store`, `backend`, `created_at`, `last_used_at`.
- **`TenantManager`** (async manager with `asyncio.Lock`): Registry/cache with LRU-style eviction. Contains:
  - `_cache: dict[str, TenantStore]` — keyed by user_id
  - `_lock: asyncio.Lock` — prevents race conditions on first DB creation
  - `get_or_create_agent(user_id: str)` — factory: returns agent bound to user-specific checkpointer/store/backend
  - `create_tenant_db(user_id: str)` — Creates DB `deepagent_{user_id}` on first use via superuser connection
  - `get_tenant_store(user_id: str)` — Returns TenantStore, creating it lazily if needed
  - `_build_tenant_db_uri(user_id: str)` — Builds per-user connection string
  - `_init_tenant_schema(tenant_db_uri)` — Calls `.setup()` on the tenant's checkpointer/store within its own DB
  - `remove_tenant(user_id: str)` — Cleanup: closes backend, store, checkpointer, removes from cache
  - `cleanup_expired()` — TTL-based eviction task
- **`get_tenant_manager()`** — singleton accessor for TenantManager instance
- **`reset_tenant_cache()`** — for tests/restart

#### `services/chat-api/app/middleware/__init__.py`

Middleware package init, exports `UserMiddleware`.

#### `services/chat-api/app/middleware/user_id_extractor.py`

FastAPI middleware that extracts `user_id` from request:
- **Priority 1:** `X-User-ID` header (most common)
- **Priority 2:** `user_id` query parameter (fallback)
- **Priority 3:** Cookie (fallback for browser clients)
- **`validate_user_id(user_id)`**: Validates format (non-empty, `^[a-zA-Z0-9_-]+$`, max 53 chars to fit PostgreSQL's 63-char DB name limit after `deepagent_` prefix). Blocklist: `postgres`, `template0`, `template1`, `pg_*` prefixes.
- Injects validated `user_id` into `request.state.user_id`.

#### `infrastructure/postgres/init/02-tenant-setup.sh`

Startup script that:
1. Waits for PostgreSQL to be ready (`pg_isready`)
2. Runs `ALTER USER postgres CREATEDB;` to allow dynamic database creation

#### New test files:
- `services/chat-api/tests/test_tenant_manager.py` — TenantManager, DB creation, cache eviction
- `services/chat-api/tests/test_tenant_isolation.py` — Verifies users cannot access each other's data
- `services/chat-api/tests/test_user_id_extraction.py` — Middleware user_id extraction tests
- `services/chat-api/tests/test_tenant_db_lifecycle.py` — DB creation, initialization, deletion lifecycle

---

### MODIFIED FILES

#### `services/chat-api/app/config.py`

**Add TENANT_* settings:**
```python
class Settings(BaseSettings):
    # ... existing fields ...
    
    # NEW: Tenant / Multi-tenancy config
    TENANT_PREFIX: str = "deepagent_"
    TENANT_SUPERUSER_DB: str = "postgres"
    TENANT_DEFAULT_TTL_SECONDS: int = 3600
    TENANT_ENFORCE_USER_ID: bool = True
    TENANT_MAX_CACHE_SIZE: int = 1000
```

#### `services/chat-api/app/database.py`

**Remove/Deprecate:**
- Module-level singletons (`_checkpointer`, `_store`, etc.)
- `get_checkpointer()` → replaced by `TenantManager.get_tenant_store().checkpointer`
- `get_store()` → replaced by `TenantManager.get_tenant_store().store`
- `init_db()` context manager → replaced by per-tenant lazy init

**Add:**
- `create_tenant_database(tenant_db_name, superuser_uri)` — standalone function to create tenant DB
- `drop_tenant_database(tenant_db_name, superuser_uri)` — for teardown/admin
- `ensure_tenant_schema(db_uri)` — calls `.setup()` on checkpointer within tenant DB

#### `services/chat-api/app/agent.py`

**Remove:** All module-level singletons (`_agent`, `reset_agent()`)

**Modify:**
- `get_agent()` → `get_agent_for_user(user_id: str)` — per-user agent factory that:
  1. Calls `TenantManager.get_or_create(user_id)` to get tenant resources
  2. Creates new `AsyncPostgresSaver` pointing to `{tenant_db_name}`
  3. Creates new `AsyncPostgresStore` pointing to `{tenant_db_name}`
  4. Creates new `PostgresBackend` pointing to `{tenant_db_name}`
  5. Wraps in `CompositeBackend`
  6. Calls `create_deep_agent()` with the above + model
  7. Stores in tenant store, returns the agent
- `agent_reset_for_user(user_id)` — cleanup for specific user

#### `services/chat-api/app/backends.py`

**Remove:** Module-level `_backend` and `_initialized` singletons

**Modify:** `get_pg_backend()` → takes `user_id` or `tenant_config`, retrieves from tenant store

**Add:** `build_tenant_backend_config(user_id)` → `PostgresConfig` with per-tenant database

#### `services/chat-api/app/routers/chat.py`

**Both endpoints now:**
- Accept `request: Request` parameter (to extract `user_id`)
- Extract `user_id` from `request.state.user_id`
- Validate non-empty → return 401 if missing
- Pass `user_id` down to service layer as first parameter

#### `services/chat-api/app/models/chat.py`

**Modify `ChatRequest`:**
- Add `user_id: str` as the FIRST required field
- Validation: `min_length=1`, `max_length=53`, pattern `^[a-zA-Z0-9_-]+$`
- Update `ChatResponse` to include `user_id` in the response (echo back)

#### `services/chat-api/app/services/chat_service.py`

**Modify `send_message()`:**
- Add `user_id` as first mandatory parameter
- Call `await get_agent_for_user(user_id)` (now uses tenant manager, not global singleton)
- Checkpointer/store now retrieved from tenant

**Modify `get_history()`:**
- Add `user_id` as mandatory parameter
- Checkpointer from tenant store

#### `services/chat-api/app/health.py`

- Add `check_tenants()` probe — checks tenant manager cache health
- Add tenant count to health response
- Deprecate global `check_checkpointer()` and `check_agent()` in favor of tenant-level checks

#### `services/chat-api/app/main.py`

- Remove global singleton init from lifespan (replaced by per-tenant lazy init)
- Add tenant middleware to FastAPI app
- Update health endpoint to include tenant-level probes

#### `services/chat-api/app/__init__.py`

- Export new tenant-related functions

#### `docker-compose.yml`

- Ensure postgres init volume includes `02-tenant-setup.sh` in `/docker-entrypoint-initdb.d`

#### `services/chat-api/tests/conftest.py`

- Add tenant test fixtures (TenantManager reset, mock tenant DB creation)

#### `services/chat-api/tests/test_chat.py`

- Add `user_id` parameter (with proper test values) to every existing test call

#### `services/chat-api/tests/test_agent.py`

- Test per-user agent factory, agent isolation between users

---

## Architecture Overview (Multi-Tenant Data Flow)

```
                              ┌─────────────────┐
                              │     Client       │
                              │  (Browser/App)  │
                              └───┬──────┬──────┘
                                  │      │
               ┌──────────────────┘      └──────────────────┐
               │                                              │
    HTTP POST: {user_id, thread_id, message}   GET /chat/{thread_id}?user_id=xxx
    HTTP Headers: X-User-ID: user-abc123
               │                                              │
               └──────────────────┬─────────────────────────┘
                                  │
         ┌────────────────────────▼─────────────────────────┐
         │              FastAPI / Docker                     │
         │              chat-api service                     │
         │                                                   │
         │  ┌─── UserMiddleware (user_id_extractor.py) ──┐ │
         │  │  - Read X-User-ID header (primary)          │ │
         │  │  - Read user_id query param (fallback)      │ │
         │  │  - Read cookie (fallback)                   │ │
         │  │  - Validate format (regex, blocklist)       │ │
         │  │  - Inject request.state.user_id             │ │
         │  └───────────────────┬─────────────────────────┘ │
         │                     ▼                             │
         │  ┌─── Router (chat.py) ───────────────────────┐ │
         │  │  Extracts user_id from request.state        │ │
         │  │  POST /chat  →  send_message(user_id, ...)  │ │
         │  │  GET /chat/{thread_id}  → get_history(     │ │
         │  │    user_id, thread_id)                      │ │
         │  └───────────────────┬─────────────────────────┘ │
         │                     ▼                             │
         │  ┌─── Service Layer (chat_service.py) ────────┐ │
         │  │  send_message(user_id, thread_id, message)  │ │
         │  │  get_history(user_id, thread_id)            │ │
         │  └───────────────────┬─────────────────────────┘ │
         │                     │                             │
         │     ┌───────────────▼───────────────┐            │
         │     │   Tenant Manager (singleton)  │            │
         │     │  ┌─ Cache hit? ─┐ ┌─ No ─┐  │            │
         │     │  │   Return      │ │CREATE │  │            │
         │     │  │   TenantStore │ │  DB   │  │            │
         │     │  │               │ │ →     │  │            │
         │     │  └───────────────┘ │       │  │            │
         │     │                    │ init  │  │            │
         │     └─┬──────────────────┼───┬───┘            │
         │       │                  ▼   ▼               │
         │     ┌─▼────────────── TenantStore ─────────▼─┐ │
         │     │  Per-user isolated state:               │ │
         │     │  ├─ checkpointer → deepagent_{user_id} │ │
         │     │  ├─ store      → deepagent_{user_id}   │ │
         │     │  ├─ backend    → deepagent_{user_id}   │ │
         │     │  └─ agent      → create_deep_agent(...)│ │
         │     └────────────────────────┬───────────────┘ │
         └──────────────────────────────┼─────────────────┘
                                        │
                         ┌──────────────▼──────────────┐
                         │  Per-User DeepAgent (isolated)│
                         │  - model, checkpointer, store│
                         │  - backend, memory, state    │
                         │  All data in deepagent_{uid}  │
                         └──────────────┬──────────────┘
                                        │
                         ┌──────────────▼──────────────┐
                         │    PostgreSQL (Docker)       │
                         │                              │
                         │  postgres      (system DB)   │
                         │  deepagent_user-abc123       │
                         │  deepagent_user-def456       │
                         │  deepagent_user-xyz789       │
                         │  ... (many more per user)    │
                         └──────────────────────────────┘
```

---

## Key Design Decisions

### 1. Dynamic Database Creation
- **Strategy:** Superuser connection string to PostgreSQL `postgres` DB to issue `CREATE DATABASE`
- **No new packages needed** — `psycopg[binary]>=3.0` (already in deps) supports `AsyncConnection` with `createdb`
- PostgreSQL `postgres` user IS the superuser (set during container bootstrap)

### 2. Agent Lifecycle per User
- **Lazy initialization + in-memory TTL cache** with `asyncio.Lock` for race condition protection
- Background eviction task removes idle tenants after TTL expires
- Prevents memory bloat while avoiding reload cost per request

### 3. Initial Database Connection
- Superuser connection always targets the `postgres` database
- Only used for tenant DB creation, not for data operations
- Tenant resources connect to `deepagent_{user_id}` from then on

### 4. Security
- `user_id` validated: `^[a-zA-Z0-9_-]+$`, max 53 chars, blocklist for system names
- Physical database isolation per user — no cross-database SQL queries possible
- No new auth packages needed — user_id is an identifier, not a token

### 5. Database Isolation Model
- **Separate databases per user_id** (explicitly required by requirements)
- More isolation than RLS or schemas; matches spec exactly
- Future-proof for per-tenant grants, extensions, backups

### 6. Tenant DB Naming
- Format: `deepagent_{user_id}`
- `deepagent_` prefix avoids conflict with `pg_*` system tables
- PostgreSQL max DB name: 63 chars → user_id max: 53 chars

### 7. Concurrency
- `asyncio.Lock` in TenantManager prevents double-DB-creation race
- Double-check locking pattern: check cache before and after acquiring lock
