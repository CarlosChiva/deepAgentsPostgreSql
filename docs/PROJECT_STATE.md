# PROJECT_STATE.md

## TASK LIST

## Task: T001 - PostgreSQL Infrastructure: init SQL, Dockerfile, env files
- **Status**: DONE
- **Description**: Create the PostgreSQL 16 Alpine base Docker image with pgvector extension and the initialization SQL script. Also create `.env.example` and `.env.production` with sensible defaults for database connection settings, API keys, and app configuration. The init SQL should create any necessary extension schemas for langgraph-checkpoint-postgres compatibility.
- **Frameworks**: Docker, PostgreSQL 16, pgvector
- **Skills**: docker-compose-orchestration
- **Files to create/modify**: 
  - `infrastructure/postgres/Dockerfile` (NEW)
  - `infrastructure/postgres/init/01-init.sql` (NEW)
  - `.env.example` (NEW)
  - `.env.production` (NEW)
- **Acceptance Criteria**: 
  - `infrastructure/postgres/Dockerfile` extends postgres:16-alpine with pgvector
  - `.env.example` contains all env vars with placeholder values
  - `.env.production` contains the same keys with example production values

## Task: T002 - docker-compose.yml: orchestrate PostgreSQL and FastAPI services
- **Status**: DONE
- **Description**: Create the root `docker-compose.yml` defining two services: `postgres` and `chat-api`. The postgres service uses custom Dockerfile, exposes port 5432, has pg_isready healthcheck. The chat-api service builds from `services/chat-api/`, exposes port 8000, depends on postgres being healthy, shares `app-network`. Define network and volumes at top level.
- **Frameworks**: Docker Compose
- **Skills**: docker-compose-orchestration
- **Files to create/modify**: 
  - `docker-compose.yml` (NEW)
- **Acceptance Criteria**: 
  - Two services defined: `postgres` and `chat-api`
  - Both on `app-network`
  - Named volume and healthcheck configured

## Task: T003 - FastAPI project setup: pyproject.toml and dependency list
- **Status**: DONE ✅
- **Description**: Initialize the Python project inside `services/chat-api/` with a `pyproject.toml`. Set minimum Python 3.12. Add all required dependencies: `fastapi`, `uvicorn[standard]`, `langchain>=1.0,<2.0`, `langchain-core>=1.0,<2.0`, `langgraph>=1.0,<2.0`, `langsmith>=0.3.0`, `deepagents`, `psycopg[binary]>=3.0`, `langgraph-checkpoint-postgres`, `sse-starlette`, `pydantic>=2.0`, `pydantic-settings`. Add [tool.fastapi] entrypoint.
- **Frameworks**: uv, FastAPI, LangChain ecosystem
- **Skills**: uv, langchain-dependencies, fastapi
- **Files to create/modify**: 
  - `services/chat-api/pyproject.toml` (NEW)
- **Acceptance Criteria**: 
  - pyproject.toml has all required dependencies with proper version constraints
  - [tool.fastapi] entrypoint set to "app.main:app"
  - `uv sync` runs successfully

## Task: T004 - Application config and database connection layer
- **Status**: DONE ✅
- **Description**: Create configuration and database connection modules. `app/config.py`: Settings class using pydantic_settings reading from env vars (POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, OPENAI_API_KEY, ANTHROPIC_API_KEY, APP_HOST, APP_PORT). `app/database.py`: `get_checkpointer()` that lazily creates a PostgresSaver, `get_store()` for PostgresStore, `setup_checkpointer()` that calls `.setup()` on first run. Singleton pattern to avoid re-creating connections.
- **Frameworks**: FastAPI, Pydantic, psycopg, langgraph-checkpoint-postgres
- **Skills**: langchain-dependencies, langgraph-persistence
- **Files to create/modify**: 
  - `services/chat-api/app/__init__.py` (NEW)
  - `services/chat-api/app/config.py` (NEW)
  - `services/chat-api/app/database.py` (NEW)
- **Acceptance Criteria**: 
  - Settings class with all env vars and defaults
  - postgres_url property constructs correct connection string
  - Singleton checkpointer and store
  - setup_checkpointer handles table creation

