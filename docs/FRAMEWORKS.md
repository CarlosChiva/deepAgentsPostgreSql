# FRAMEWORKS.md

## Technologies Catalog

### Runtime & Language

| Technology | Version | Role |
|------|---------|------|
| **Python** | 3.12 | Language runtime |
| **uv** | latest | Package manager & virtual environment tool (replaces pip + poetry + pipenv) |
| **FastAPI** | latest | Web framework (ASGI). Used via `fastapi dev` (dev) and `fastapi run` (prod) |
| **Uvicorn** | latest | ASGI server (dependency of FastAPI) |
| **PostgreSQL** | 16-alpine | Database for checkpointer persistence + potential future vector storage |

### AI / Agent Frameworks

| Technology | Version | Role |
|------|---------|------|
| **DeepAgents** | latest | Core agent framework. `create_deep_agent()` instantiates the agent with built-in TodoListMiddleware, FilesystemMiddleware, SubAgentMiddleware. Provides memory, skills, and planning out of the box. |
| **LangChain** | >=1.0,<2.0 | Base framework. LangChain 1.0 LTS provides models, tools, chains. DeepAgents depends on this. |
| **langchain-core** | >=1.0,<2.0 | Shared base types & interfaces. Peer dependency always installed with langchain. |
| **LangGraph** | >=1.0,<2.0 | Orchestration layer (transitive dep of deepagents). Provides StateGraph, checkpointer system, state management. |
| **LangSmith** | >=0.3.0 | Tracing and observability for LLM calls. |

### Database & Persistence

| Technology | Version | Role |
|------|---------|------|
| **langgraph-checkpoint-postgres** | compatible with langgraph 1.0 | **PostgresSaver** checkpointer. Persists thread states (conversation history + tool execution state) to PostgreSQL. Called on startup with `.setup()` to create checkpoint tables. |
| **psycopg** | >=3.0 | PostgreSQL async connection library (used by langgraph-checkpoint-postgres internally) |
| **pgvector** | latest | PostgreSQL extension for vector similarity search (installed via custom Dockerfile). Future-proof for RAG embeddings. |

### Security & Validation

| Technology | Version | Role |
|------|---------|------|
| **Pydantic** | bundled with fastapi | Data validation for request/response models |
| **pydantic-settings** | bundled with fastapi | Environment variable management for config |

### HTTP & Streaming

| Technology | Version | Role |
|------|---------|------|
| **sse-starlette** | latest | SSE (Server-Sent Events) support for FastAPI streaming responses |

### Tooling & Quality

| Technology | Version | Role |
|------|---------|------|
| **ruff** | latest | Linting and formatting (uvx ruff) |
| **ty** | latest | Type checking |
| **pytest** | latest | Testing framework |
| **httpx** | latest | HTTP client test framework (FastAPI's TestClient uses it) |
| **asyncer** | latest | Concurrency management (mixing sync/async) |

### Docker

| Technology | Version | Role |
|------|---------|------|
| **Docker** | latest | Container runtime |
| **Docker Compose** | >=3.8 | Multi-container orchestration |

## Dependency Graph (pyproject.toml dependencies)

```toml
[project]
name = "chat-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    # --- DeepAgents Core (bundles langgraph internally) ---
    "deepagents",
    "langchain>=1.0,<2.0",
    "langchain-core>=1.0,<2.0",
    "langsmith>=0.3.0",

    # --- Persistence ---
    "langgraph-checkpoint-postgres",

    # --- Web ---
    "fastapi[standard]>=0.115.0",       # includes uvicorn, pydantic, pydantic-settings
    "sse-starlette",                     # SSE streaming for chat responses

    # --- Model Provider (pick one at minimum) ---
    # "langchain-openai",               # Uncomment for OpenAI models
    # "langchain-anthropic",            # Uncomment for Anthropic Claude
    # "langchain-google-genai",         # Uncomment for Google Gemini

    # --- Tooling / Dev ---
    "ruff",
    "pytest",
    "httpx",
    "asyncer",
]
```

## Architecture Overview (Data Flow)

```
                    ┌─────────────────┐
                    │     Client      │
                    │  (Browser/App)  │
                    └────────┬────────┘
                             │
              HTTP POST /chat   GET /chat/{thread_id}
              SSE streaming    │
                    ┌────────▼────────┐
                    │  FastAPI / Docker │   (services/chat-api/)
                    │  chat-api service │
                    └────────┬────────┘
                             │
              ┌──────────────┼───────────────┐
              │              │              │
              ▼              ▼              ▼
    ┌───────────────┐  ┌───────────┐  ┌───────────────┐
    │ DeepAgent     │  │ Pydantic  │  │ SSE Response  │
    │ (create_      │  │ Models    │  │ (SSE/JSON)    │
    │  deep_agent)  │  └───────────┘  └───────────────┘
    └───────┬───────┘
            │
            │ thread_id (configurable)
            ▼
    ┌───────────────┐     persists states     ┌───────────────┐
    │ PostgresSaver  │ ───────────────►  │  PostgreSQL     │
    │ (checkpointer) │                   │  (docker-compose│
    └───────────────┘                     │   postgres svc) │
                                          └───────────────┘
```

1. **Client → POST /chat**: Sends `{thread_id, message}` → FastAPI receives → validates with Pydantic model
2. **FastAPI → DeepAgent**: Calls `agent.invoke(messages, config={"configurable": {"thread_id": thread_id}})` with the LLM model
3. **DeepAgent → Response**: Agent processes with tools, planning, memory → returns result → streamed back via SSE
4. **DeepAgent ↔ Persistent State**: `PostgresSaver` automatically persists conversation state to PostgreSQL after each super-step
5. **Client → GET /chat/{thread_id}**: Retrieves checkpoint history from PostgreSQL → reconstructs message sequence → returns as JSON

## Environment Variables Required (`.env.example`)

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

# LangSmith (optional but recommended)
LANGSMITH_API_KEY=ls-proj-xxx
LANGSMITH_PROJECT=deepagent-postgres

# Chat Service
CHAT_SERVICE_PORT=8000
MAX_MESSAGES_PER_THREAD=100
```

## Known Risks & Considerations

| Risk | Details | Mitigation |
|------|---------|------------|
| **DeepAgents version instability** | DeepAgents is a newer framework; APIs may shift | Pin to a tested version in production `DEEPAGENTS>=0.x,<1.0` |
| **langchain-community not semver** | The community package does not follow semantic versioning | Avoid; use dedicated integrations (e.g., model-specific packages) instead |
| **PostgresSaver `.setup()` must be called once** | Calling `.setup()` on an already-initialized DB causes errors | Wrap in try/except catching table-already-exists errors, or use a flag in config |
| **Memory exhaustion with long conversations** | PostgresSaver stores full message history per thread | Add a max messages cap in config; implement message truncation in the service |
| **No authentication on endpoints** | All endpoints are currently open | Add an optional API key middleware in a future iteration |
| **FilesystemBackend not for production** | DeepAgents' FilesystemBackend allows arbitrary file access | Use StateBackend in the web server context; never mount FilesystemBackend |
| **PostgreSQL port 5432 exposed** | Database port accessible from host in dev mode | Remove `ports: 5432:5432` in production compose; keep only internal network |
