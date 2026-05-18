"""Tiny Python side of the polyglot fixture."""


class Config:
    def __init__(self, debug: bool = False) -> None:
        self.debug = debug

    def is_debug(self) -> bool:
        return self.debug


def load_config() -> Config:
    return Config()
