# PROJECT_STRUCTURE.md - Target Architecture

## Target Project Structure for `services/chat-api/app/`

```
app/
├── __init__.py
├── main.py                     # FastAPI app factory with lifespan
│
├── core/                       # FastAPI "core" = shared internals only
│   ├── __init__.py
│   ├── config.py               # Pydantic Settings (current, fine)
│   ├── exceptions.py           # NEW: custom exceptions, error handlers
│   ├── dependencies.py         # NEW: dependency functions (Depends() wrappers, tenant manager getter)
│   └── health.py               # ← from core/health.py (keeps here — health probes are infra-adjacent, not domain)
│
├── models/                     # NEW (was schemas/) — Pydantic data models
│   ├── __init__.py
│   ├── request.py              # ChatRequest (unmoved, fine)
│   └── response.py             # ChatResponse, ChatHistoryResponse, MessageItem
│
├── db/                         # NEW (was infrastructure/database.py scope)
│   ├── __init__.py
│   ├── connection.py           # ← current infrastructure/database.py DB init, connection URIs
│   ├── tenant_db.py            # ← current infrastructure/database.py create/drop tenant DB, ensure_schema
│   └── pool.py                 # NEW: shared connection pool management (checkpointer, store)
│
├── agents/                     # NEW (was core/agent.py, core/agent_manager.py, core/backends.py)
│   ├── __init__.py
│   ├── factory.py              # ← current core/agent.py (singleton agent, model provider selection, build_model, create_deep_agent)
│   ├── tenant.py               # ← current core/agent_manager.py (TenantManager, TenantStore, per-user DB)
│   └── backends.py             # ← current core/backends.py (build_tenant_backend_config, build_backend_for_tenant)
│
├── middleware/                 # NEW (was infrastructure/middleware.py)
│   ├── __init__.py
│   └── user.py                 # ← current infrastructure/middleware.py (validate_user_id, _extract_user_id, UserMiddleware)
│
├── services/                   # business logic (domain layer — stays here)
│   ├── __init__.py
│   └── chat_service.py         # ← current, no changes needed
│
├── routers/                    # API layer (stays here)
│   ├── __init__.py
│   └── chat.py                 # ← current, add Annotated types, router-level Depends, response_model
│
└── utils/                      # NEW: misc helpers that don't fit above
│   ├── __init__.py
│   └── streaming.py            # ← SSE generator patterns extracted from chat_service.py
```

## Rationale for Each Change

| Current | Target | Why |
|---------|-----|-----|
| `app/database.py` (root) | **Deleted** | Dead duplicate of `infrastructure/database.py`. Never imported. |
| `core/agent.py` → `agents/factory.py` | `agents/` | Agent construction, model provider selection, and checkpointer/store lazy init are domain-specific, not framework internals. |
| `core/agent_manager.py` → `agents/tenant.py` | `agents/` | Tenant-per-user agent lifecycle belongs with the agent domain, not FastAPI core. |
| `core/backends.py` → `agents/backends.py` | `agents/` | Backend configuration is tightly coupled to agent construction. |
| `core/health.py` → `core/health.py` (stays) | `core/` | Health check probes (DB ping, cache status) are infrastructure-adjacent shared internals. Acceptable in `core/`. |
| `infrastructure/database.py` → `db/connection.py` + `db/tenant_db.py` | `db/` | Split by concern: shared DB init/pooling goes in `connection.py`, tenant DB CRUD goes in `tenant_db.py`. |
| `infrastructure/middleware.py` → `middleware/user.py` | `middleware/` | Custom middleware is middleware. Naming it `user.py` clarifies what it extracts/validates. |
| `schemas/` → `models/` | `models/` | FastAPI convention prefers `models/` for Pydantic models. Both names acceptable — `models/` is the official template name. |
| `services/chat_service.py` | `services/` (stays) | Business logic in `services/` is correct FastAPI convention. |
| `routers/chat.py` | `routers/` (stays) | API layer in `routers/` is correct FastAPI convention. |
| **Add `core/exceptions.py`** | NEW | Centralize custom exceptions, exception handlers, and error middleware — FastAPI convention. |
| **Add `core/dependencies.py`** | NEW | Centralize `Depends()`-based functions (tenant manager getter, validated user dependency) — FastAPI convention. |
| **Add `utils/streaming.py`** | NEW | Extract the SSE generator and `_extract_reply` helper so `chat_service.py` stays focused on orchestration. |

