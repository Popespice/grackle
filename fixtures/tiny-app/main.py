"""Entry point — wires models, services, and utils together."""

from __future__ import annotations

from models import Admin, User
from services import AuthService
from utils import hash_password

_auth = AuthService()


def run(name: str, email: str, password: str) -> None:
    user = User(name=name, email=email)
    print(user.display())
    pw_hash = hash_password(password)
    if _auth.login(email, password):
        token = AuthService.create_token(user.email)
        print(f"token={token} hash={pw_hash}")


def make_admin(name: str, email: str, level: int) -> Admin:
    return Admin(name=name, email=email, level=level)


if __name__ == "__main__":
    run("Alice", "alice@example.com", "secret")
