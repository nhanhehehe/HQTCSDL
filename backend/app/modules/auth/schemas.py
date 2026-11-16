from typing import Annotated

from pydantic import StringConstraints

from app.core.schemas import WebModel


class LoginRequest(WebModel):
    username: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class Admin(WebModel):
    id: str
    username: str


class SessionResponse(WebModel):
    admin: Admin
    expires_at: str
    csrf_token: str
