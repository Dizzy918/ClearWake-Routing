"""
JWT-based auth dependencies (issue #89).

* ``get_current_user`` — verifies the bearer token, loads the User
  document, and raises 401 on any failure.
* ``require_role`` — factory returning a dependency that 403s if the
  user's role isn't in the allowed set.
"""

from __future__ import annotations

from typing import Callable

import jwt
from fastapi import Depends, Header, HTTPException

from src.core.security import decode_access_token
from src.infrastructure.repositories.user_repository import UserRepository
from src.models.user import User


_user_repo = UserRepository()


async def get_current_user(authorization: str = Header(default="")) -> User:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")

    user = _user_repo.get_by_id(user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


def require_role(*allowed: str) -> Callable:
    """Return a dependency that authorises only the given roles."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{user.role}' is not allowed for this action",
            )
        return user

    return _check


def require_company_access(user: User, company_id: object) -> None:
    """403 when *company_id* names a different tenant than the caller's.

    ``None`` passes — endpoints treat a missing company filter as
    "the caller's own company".
    """
    if company_id is not None and str(company_id) != str(user.company_id):
        raise HTTPException(
            status_code=403,
            detail="Cannot access another company's data",
        )
