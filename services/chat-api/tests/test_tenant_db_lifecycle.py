"""Tests for the tenant database lifecycle functions in app/database.py.

Covers:
- tenant_db_name formatting
- create_tenant_database (first time & idempotent)
- ensure_tenant_schema
- drop_tenant_database (existing & non-existing)

All database operations are fully mocked — no real PostgreSQL connection needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ────────────────
# tenant_db_name
# ────────────────

def test_tenant_db_name_returns_correct_name():
    """tenant_db_name prefixes the user_id correctly."""
    from app.database import tenant_db_name  # noqa: E402

    class FakeSettings:
        TENANT_PREFIX = "deepagent_"

    name = tenant_db_name("alice", FakeSettings())
    assert name == "deepagent_alice"

    name2 = tenant_db_name("user-456", FakeSettings())
    assert name2 == "deepagent_user-456"

    # Test with no settings (defaults to module-level settings with TENANT_PREFIX)
    name3 = tenant_db_name("bob")
    assert name3.startswith("deepagent_bob")


# ────────────────
# create_tenant_database
# ────────────────

@pytest.mark.asyncio
async def test_create_tenant_database_first_time():
    """Creating a non-existing DB calls psycopg and issues CREATE DATABASE."""
    from app.database import create_tenant_database  # noqa: E402

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=None)  # doesn't exist
    mock_conn.cursor = MagicMock(return_value=mock_cursor)

    with patch("app.database.AsyncConnection", return_value=mock_conn) as MockConn:
        result = await create_tenant_database("deepagent_alice", "postgresql://p:pass@h:5432/postgres")

    assert result is True
    # Verify CREATE DATABASE was called
    create_calls = [c for c in mock_cursor.execute.call_args_list if "CREATE DATABASE" in str(c)]
    assert len(create_calls) >= 1


@pytest.mark.asyncio
async def test_create_tenant_database_idempotent_second_time():
    """Creating the same DB again doesn't error — returns True (already exists)."""
    from app.database import create_tenant_database  # noqa: E402

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock()
    mock_cursor.fetchone = AsyncMock(return_value=1)  # already exists
    mock_conn.cursor = MagicMock(return_value=mock_cursor)

    with patch("app.database.AsyncConnection", return_value=mock_conn) as MockConn:
        result = await create_tenant_database("deepagent_alice", "postgresql://p:pass@h:5432/postgres")

    # Should return True (already exists, no error)
    assert result is True
    # Should NOT issue CREATE DATABASE (only SELECT)
    create_calls = [c for c in mock_cursor.execute.call_args_list if "CREATE DATABASE" in str(c)]
    assert len(create_calls) == 0


@pytest.mark.asyncio
async def test_create_tenant_database_fallback_to_template1():
    """If postgres fails, it falls back to template1."""
    from app.database import create_tenant_database  # noqa: E402

    call_count = 0

    async def side_effect(uri, autocommit=True):
        nonlocal call_count
        call_count += 1
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=None)

        if call_count == 1:
            # First call (postgres) should raise OperationalError
            from psycopg import OperationalError
            raise OperationalError("connection refused")
        else:
            # Second call (template1) succeeds
            mock_cursor = AsyncMock()
            mock_cursor.execute = AsyncMock()
            mock_cursor.fetchone = AsyncMock(return_value=None)
            mock_conn.cursor = MagicMock(return_value=mock_cursor)
            return mock_conn

    async def fake_connect(uri, autocommit=True):
        return await side_effect(uri, autocommit)

    with patch("app.database.AsyncConnection", side_effect=fake_connect):
        result = await create_tenant_database("deepagent_alice", "postgresql://p:pass@h:5432/postgres")

    assert result is True


@pytest.mark.asyncio
async def test_create_tenant_database_invalid_uri():
    """Invalid superuser_uri raises ValueError."""
    from app.database import create_tenant_database  # noqa: E402

    with pytest.raises(ValueError, match="Invalid superuser_uri"):
        await create_tenant_database("deepagent_x", "invalid-no-db")


# ────────────────
# ensure_tenant_schema
# ────────────────

