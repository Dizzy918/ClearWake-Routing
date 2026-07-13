from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException

from src.api.auth_dependencies import get_current_user, require_role
from src.core.config import settings
from src.core.security import hash_password, issue_access_token, verify_password
from src.infrastructure.repositories.user_repository import UserRepository
from src.models.user import User
from src.schemas.auth import LoginSchema, RegisterUserSchema, TokenResponse, UserOut
from src.core.time_utils import utc_now

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
repo = UserRepository()


def _to_user_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id),
        company_id=str(user.company_id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
    )


@router.post("/register", response_model=UserOut)
def register_user(
    payload: RegisterUserSchema,
    actor: User = Depends(require_role("admin")),
):
    """
    Create a new user. Only admins of the same company can register users.
    """
    if not ObjectId.is_valid(payload.company_id):
        raise HTTPException(status_code=400, detail="Invalid company_id")
    if str(actor.company_id) != payload.company_id:
        raise HTTPException(
            status_code=403, detail="Admins can only register users in their own company"
        )
    if repo.get_by_email(payload.email):
        raise HTTPException(status_code=409, detail="Email already registered")

    user = repo.create({
        "company_id": ObjectId(payload.company_id),
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "full_name": payload.full_name,
        "role": payload.role,
    })
    return _to_user_out(user)


@router.post("/bootstrap-admin", response_model=UserOut)
def bootstrap_admin(payload: RegisterUserSchema):
    """
    Create the first admin user for a company.

    Allowed only when the company has no users yet — protects against
    accidental admin creation in production.
    """
    if not ObjectId.is_valid(payload.company_id):
        raise HTTPException(status_code=400, detail="Invalid company_id")
    if repo.list_for_company(payload.company_id):
        raise HTTPException(
            status_code=409,
            detail="Company already has users; use /register with an admin token",
        )
    user = repo.create({
        "company_id": ObjectId(payload.company_id),
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "full_name": payload.full_name,
        "role": "admin",
    })
    return _to_user_out(user)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginSchema):
    user = repo.get_by_email(payload.email)
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user.last_login_at = utc_now()
    user.save()

    token = issue_access_token(
        user_id=str(user.id),
        company_id=str(user.company_id),
        role=user.role,
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.JWT_EXPIRES_MINUTES * 60,
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _to_user_out(user)
