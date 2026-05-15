"""Shared utility functions used across the tiny-app."""

import hashlib


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def send_welcome(email: str) -> None:
    normalized = normalize_email(email)
    print(f"Welcome email → {normalized}")
