"""Output model: what templates may know about the signed-in user (CLAUDE.md code rules)."""

from pydantic import BaseModel

from app.models.user import User


class AccountView(BaseModel):
    """No password hash and no provider IDs: only what a page shows."""

    name: str | None
    email: str | None
    email_confirmed: bool
    has_password: bool
    google_linked: bool

    @classmethod
    def from_user(cls, user: User) -> "AccountView":
        return cls(
            name=user.name,
            email=user.email,
            email_confirmed=user.email_verified_at is not None,
            has_password=user.password_hash is not None,
            google_linked=user.google_sub is not None,
        )
