"""Sign-in and roles: Entra ID OIDC (``AUTH_MODE=oidc``) or the dev personas (``AUTH_MODE=dev``).

:mod:`.roles` (the principal and its roles), :mod:`.jwt` (token validation), :mod:`.oidc` (the
tenant's endpoints), :mod:`.session` (the signed cookies), :mod:`.router` (``/auth/*``); the
FastAPI dependencies that use them are in :mod:`api.app.deps`.
"""
from api.app.auth.roles import GATE_OWNERS, Principal

__all__ = ["GATE_OWNERS", "Principal"]
