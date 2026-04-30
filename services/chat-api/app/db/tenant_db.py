"""Tenant database CRUD operations.

Provides functions to create, drop, and initialise per-tenant PostgreSQL
databases for the multi-tenant chat application.
"""

from __future__ import annotations

import logging

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

logger = logging.getLogger(__name__)


async def create_tenant_database(tenant_db_name: str, superuser_uri: str) -> bool:
    """Create a tenant database if it does not already exist.

    Parameters
    ----------
    tenant_db_name:
        The name of the database to create (e.g. ``deepagent_user-abc``).
    superuser_uri:
        A connection URI to the superuser database (e.g. ``postgres``).

    Returns
    -------
    ``True`` if the database was created, ``False`` if it already existed.
    """
    conn = await psycopg.AsyncConnection.connect(superuser_uri, autocommit=True)
    async with conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (tenant_db_name,)
        )
        if not await cur.fetchone():
            await cur.execute(f'CREATE DATABASE "{tenant_db_name}"')
            logger.info("Created database %s", tenant_db_name)
            return True
    return False


async def drop_tenant_database(tenant_db_name: str, superuser_uri: str) -> bool:
    """Drop a tenant database.

    Parameters
    ----------
    tenant_db_name:
        The name of the database to drop.
    superuser_uri:
        A connection URI to the superuser database (e.g. ``postgres``).

    Returns
    -------
    ``True`` if the database was dropped, ``False`` if it did not exist.
    """
    conn = await psycopg.AsyncConnection.connect(superuser_uri, autocommit=True)
    async with conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (tenant_db_name,)
        )
        if await cur.fetchone():
            await cur.execute(f'DROP DATABASE IF EXISTS "{tenant_db_name}"')
            logger.info("Dropped database %s", tenant_db_name)
            return True
    return False


async def ensure_tenant_schema(db_uri: str) -> None:
    """Ensure checkpointer and store tables exist in *db_uri*.

    Parameters
    ----------
    db_uri:
        A connection URI for the target tenant database.
    """
    async with AsyncPostgresSaver.from_conn_string(db_uri) as saver:
        await saver.setup()
    async with AsyncPostgresStore.from_conn_string(db_uri) as store:
        await store.setup()
