"""Database service — async query with a closure, cached_property example."""

from __future__ import annotations

from functools import cached_property


class Database:
    def __init__(self, url: str) -> None:
        self._url = url

    @cached_property
    def connection(self) -> str:
        return f"conn:{self._url}"


async def query(sql: str) -> list[dict[str, object]]:
    def _parse_row(row: str) -> dict[str, object]:
        return {"row": row}

    return [_parse_row(r) for r in sql.split()]
