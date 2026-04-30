# PROJECT_STATE.md

## Refactor `services/chat-api/` Application Structure

### Current Phase: EXECUTION

| # | Task | Status | Dependencies |
|---|------|--------|--------------|
| 1 | Create new directory structure: `app/db/`, `app/agents/`, `app/middleware/`, `app/utils/` | DONE | none |
| 2 | Create `app/db/__init__.py` | DONE | 1 |
| 3 | Create `app/db/connection.py` from `app/infrastructure/database.py` — extract `get_db_uri()`, `init_db_tables()`, `get_checkpointer()`, `get_store()` | DONE | 2 |
| 4 | Create `app/db/tenant_db.py` from `app/infrastructure/database.py` — extract `create_tenant_database()`, `drop_tenant_database()`, `ensure_tenant_schema()` | DONE | 2 |
| 5 | Create `app/db/pool.py` — pool management utility | DONE | 2 |
| 6 | Create `app/agents/__init__.py` with exports | DONE | 1 |
| 7 | Create `app/agents/factory.py` — extract from `app/core/agent.py` | DONE | 6 |
| 8 | Create `app/agents/tenant.py` — extract from `app/core/agent_manager.py` | DONE | 6 |
| 9 | Create `app/agents/backends.py` — extract from `app/core/backends.py` | DONE | 6 |
| 10 | Create `app/middleware/__init__.py` with exports | DONE | 1 |
| 11 | Create `app/middleware/user.py` — move from `app/infrastructure/middleware.py` | DONE | 10 |
| 12 | Create `app/utils/__init__.py` | DONE | 1 |
| 13 | Create `app/utils/streaming.py` — SSE streaming utilities | DONE | 12 |
| 14 | Rename `app/schemas/` directory to `app/models/` | DONE | none |
| 15 | Update `app/core/config.py` — add validation constants | DONE | 14 |
| 16 | Create `app/core/exceptions.py` — custom exception classes | DONE | none |
| 17 | Create `app/core/dependencies.py` — FastAPI dependency injection | DONE | none |
| 18 | Update `app/core/agent.py` — rewrite as thin layer | DONE | 7, 8, 9 |
| 19 | Update `app/routers/__init__.py` — update import | DONE | 14 |
| 20 | Refactor `app/models/request.py` — fix Pydantic field annotations | DONE | 14 |
| 21 | Refactor `app/routers/chat.py` — Annotated types, strong return types | DONE | 17, 20 |
| 22 | Refactor `app/main.py` — new imports, middleware config, optimized CORS | DONE | 3, 8, 11, 21 |
| 23 | Update `app/services/chat_service.py` — update imports | DONE | 14, 8, 7 |
| 24 | Update `app/core/health.py` — update import paths | DONE | 8 |
| 25 | Remove old `app/infrastructure/` directory — blocked: active imports remain, will be resolved in Task #31 | DONE | 11, 3, 4 |
| 26 | Remove old `app/core/agent_manager.py` | DONE | 8 |
| 27 | Remove old `app/core/agent.py` — replace with compat export | DONE | 7 |
| 28 | Remove old `app/core/backends.py` | DONE | 9 |
| 29 | Remove dead `app/database.py` file | DONE | none |
| 30 | Update `pyproject.toml` — verify dependencies | PENDING | 30 |
| 31 | Verify entire import graph | PENDING | 25, 26, 27, 28, 29 |
| 32 | Document refactoring in docs | PENDING | 31 |