## Task: T005 - DeepAgents agent factory
- **Status**: DONE ✅
- **Description**: Create core agent logic in `app/agent.py`. `get_agent()` function that builds DeepAgent using `create_deep_agent()` with: model provider (prioritize OpenAI if key set, fall back to Anthropic, error if neither), system prompt for chatbot, PostgresSaver checkpointer, PostgresStore. Lazy initialization with caching/singleton pattern.
- **Frameworks**: DeepAgents, LangGraph
- **Skills**: deep-agents-core, deep-agents-memory, langchain-fundamentals
- **Files to create/modify**: 
  - `services/chat-api/app/agent.py` (NEW)
- **Acceptance Criteria**: 
  - `get_agent()` returns configured DeepAgent with checkpointer and store
  - Model selection based on API keys
  - Singleton cached agent instance

## Task: T006 - Pydantic models for chat and history endpoints
- **Status**: DONE ✅
- **Description**: Create Pydantic models for API request/response validation. `app/models/chat.py`: ChatRequest (message, thread_id, stream), ChatResponse. `app/models/history.py`: MessageItem (role, content, timestamp), ChatHistoryResponse. Proper Field descriptions and examples for FastAPI docs.
- **Frameworks**: FastAPI, Pydantic
- **Skills**: fastapi
- **Files to create/modify**: 
  - `services/chat-api/app/models/__init__.py` (NEW)
  - `services/chat-api/app/models/chat.py` (NEW)
  - `services/chat-api/app/models/history.py` (NEW)
- **Acceptance Criteria**: 
  - All 4 models with proper types, fields, descriptions
  - All exported from models/__init__.py

## Task: T007 - Chat service layer
- **Status**: DONE ✅
- **Description**: Business logic in `app/services/chat_service.py`. `send_message()`: invokes DeepAgent via `agent.invoke()` with thread_id config, handles streaming, returns ChatResponse or SSE `StreamingResponse`. `get_history()`: retrieves conversation history from `PostgresSaver` checkpoint states, reconstructs `MessageItem` list. Proper error handling with generic HTTP detail messages.
- **Frameworks**: FastAPI, DeepAgents, LangGraph, sse-starlette
- **Skills**: deep-agents-core, langgraph-persistence
- **Files to create/modify**: 
  - `services/chat-api/app/services/__init__.py` (NEW)
  - `services/chat-api/app/services/chat_service.py` (NEW)
- **Acceptance Criteria**: 
  - send_message calls agent.invoke correctly
  - get_history retrieves and reconstructs messages
  - Error handling for invalid input, agent failures, missing threads

## Task: T008 - Router: POST /chat and GET /chat/{thread_id}
- **Status**: DONE ✅
- **Description**: FastAPI router in `app/routers/chat.py`. `POST /`: accepts ChatRequest, calls service, returns ChatResponse. Supports streaming via SSE when stream=True. `GET /{thread_id}`: accepts thread_id, returns ChatHistoryResponse. Both with OpenAPI docs, HTTPException handling.
- **Frameworks**: FastAPI, sse-starlette
- **Skills**: fastapi
- **Files to create/modify**: 
  - `services/chat-api/app/routers/__init__.py` (NEW)
  - `services/chat-api/app/routers/chat.py` (NEW)
- **Acceptance Criteria**: 
  - POST /chat returns ChatResponse (streaming optional)
  - GET /chat/{thread_id} returns message history
  - Both endpoint documented with summary, description, response_model

## Task: T009 - Main FastAPI app: wire everything together
- **Status**: DONE ✅
- **Description**: Main app in `app/main.py`. FastAPI app with title/version. Startup event calls `setup_checkpointer()`. Includes chat router. `/health` endpoint checks DB connectivity. CORS middleware for dev. `/docs` auto-generated.
- **Frameworks**: FastAPI
- **Skills**: fastapi
- **Files to create/modify**: 
  - `services/chat-api/app/main.py` (NEW)
- **Acceptance Criteria**: 
  - FastAPI app with all routes wired
  - Startup setup_checkpointer runs
  - /health endpoint works
  - CORS configured

## Task: T010 - Dockerize the chat-api service
- **Status**: DONE ✅
- **Description**: Containerization assets. `Dockerfile`: multi-stage build (build stage with uv sync, run stage with .venv). `.dockerignore`: excludes .git, __pycache__, .env, etc.
- **Frameworks**: Docker, uv
- **Skills**: docker-compose-orchestration
- **Files to create/modify**: 
  - `services/chat-api/Dockerfile` (NEW)
  - `services/chat-api/.dockerignore` (NEW)
