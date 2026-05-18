"""Rust walker — TreeSitterWalker subclass."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.python_parser.visitors import GraphBuilder
from grackle.rust_parser.visitors import RustFileVisitor
from grackle.tree_sitter_walker import TreeSitterWalker

if TYPE_CHECKING:
    from tree_sitter import Tree

    from grackle.adapters.base import StaticGraph


class RustWalker(TreeSitterWalker):
    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".rs",)

    @property
    def language_name(self) -> str:
        return "rust"

    def visit_tree(self, tree: Tree, source: str, file_id: str) -> GraphBuilder:
        builder = GraphBuilder()
        RustFileVisitor(file_id, builder).visit(tree)
        return builder

    def _resolve(self, graph: StaticGraph) -> StaticGraph:
        from grackle.rust_parser.resolver import resolve_graph

        return resolve_graph(graph, self._root)
