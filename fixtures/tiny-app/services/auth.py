"""Authentication service — calls into utils; TYPE_CHECKING import of User."""

from __future__ import annotations

from typing import TYPE_CHECKING

from utils import hash_password, normalize_email

if TYPE_CHECKING:
    from models import User


class AuthService:
    def login(self, email: str, password: str) -> bool:
        hashed = hash_password(password)
        norm = normalize_email(email)
        return self._verify(norm, hashed)

    def _verify(self, email: str, hashed: str) -> bool:
        return bool(email) and bool(hashed)

    @staticmethod
    def create_token(user_id: str) -> str:
        return f"tok_{user_id}"