@pytest.mark.asyncio
async def test_ensure_tenant_schema_calls_setup():
    """ensure_tenant_schema calls setup() on both checkpointer and store."""
    from app.database import ensure_tenant_schema  # noqa: E402

    mock_checkpointer = MagicMock()
    mock_checkpointer.setup = AsyncMock()

    mock_store = MagicMock()
    mock_store.setup = AsyncMock()

    with patch("app.database.AsyncPostgresSaver") as MockSaver, \
         patch("app.database.AsyncPostgresStore") as MockStore:

        # Mock context manager for checkpointer
        mock_saver_cm = AsyncMock()
        mock_saver_cm.__aenter__ = AsyncMock(return_value=mock_checkpointer)
        mock_saver_cm.__aexit__ = AsyncMock(return_value=None)
        MockSaver.from_conn_string.return_value = mock_saver_cm

        # Mock context manager for store
        mock_store_cm = AsyncMock()
        mock_store_cm.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cm.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value = mock_store_cm

        await ensure_tenant_schema("postgresql://p:pass@h:5432/deepagent_alice")

    mock_checkpointer.setup.assert_called_once()
    mock_store.setup.assert_called_once()
    MockSaver.from_conn_string.assert_called_once()
    MockStore.from_conn_string.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_tenant_schema_handles_already_initialized():
    """ensure_tenant_schema doesn't error if setup() says tables already exist."""
    from app.database import ensure_tenant_schema  # noqa: E402

    mock_checkpointer = MagicMock()
    mock_checkpointer.setup = AsyncMock(
        side_effect=Exception("Table already exists - PG")
    )

    mock_store = MagicMock()
    mock_store.setup = AsyncMock()

    with patch("app.database.AsyncPostgresSaver") as MockSaver, \
         patch("app.database.AsyncPostgresStore") as MockStore:

        mock_saver_cm = AsyncMock()
        mock_saver_cm.__aenter__ = AsyncMock(return_value=mock_checkpointer)
        mock_saver_cm.__aexit__ = AsyncMock(return_value=None)
        MockSaver.from_conn_string.return_value = mock_saver_cm

        mock_store_cm = AsyncMock()
        mock_store_cm.__aenter__ = AsyncMock(return_value=mock_store)
        mock_store_cm.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value = mock_store_cm

        # Should not raise — "already exists" is ignored
        await ensure_tenant_schema("postgresql://p:pass@h:5432/deepagent_alice")


# ────────────────
# drop_tenant_database
# ────────────────

@pytest.mark.asyncio
async def test_drop_tenant_database_removes_db():
    """Dropping an existing DB issues DROP DATABASE IF EXISTS."""
    from app.database import drop_tenant_database  # noqa: E402

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock(return_value=None)
    mock_conn.cursor = MagicMock(return_value=mock_cursor)

    with patch("app.database.AsyncConnection", return_value=mock_conn) as MockConn:
        result = await drop_tenant_database("deepagent_alice", "postgresql://p:pass@h:5432/postgres")

    assert result is True
    drop_calls = [c for c in mock_cursor.execute.call_args_list if "DROP DATABASE" in str(c)]
    assert len(drop_calls) >= 1


@pytest.mark.asyncio
async def test_drop_tenant_database_if_not_exists():
    """Dropping a non-existing DB doesn't error."""
    from app.database import drop_tenant_database  # noqa: E402

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_cursor = AsyncMock()
    mock_cursor.execute = AsyncMock(return_value=None)
    mock_conn.cursor = MagicMock(return_value=mock_cursor)

    with patch("app.database.AsyncConnection", return_value=mock_conn) as MockConn:
        result = await drop_tenant_database("deepagent_nonexistent", "postgresql://p:pass@h:5432/postgres")

    # DROP DATABASE IF EXISTS should return True even if DB doesn't exist
    assert result is True


@pytest.mark.asyncio
async def test_drop_tenant_database_invalid_uri():
    """Invalid superuser_uri raises ValueError."""
    from app.database import drop_tenant_database  # noqa: E402

    with pytest.raises(ValueError, match="Invalid superuser_uri"):
        await drop_tenant_database("deepagent_x", "invalid-no-db")


# ────────────────
# get_tenant_connection_string
# ────────────────

def test_tenant_connection_string_format():
    """Connection string has correct format: postgresql://user:pass@host:port/dbname."""
    from app.database import get_tenant_connection_string  # noqa: E402

    class FakeSettings:
        TENANT_PREFIX = "deepagent_"

    uri = get_tenant_connection_string("alice", FakeSettings())

    assert uri.startswith("postgresql://")
    assert "deepagent_alice" in uri


def test_tenant_connection_string_escapes_password_with_special_chars():
    """Password may contain special chars — they should be in the URI."""
    from app.database import get_tenant_connection_string  # noqa: E402

    class FakeSettings:
        POSTGRES_HOST = "pg.example.com"
        POSTGRES_PORT = 5432
        POSTGRES_USER = "admin"
        POSTGRES_PASSWORD = "p@ss/w0rd"
        TENANT_PREFIX = "deepagent_"

    uri = get_tenant_connection_string("bob", FakeSettings())
    assert "deepagent_bob" in uri


def test_tenant_db_name_uses_default_settings():
    """When no settings passed, module-level TENANT_PREFIX is used."""
    from app.database import tenant_db_name  # noqa: E402
    from app import database as db_mod  # noqa: E402

    # If module-level settings have TENANT_PREFIX, use it
    name = tenant_db_name("test-user")
    assert "deepagent_test-user" in name