- **Acceptance Criteria**: 
  - Multi-stage Dockerfile with layer caching
  - CMD uses uvicorn app.main:app
  - .dockerignore excludes dev artifacts

## Task: T011 - Health check and graceful shutdown
- **Status**: DONE ✅
- **Description**: Enhanced `/health` endpoint with PostgreSQL, checkpointer, and agent health checks. Added `/ready` endpoint with proper HTTP 503 during startup/shutdown. Added graceful shutdown via `lifespan` handler that closes both the checkpointer and AsyncPostgresStore connection pools without rejecting active in-flight requests. Added request logging middleware with timing. Added `/startup_failed` flag to block readiness when initialization fails.
- **Frameworks**: FastAPI, LangGraph
- **Skills**: fastapi, langgraph-persistence, deep-agents-core
- **Files to create/modify**: 
  - `services/chat-api/app/health.py` (NEW) — health check probes
  - `services/chat-api/app/main.py` (modify) — lifespan, endpoints, middleware
  - `services/chat-api/app/database.py` (modify) — close_store(), shutdown flags
- **Acceptance Criteria**: 
  - `/health` checks postgres and checkpointer → ✅ Pass
  - `/ready` endpoint returns proper status → ✅ Pass (HTTP 503 during startup)
  - Graceful shutdown closes connections → ✅ Pass (checkpointer + store pools closed)

## Task: T012 - Unit test configuration and fixtures (conftest.py)
- **Status**: DONE ✅
- **Description**: Test infrastructure. `conftest.py`: PostgresTestSaver or mock checkpointer, async test client with httpx, fixtures for client, checkpointer, store. Add dev deps to pyproject.toml: pytest, pytest-asyncio, httpx, httpx-sse.
- **Frameworks**: pytest, httpx, pytest-asyncio
- **Skills**: fastapi, langgraph-persistence
- **Files to create/modify**: 
  - `services/chat-api/tests/__init__.py` (NEW)
  - `services/chat-api/tests/conftest.py` (NEW)
  - `services/chat-api/tests/test_chat.py` (modify - add dev deps if missing)
  - `services/chat-api/tests/test_agent.py` (modify - add dev deps if missing)
- **Acceptance Criteria**: 
  - All fixtures created
  - pytest-asyncio configured
  - Dev deps added to pyproject.toml

## Task: T013 - Chat endpoint tests
- **Status**: DONE
- **Description**: Tests for API endpoints in `tests/test_chat.py`: test_chat_post_returns_response, test_chat_post_with_custom_thread_id, test_chat_history_returns_messages, test_chat_history_empty_for_new_thread, test_chat_stream_returns_sse_events, test_invalid_request_body.
- **Frameworks**: pytest, httpx
- **Skills**: fastapi
- **Files to create/modify**: 
  - `services/chat-api/tests/test_chat.py` (NEW)
- **Acceptance Criteria**: 
  - All endpoint tests defined with proper assertions
  - Tests use fixtures from conftest.py
  - HTTP status codes verified

## Task: T014 - Agent and service layer tests
- **Status**: DONE ✅
- **Description**: Tests in `tests/test_agent.py`: test_get_agent_returns_instance, test_agent_uses_correct_checkpointer, test_send_message_calls_agent_invoke, test_get_history_retrieves_messages, test_invalid_input_raises_value_error, test_chat_response_structure.
- **Frameworks**: pytest, unittest.mock
- **Skills**: fastapi, deep-agents-core, langchain-fundamentals
- **Files to create/modify**: 
  - `services/chat-api/tests/test_agent.py` (NEW)
- **Acceptance Criteria**: 
  - 6 tests with proper assertions
  - Uses mock agent and test_checkpointer
  - Error handling verified

## Task: T015 - Final documentation and README
- **Status**: DONE ✅
- **Description**: Project README.md: project description, prerequisites, quick start, API curl examples, project structure, env vars table, health endpoints, troubleshooting, development instructions, Docker commands.
- **Skills**: None specific
- **Files to create/modify**: 
  - `README.md` (NEW)
- **Acceptance Criteria**: 
  - Complete README with all sections ✅
  - Quick start is copy-paste runnable ✅
  - API examples for both endpoints ✅
