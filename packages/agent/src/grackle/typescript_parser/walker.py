"""TypeScript/TSX walker — TreeSitterWalker subclass."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.python_parser.visitors import GraphBuilder
from grackle.tree_sitter_walker import TreeSitterWalker
from grackle.typescript_parser.visitors import TSFileVisitor

if TYPE_CHECKING:
    from tree_sitter import Tree

    from grackle.adapters.base import StaticGraph


class TSWalker(TreeSitterWalker):
    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".ts", ".tsx", ".mts", ".cts")

    @property
    def language_name(self) -> str:
        return "typescript"

    def visit_tree(self, tree: Tree, source: str, file_id: str) -> GraphBuilder:
        builder = GraphBuilder()
        TSFileVisitor(file_id, builder).visit(tree)
        return builder

    def _resolve(self, graph: StaticGraph) -> StaticGraph:
        from grackle.typescript_parser.resolver import resolve_graph

        return resolve_graph(graph)
