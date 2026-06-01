"""Node-ID resolution for the Node/V8 runtime adapter (ADR-0022).

Mirrors ``python_runtime/node_resolution.py`` but resolves V8 CDP *callFrames*
(``{url, lineNumber, functionName}``) to TypeScript static-graph node IDs instead
of Python ``CodeType`` attributes. Both share the index build / cached
normalisation / resolution machinery in
:class:`grackle.adapters.runtime_resolution.RuntimeResolver`; this subclass adds
the ``functionName`` name-fallback query and supplies :meth:`_normalize` for a
``file://`` URL.

Type-stripping (Node >= 22.6, ``--experimental-strip-types``) is the unlock: it
replaces TypeScript type annotations with whitespace, so the file V8 actually
executes keeps its ``.ts`` URL *and* its original line numbers. A profiler
callFrame ``{url:"file://.../src/math.ts", lineNumber:4 (0-based), functionName:"fib"}``
therefore resolves directly: ``(src/math.ts, 4 + 1) -> src/math.ts:fib``.

Resolution contract (used by both the sampling and coverage channels):

- ``None``  -> the frame is *not* a project frame; the caller filters it out.
  Covers V8 pseudo-frames (``(root)``/``(program)``/``(idle)``/``(garbage
  collector)``), ``node:internal/*``, other non-``file`` URLs, empty URLs, and
  any file outside the project root.
- ``str``   -> a project node ID to emit/count. May be a function/method node, a
  file node (fallback), or ``UNRESOLVED`` for an in-project file the static graph
  did not index (kept visible rather than silently dropped).

Fallback chain (first match wins) once a frame is known to be in-project:

1. function/method node whose ``(path, line)`` matches exactly,
2. function/method node whose ``(path, functionName)`` matches *uniquely*,
3. file node for ``path``,
4. ``UNRESOLVED``.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from grackle.adapters.runtime_resolution import NOT_PROJECT, UNRESOLVED, RuntimeResolver
from grackle.paths import to_posix

# V8 synthetic frames that carry no source position. Filtered, never surfaced.
_PSEUDO_FUNCTIONS = frozenset({"(root)", "(program)", "(idle)", "(garbage collector)", "(gc)"})

# Re-exported so ``from ...node_resolution import UNRESOLVED`` keeps working for
# the launcher and tests after the constant moved to the shared base.
__all__ = ["UNRESOLVED", "NodeResolver"]


class NodeResolver(RuntimeResolver):
    """Pre-indexed lookup from a V8 callFrame to a TypeScript node ID."""

    def resolve_frame(
        self,
        url: str,
        line: int | None,
        function_name: str | None = None,
    ) -> str | None:
        """Resolve a V8 callFrame to a node ID, or ``None`` to filter it.

        Args:
            url: The callFrame ``url`` (e.g. ``"file:///.../src/app.ts"``).
            line: 1-based source line of the frame (V8 ``lineNumber`` + 1), or
                ``None`` when only a name is available.
            function_name: The callFrame ``functionName`` (used for the name
                fallback and pseudo-frame filtering).
        """
        if function_name in _PSEUDO_FUNCTIONS:
            return None
        posix = self._cached_normalize(url)
        if posix == NOT_PROJECT:
            return None

        # V8 reports the top-level/module frame with an empty functionName, but its
        # reported line is NOT always 1 — it tracks the first executing statement,
        # which can coincide with a function declared on that line. A by-line lookup
        # would then mis-attribute the module frame to that function. So treat ANY
        # empty-name frame as a module frame and route it to the file node, mirroring
        # the Python resolver's literal "<module>" guard. Truly anonymous callbacks
        # (also empty-name) likewise fall to the file node rather than guessing a
        # line-colliding function — an accepted trade documented in ADR-0022.
        module_frame = not function_name

        if not module_frame and line is not None:
            sym_id = self._sym_index.get((posix, line))
            if sym_id is not None:
                return sym_id

        name_id = self._resolve_by_name(posix, function_name)
        if name_id is not None:
            return name_id

        file_id = self._file_index.get(posix)
        if file_id is not None:
            return file_id
        return UNRESOLVED

    def source_path(self, url: str) -> Path | None:
        """Return the absolute filesystem path for a project-file *url*, else ``None``.

        Used by the coverage channel to read a script's source (for the
        offset→line map) without a CDP round-trip — type-stripping preserves line
        boundaries, so the on-disk ``.ts`` matches what V8 executed.
        """
        posix = self._cached_normalize(url)
        if posix == NOT_PROJECT:
            return None
        return self._root / posix

    def _normalize(self, identifier: str) -> str | None:
        """Normalise a callFrame URL to a POSIX-relative project path, or None.

        Returns ``None`` for empty URLs, non-``file`` schemes (``node:``,
        ``http(s):``, ``eval``/``<anonymous>``), and paths outside the root.
        """
        if not identifier:
            return None
        if identifier.startswith("file://"):
            # url2pathname converts the URL path to a native filesystem path,
            # handling percent-decoding and the Windows /C:/... → C:\... case.
            parsed = urlparse(identifier)
            host = parsed.netloc
            if host and host.lower() != "localhost":
                # UNC path: file://server/share/... → \\server\share\... — the
                # authority is the host and must be reattached, not dropped.
                filename = url2pathname(f"//{host}{parsed.path}")
            else:
                filename = url2pathname(parsed.path)
        elif "://" in identifier or identifier.startswith("node:"):
            # Remote/builtin/synthetic source — never a project file.
            return None
        else:
            # A bare path (rare from V8, but tolerate it — includes Windows
            # drive-letter paths that urlparse would misread as a scheme).
            filename = identifier
        try:
            return to_posix(Path(filename), self._root)
        except (ValueError, OSError):
            return None
