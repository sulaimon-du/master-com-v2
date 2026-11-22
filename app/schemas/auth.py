"""Input models for the auth forms. Validation happens here, before any service runs."""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN = 10
PASSWORD_MAX = 128

Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class _Form(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EmailForm(_Form):
    email: Email

    @field_validator("email")
    @classmethod
    def _shape(cls, value: str) -> str:
        value = value.lower()
        if not EMAIL_RE.match(value):
            raise ValueError("email")
        return value


class SignInForm(EmailForm):
    # Length is not checked here: any submitted value is compared, so old passwords still work.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class RegisterForm(EmailForm):
    name: Name
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class NewPasswordForm(_Form):
    token: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
