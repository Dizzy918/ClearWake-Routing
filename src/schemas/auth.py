from typing import Optional

from pydantic import BaseModel, Field, field_validator

from src.models.user import ROLES


class RegisterUserSchema(BaseModel):
    company_id: str
    email: str
    password: str = Field(min_length=8)
    full_name: Optional[str] = None
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def _check_role(cls, role: str) -> str:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return role


class LoginSchema(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    id: str
    company_id: str
    email: str
    full_name: Optional[str] = None
    role: str
    is_active: bool


class RoleChangeSchema(BaseModel):
    """Change a sub-account's role."""

    role: str

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str) -> str:
        from src.models.user import ROLES

        if value not in ROLES:
            raise ValueError(f"role must be one of: {', '.join(ROLES)}")
        return value
