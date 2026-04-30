"""Database package for multi-tenant PostgreSQL management.

Provides database connection initialization, tenant database CRUD operations,
and connection pooling for the chat API service.
"""

# Submodules (imported lazily once the modules are implemented):
# - connection: DB initialization, connection URIs, checkpointer, store
# - tenant_db:  Tenant database CRUD operations
# - pool:       Connection pool management

# __all__ lists the submodules exported by this package.
# The actual symbols will be re-exported once the submodules are implemented.
__all__ = [
    "connection",
    "tenant_db",
    "pool",
]