## Current Issues

1. **`app/database.py` is a dead duplicate** — contains the same `init_db_tables()` / `get_checkpointer()` / `get_store()` logic as `app/infrastructure/database.py`. Never imported. Should be removed.
2. **`core/` is an anti-pattern** — contains agent construction, tenant management, backend helpers — all domain logic, not FastAPI internals.
3. **`infrastructure/` is too small to be its own layer** — holds only `database.py` and `middleware.py`. Should be split and renamed.
4. **No `dependencies.py`** — missing centralized `Depends()`-based dependency functions.
5. **No `exceptions.py` in core** — custom exceptions scattered as bare `HTTPException`.
6. **`schemas/` naming** — FastAPI convention prefers `models/`.

## Per-File Descriptions (Target)

```
app/main.py
  → FastAPI app factory. Contains lifespan, middleware wiring, router inclusion.
  → Should import from core/config, core/dependencies (health), routers/, database.py (db init).
  → All router-level prefix/tags/depends should be on the APIRouter, not include_router().

app/core/config.py
  → Settings class (Pydantic BaseSettings). Already follows FastAPI convention.
  → Move to app/__init__? No, leave in core/.

app/core/exceptions.py  (NEW)
  → Custom exception classes (e.g. TenantNotFoundError, AgentUnavailableError).
  → Exception handler registrations @app.exception_handler().

app/core/dependencies.py  (NEW)
  → get_tenant_manager: Depends() callable that returns the TenantManager singleton.
  → get_validated_user_id: FastAPI dependency that extracts and validates user_id from request.
  → Any shared Depends() functions that multiple routers need.

app/core/health.py
  → check_health(), check_postgres(), check_tenants().
  → Lightweight probes. Fine in core/ as cross-cutting concern.

app/models/request.py
  → ChatRequest (Pydantic model). Minimal change needed:
    - Replace bare `str` field defaults with Field() annotations.
    - Consider replacing `body: ChatRequest` in route with Annotated types.

app/models/response.py
  → ChatResponse, ChatHistoryResponse, MessageItem. No structural issues.

app/db/connection.py  (NEW — from infrastructure/database.py)
  → get_db_uri(), init_db_tables(), get_checkpointer(), get_store().
  → Shared checkpointer/store initialization for non-tenant deployments.

app/db/tenant_db.py  (NEW — from infrastructure/database.py)
  → create_tenant_database(), drop_tenant_database(), ensure_tenant_schema().
  → Tenant database CRUD operations.

app/db/pool.py  (NEW)
  → Connection pool management for checkpointer and store.

app/agents/factory.py  (NEW — from core/agent.py)
  → get_agent(), build_model(), create_deep_agent().
  → Model provider priority logic, singleton agent pattern.

app/agents/tenant.py  (NEW — from core/agent_manager.py)
  → TenantStore (dataclass), TenantManager class, get_tenant_manager(), reset_tenant_manager().
  → Per-user isolation, LRU eviction, TTL-based cleanup.

app/agents/backends.py  (NEW — from core/backends.py)
  → build_tenant_backend_config(), build_backend_for_tenant().

app/middleware/user.py  (NEW — from infrastructure/middleware.py)
  → validate_user_id(), _extract_user_id_from_request(), UserMiddleware.

app/services/chat_service.py
  → send_message(), get_history(), get_agent_for_user().
  → Should use dependencies.py for TenantManager access.

app/routers/chat.py
  → APIRouter(prefix="/chat", tags=["chat"]).
  → post_chat(), get_chat_history().
  → Use Annotated types for all path/query parameters.
  → response_model on route decorator.
  → Router-level depends=[Depends(get_validated_user_id)].
```
