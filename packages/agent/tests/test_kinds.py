from __future__ import annotations

import pytest

from grackle.kinds import (
    EdgeKind,
    KindRegistry,
    NodeKind,
    edge_kinds,
    node_kinds,
)

# ---------------------------------------------------------------------------
# NodeKind registry basics
# ---------------------------------------------------------------------------


def test_node_kind_register_and_lookup() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    k = NodeKind(name="widget", display_name="Widget", color="--color-accent", shape="dot")
    reg.register(k)
    assert reg.get("widget") == k


def test_edge_kind_register_and_lookup() -> None:
    reg: KindRegistry[EdgeKind] = KindRegistry()
    k = EdgeKind(name="uses", display_name="Uses", color="--color-accent", style="solid")
    reg.register(k)
    assert reg.get("uses") == k


def test_get_returns_none_for_unknown() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    assert reg.get("nonexistent") is None


def test_duplicate_registration_raises() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    k = NodeKind(name="x", display_name="X", color="--color-accent", shape="dot")
    reg.register(k)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(k)


def test_case_insensitive_lookup() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    k = NodeKind(
        name="File", display_name="File", color="--color-node-file", shape="rounded-square"
    )
    reg.register(k)
    assert reg.get("FILE") == k
    assert reg.get("file") == k
    assert reg.get("  File  ") == k


def test_case_insensitive_duplicate_detection() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    k1 = NodeKind(name="Thing", display_name="Thing", color="--color-accent", shape="dot")
    k2 = NodeKind(name="thing", display_name="Thing2", color="--color-accent", shape="dot")
    reg.register(k1)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(k2)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_empty_name_raises() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register(NodeKind(name="", display_name="?", color="--color-accent", shape="dot"))


def test_whitespace_only_name_raises() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register(NodeKind(name="   ", display_name="?", color="--color-accent", shape="dot"))


def test_control_char_in_name_raises() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    with pytest.raises(ValueError, match="control characters"):
        reg.register(
            NodeKind(name="bad\nname", display_name="?", color="--color-accent", shape="dot")
        )


def test_color_must_start_with_color_prefix() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    with pytest.raises(ValueError, match="--color-"):
        reg.register(NodeKind(name="x", display_name="X", color="red", shape="dot"))


def test_color_with_correct_prefix_accepted() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    k = NodeKind(name="x", display_name="X", color="--color-anything", shape="dot")
    reg.register(k)
    assert reg.get("x") == k


# ---------------------------------------------------------------------------
# known_names
# ---------------------------------------------------------------------------


def test_known_names_returns_sorted() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    for name, shape in [("zebra", "dot"), ("apple", "circle"), ("mango", "diamond")]:
        reg.register(
            NodeKind(name=name, display_name=name.title(), color="--color-accent", shape=shape)
        )
    assert reg.known_names() == ["apple", "mango", "zebra"]


def test_known_names_empty_registry() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    assert reg.known_names() == []


# ---------------------------------------------------------------------------
# Rule-of-three guard — more than 3 kinds work fine
# ---------------------------------------------------------------------------


def test_more_than_three_node_kinds() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    for i in range(5):
        reg.register(
            NodeKind(name=f"kind{i}", display_name=f"Kind {i}", color="--color-accent", shape="dot")
        )
    assert len(reg.known_names()) == 5


def test_more_than_three_edge_kinds() -> None:
    reg: KindRegistry[EdgeKind] = KindRegistry()
    for i in range(4):
        reg.register(
            EdgeKind(name=f"rel{i}", display_name=f"Rel {i}", color="--color-accent", style="solid")
        )
    assert len(reg.known_names()) == 4


# ---------------------------------------------------------------------------
# Display metadata round-trips
# ---------------------------------------------------------------------------


def test_node_kind_metadata_roundtrip() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    k = NodeKind(
        name="fancy",
        display_name="Fancy Node",
        color="--color-node-file",
        shape="circle",
        icon="star",
    )
    reg.register(k)
    result = reg.get("fancy")
    assert result is not None
    assert result.display_name == "Fancy Node"
    assert result.shape == "circle"
    assert result.icon == "star"


def test_edge_kind_metadata_roundtrip() -> None:
    reg: KindRegistry[EdgeKind] = KindRegistry()
    k = EdgeKind(name="owns", display_name="Owns", color="--color-edge-call", style="dashed")
    reg.register(k)
    result = reg.get("owns")
    assert result is not None
    assert result.display_name == "Owns"
    assert result.style == "dashed"


def test_node_kind_icon_defaults_none() -> None:
    reg: KindRegistry[NodeKind] = KindRegistry()
    k = NodeKind(name="plain", display_name="Plain", color="--color-accent", shape="dot")
    reg.register(k)
    assert reg.get("plain") is not None
    assert reg.get("plain").icon is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Module-level singleton defaults
# ---------------------------------------------------------------------------


def test_default_node_kinds_registered() -> None:
    assert node_kinds.get("file") is not None
    assert node_kinds.get("class") is not None
    assert node_kinds.get("function") is not None
    assert node_kinds.get("method") is not None
    assert node_kinds.get("interface") is not None
    assert node_kinds.get("type_alias") is not None
    assert node_kinds.get("enum") is not None
    assert node_kinds.get("struct") is not None


def test_default_edge_kinds_registered() -> None:
    assert edge_kinds.get("import") is not None
    assert edge_kinds.get("call") is not None
    assert edge_kinds.get("inherit") is not None
    assert edge_kinds.get("implements") is not None


def test_default_node_kind_colors_are_tokens() -> None:
    for name in (
        "file",
        "class",
        "function",
        "method",
        "interface",
        "type_alias",
        "enum",
        "struct",
    ):
        kind = node_kinds.get(name)
        assert kind is not None
        assert kind.color.startswith("--color-"), f"{name!r}: {kind.color!r}"


def test_default_edge_kind_colors_are_tokens() -> None:
    for name in ("import", "call", "inherit", "implements"):
        kind = edge_kinds.get(name)
        assert kind is not None
        assert kind.color.startswith("--color-"), f"{name!r}: {kind.color!r}"
