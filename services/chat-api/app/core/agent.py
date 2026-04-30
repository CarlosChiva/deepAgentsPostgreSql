"""Compatibility layer -- re-exports from ``app.agents.*`` modules.

This module exists solely to provide backward compatibility for code that
imports from ``app.core.agent``.  All implementation logic has been moved
into the dedicated submodules:

- :mod:`app.agents.factory` -- agent construction, model provider selection
- :mod:`app.agents.tenant` -- tenant management, per-user DB, caching
- :mod:`app.agents.backends` -- backend configuration helpers

New code should import directly from ``app.agents.*`` instead.

TODO: Update callers to use the canonical import paths and delete this module.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "app.core.agent is deprecated; import from app.agents.factory, "
    "app.agents.tenant, or app.agents.backends instead.",
    DeprecationWarning,
    stacklevel=2,
)

# -- Re-exports from app.agents.factory --

from app.agents.factory import (
    build_model,
    create_deep_agent,
    get_agent,
)

# -- Re-exports from app.agents.tenant --

from app.agents.tenant import (
    TenantManager,
    TenantStore,
    get_tenant_manager,
    reset_tenant_manager,
)

# -- Re-exports from app.agents.backends --

from app.agents.backends import (
    build_backend_for_tenant,
    build_tenant_backend_config,
)

# -- Public surface --

__all__ = [
    # From app.agents.factory
    "build_model",
    "create_deep_agent",
    "get_agent",
    # From app.agents.tenant
    "TenantManager",
    "TenantStore",
    "get_tenant_manager",
    "reset_tenant_manager",
    # From app.agents.backends
    "build_backend_for_tenant",
    "build_tenant_backend_config",
]
