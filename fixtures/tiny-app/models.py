"""Domain models: User and Admin with inheritance, decorators, TYPE_CHECKING."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.auth import AuthService


@dataclass
class User:
    name: str
    email: str

    def display(self) -> str:
        return f"{self.name} <{self.email}>"


class Admin(User):
    """Admin user with an elevated privilege level."""

    def __init__(self, name: str, email: str, level: int) -> None:
        super().__init__(name, email)
        self.level = level

    @property
    def is_superadmin(self) -> bool:
        return self.level >= 10
