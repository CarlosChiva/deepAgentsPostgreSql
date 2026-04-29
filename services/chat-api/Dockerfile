# ---- Stage 1: builder ----
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock .

RUN uv sync --frozen --all-extras

# ---- Stage 2: runtime ----
FROM python:3.12-slim

ENV PATH="/app/.venv/bin:$PATH"

# python user already exists in python:3.12-slim image
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY app/ app/


EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
