"""Python client — cross-language fixture for Phase 5.3."""

import subprocess

import requests


def fetch_users() -> list:
    return requests.get("/api/users").json()


def build() -> None:
    subprocess.run(["./scripts/build.ts"])
