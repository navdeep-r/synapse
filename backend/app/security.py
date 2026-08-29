"""Authentication hook and the exposure warning (R6).

The console sends no credentials, so the API is unauthenticated by default. That
is fine bound to loopback and dangerous anywhere else, which is what the startup
banner exists to say out loud.

``require_auth`` is the legacy shared-secret guard. New code should use
``require_principal`` from ``kg_acl.principal``, which resolves every request
to a ``Principal`` with identity, roles, and clearance.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.config import Settings

logger = logging.getLogger("synapse.security")

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def warn_if_exposed(settings: Settings) -> None:
    """Log a loud banner when the API is reachable off-host without a token."""
    if settings.host in LOOPBACK_HOSTS:
        return
    if settings.auth_token:
        logger.info("Bound to %s with SYNAPSE_AUTH_TOKEN set; requests must be authenticated.", settings.host)
        return

    banner = "!" * 78
    logger.warning(
        "\n%s\n"
        "  SYNAPSE IS LISTENING ON %s WITH NO AUTHENTICATION.\n"
        "  Every endpoint is publicly readable and writable, including:\n"
        "    - POST /api/v1/query/simulate  (the steward-only agent query panel)\n"
        "    - POST /api/v1/snapshot/generate  (produces an exportable data dump)\n"
        "    - POST /api/v1/uploads  (accepts arbitrary documents)\n"
        "  If you are tunnelling this for a demo (ngrok, Cloudflare Tunnel, port\n"
        "  forwarding), set SYNAPSE_AUTH_TOKEN first.\n"
        "%s",
        settings.host,
        banner,
        banner,
    )


async def require_auth(request: Request) -> None:
    """Legacy single-token auth. Deprecated — use ``require_principal``."""
    settings: Settings = request.app.state.synapse.settings
    if not settings.auth_token:
        return

    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if token != settings.auth_token:
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


# Re-export the new auth dependency for convenience.
from kg_acl.principal import require_principal  # noqa: E402, F401

__all__ = ["require_auth", "require_principal", "warn_if_exposed"]

