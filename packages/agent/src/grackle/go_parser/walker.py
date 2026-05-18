"""Go walker — TreeSitterWalker subclass."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grackle.go_parser.hints import extract_hints
from grackle.go_parser.visitors import GoFileVisitor
from grackle.python_parser.visitors import GraphBuilder
from grackle.tree_sitter_walker import TreeSitterWalker

if TYPE_CHECKING:
    from tree_sitter import Tree

    from grackle.adapters.base import StaticGraph


class GoWalker(TreeSitterWalker):
    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".go",)

    @property
    def language_name(self) -> str:
        return "go"

    def visit_tree(self, tree: Tree, source: str, file_id: str) -> GraphBuilder:
        builder = GraphBuilder()
        GoFileVisitor(file_id, builder).visit(tree)
        return builder

    def hints_for_file(self, source: str, file_id: str) -> list[Any]:
        return extract_hints(source, file_id)

    def _resolve(self, graph: StaticGraph) -> StaticGraph:
        from grackle.go_parser.resolver import resolve_graph

        return resolve_graph(graph, self._root)
