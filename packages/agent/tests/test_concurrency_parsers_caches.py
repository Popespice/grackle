"""Campaign T7-1 and T7-4 (docs/test-campaigns/phase-12.md): the shared,
unlocked state that watch mode's rebuild thread and the connect path both use.

``serve --watch`` rebuilds the graph on a dedicated executor thread while a
connecting client's parse runs inline on the event-loop thread (ADR-0027 §5).
Two pieces of state are shared across those threads with no lock.

**T7-1 — the tree-sitter ``Parser`` singleton.** ``tree_sitter_runtime.get_parser``
hands every caller one cached ``Parser`` per language. The C parser behind it
is not reentrant: two threads inside ``ts_parser_parse`` on one parser
corrupt it, and the process crashes (10/10 runs, SIGSEGV or SIGBUS, in
``test_harness_detects_concurrent_entry_into_one_parser``, which runs
nightly). What makes the singleton safe today is that py-tree-sitter 0.25
holds the GIL for the whole of a ``parse(bytes)`` call. Measured: a
pure-Python ticker thread falls silent for 89–92% of a ~1 MB parse's wall
time, the rest being its turns at either edge of the call
(``test_parse_holds_the_gil_for_the_whole_call``). So concurrent callers are
serialized and never overlap inside the C parser.

That safety is contingent, not designed. It breaks as soon as Python code
runs in the middle of a parse, because the GIL can then pass to another
thread that enters the same parser. Anything that does this would break it:
a ``logger`` on the parser, or the read-callback form of ``parse``, which is
the only form that accepts a ``progress_callback``. py-tree-sitter 0.25
deprecates ``timeout_micros`` in favour of that callback, and ADR-0027 lists
"a cooperatively cancellable parser" as future work. A GIL-free CPython build,
or a future py-tree-sitter release that drops the GIL during parsing, would
break it in the same way. The pins below race real ``.parse()`` calls, both
directly on the singleton and through the real walkers, in a child process,
so that a crash fails one assertion cleanly instead of killing the test run.

**T7-4 — ``meta_cache`` / ``predicted_ctx.cache``.** Both are plain dicts that
``server._cache_bounded`` bounds with ``pop(next(iter(cache)))``. Its
``except (StopIteration, RuntimeError)`` absorbs another thread's
insert/evict landing between ``iter()`` and ``next()``. That guard is the T4-4
calibration survivor: no test used to reach it, and narrowing it left the
suite green. The race is real, though. With plain dicts and two threads
through the real ``_cache_bounded``, it is reached about once per 300k calls
at the default 5 ms switch interval (10–15 times per 4M calls, 5 runs), and
136–228 times per 40k calls at 1 µs (30 runs). So it is exercised here three
ways: deterministically on ``_cache_bounded`` itself; deterministically through the
real ``_build_static_graph`` on both caches, where losing the guard drops
``predicted_heat`` from one push and raises out of the other (which ends
watch mode); and as a genuine plain-dict hammer. The ``StopIteration`` half
of the guard is defensive only. The caches are mutated nowhere but
``_cache_bounded``, which evicts only while ``len > limit``, so with a cap of
16 or 64 and two writers a cache can never be empty when ``next()`` runs.
"""

from __future__ import annotations

import collections
import concurrent.futures
import contextlib
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

import grackle.server as server_module

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import CodeType

    from tree_sitter import Point, Tree

    from grackle.cache import CacheManager


# ===========================================================================
# T7-1 — child side. Runs in a fresh interpreter started by _run_child, which
# loads this file by path and calls _race_child_main; kept here (not in a
# helper module) so it is linted and type-checked with the tests that use it.
# ===========================================================================


def _source(lang: str, variant: int, blocks: int) -> bytes:
    """A deterministic source file; ``variant`` changes both the identifiers
    and the number of blocks, so any two variants parse to different trees."""
    if lang in ("typescript", "tsx"):
        unit = (
            "export function f{v}_{k}(a: number, b: string): number {{\n"
            "  const xs = [1, 2, {k}].map((x) => x * a + {v});\n"
            "  if (b.length > {k}) {{ return xs[0]; }}\n"
            "  return g{v}_{k}(a) + xs.length;\n"
            "}}\n"
            "export class C{v}_{k} {{ m(y: string): number {{ return f{v}_{k}(y.length, y); }} }}\n"
        )
        if lang == "tsx":
            unit += 'export const E{v}_{k} = () => <div id="{k}">{{"v{v}"}}</div>;\n'
        header = ""
    elif lang == "go":
        unit = (
            "func F{v}_{k}(a int, b string) int {{\n"
            "\txs := []int{{1, 2, {k}}}\n"
            "\tif len(b) > {k} {{ return xs[0] }}\n"
            "\treturn G{v}_{k}(a) + len(xs) + {v}\n"
            "}}\n"
            "type T{v}_{k} struct{{ n int }}\n"
            'func (t T{v}_{k}) M() int {{ return F{v}_{k}(t.n, "x") }}\n'
        )
        header = "package p{v}\n\n"
    elif lang == "rust":
        unit = (
            "pub fn f{v}_{k}(a: i64, b: &str) -> i64 {{\n"
            "    let xs = vec![1, 2, {k}];\n"
            "    if b.len() > {k} {{ xs[0] }} else {{ g{v}_{k}(a) + {v} }}\n"
            "}}\n"
            "pub struct S{v}_{k} {{ n: i64 }}\n"
            'impl S{v}_{k} {{ pub fn m(&self) -> i64 {{ f{v}_{k}(self.n, "x") }} }}\n'
        )
        header = ""
    else:
        raise ValueError(lang)
    body = "".join(unit.format(v=variant, k=k) for k in range(blocks + variant))
    return (header.format(v=variant) + body).encode()


def _tree_digest(tree: Tree) -> str:
    """S-expression plus every node's (type, start, end), preorder."""
    h = hashlib.sha256(str(tree.root_node).encode())
    cursor = tree.walk()
    while True:
        node = cursor.node
        if node is not None:
            h.update(f"{node.type}:{node.start_byte}:{node.end_byte};".encode())
        if cursor.goto_first_child():
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return h.hexdigest()


class _NoCache:
    """A cache that always misses, so every walk really parses every file."""

    def get(self, path: Path) -> None:
        return None

    def set(self, path: Path, content_hash: str, partial: dict[str, Any]) -> None:
        return None


class _Tally:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.parses = 0
        self.mismatches = 0
        self.errors: list[str] = []
        self.parser_ids: set[int] = set()

    def record(self, got: str, want: str) -> None:
        with self.lock:
            self.parses += 1
            if got != want:
                self.mismatches += 1

    def error(self, exc: BaseException) -> None:
        with self.lock:
            self.errors.append(repr(exc))

    def summary(self) -> dict[str, Any]:
        return {
            "parses": self.parses,
            "mismatches": self.mismatches,
            "errors": self.errors[:3],
            "error_count": len(self.errors),
            "distinct_parsers": len(self.parser_ids),
        }


def _run_racers(threads: int, switch_interval: float, work: Callable[[int], None]) -> None:
    """Start ``threads`` workers at one barrier with the GIL switch interval
    shrunk, so any Python code that runs mid-parse hands the GIL over."""
    barrier = threading.Barrier(threads)

    def _worker(tid: int) -> None:
        barrier.wait()
        work(tid)

    old = sys.getswitchinterval()
    sys.setswitchinterval(switch_interval)
    try:
        racers = [threading.Thread(target=_worker, args=(t,)) for t in range(threads)]
        for r in racers:
            r.start()
        for r in racers:
            r.join()
    finally:
        sys.setswitchinterval(old)


def _race_direct(cfg: dict[str, Any]) -> dict[str, Any]:
    """Every thread parses through the ``get_parser`` singleton at once."""
    return {lang: _race_direct_one(lang, cfg) for lang in cfg["langs"]}


def _race_direct_one(lang: str, cfg: dict[str, Any]) -> dict[str, Any]:
    from grackle.tree_sitter_runtime import get_parser

    threads: int = cfg["threads"]
    sources = [_source(lang, v, cfg["blocks"]) for v in range(threads)]
    reference = get_parser(lang)
    expected = [_tree_digest(reference.parse(s)) for s in sources]
    tally = _Tally()

    def work(tid: int) -> None:
        parser = get_parser(lang)
        with tally.lock:
            tally.parser_ids.add(id(parser))
        for it in range(cfg["iterations"]):
            v = (tid + it) % threads
            try:
                got = _tree_digest(parser.parse(sources[v]))
            except Exception as exc:  # noqa: BLE001 — tallied, not raised
                tally.error(exc)
                continue
            tally.record(got, expected[v])

    _run_racers(threads, cfg["switch_interval"], work)
    return tally.summary()


def _race_walker(cfg: dict[str, Any]) -> dict[str, Any]:
    """Every thread runs the real walker over the same project at once; the
    walkers share the singleton through ``tree_sitter_walker.get_parser``,
    which is wrapped here only to record which parser each call returned."""
    import grackle.tree_sitter_walker as walker_module

    # The walker's module-level name for the runtime's get_parser (it is not
    # re-exported, so reach it through the module dict).
    walker_globals: dict[str, Any] = vars(walker_module)
    real_get_parser = walker_globals["get_parser"]
    tallies = {lang: _Tally() for lang in cfg["langs"]}

    def recording_get_parser(language: str) -> Any:
        parser = real_get_parser(language)
        tally = tallies[language]
        with tally.lock:
            tally.parser_ids.add(id(parser))
        return parser

    walker_globals["get_parser"] = recording_get_parser
    return {lang: _race_walker_one(lang, cfg, tallies[lang]) for lang in cfg["langs"]}


def _race_walker_one(lang: str, cfg: dict[str, Any], tally: _Tally) -> dict[str, Any]:
    from grackle.adapters.base import ParseOptions
    from grackle.go_parser.walker import GoWalker
    from grackle.rust_parser.walker import RustWalker
    from grackle.typescript_parser.walker import TSWalker

    walkers: dict[str, tuple[type[TSWalker | GoWalker | RustWalker], str]] = {
        "typescript": (TSWalker, ".ts"),
        "go": (GoWalker, ".go"),
        "rust": (RustWalker, ".rs"),
    }
    walker_cls, ext = walkers[lang]
    root = Path(cfg["workdir"]) / lang
    root.mkdir(parents=True, exist_ok=True)
    for v in range(cfg["files"]):
        (root / f"m{v}{ext}").write_bytes(_source(lang, v, cfg["blocks"]))
    cache = cast("CacheManager", _NoCache())

    def walk_digest() -> str:
        graph = walker_cls(root, ParseOptions(), cache).walk()
        return hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()

    expected = walk_digest()
    tally.parser_ids.clear()

    def work(_tid: int) -> None:
        for _ in range(cfg["iterations"]):
            try:
                got = walk_digest()
            except Exception as exc:  # noqa: BLE001 — tallied, not raised
                tally.error(exc)
                continue
            tally.record(got, expected)

    _run_racers(cfg["threads"], cfg["switch_interval"], work)
    return tally.summary()


def _race_callback(cfg: dict[str, Any]) -> dict[str, Any]:
    """The positive control: parse through a read callback that yields the GIL
    on every chunk, so threads genuinely overlap inside the C parser — on one
    shared parser (``shared``) or on one parser per thread."""
    from tree_sitter import Parser

    from grackle.tree_sitter_runtime import get_parser

    threads: int = cfg["threads"]
    lang = "typescript"
    language = get_parser(lang).language
    assert language is not None
    sources = [_source(lang, v, cfg["blocks"]) for v in range(threads)]
    expected = [_tree_digest(Parser(language).parse(s)) for s in sources]
    shared = Parser(language)
    tally = _Tally()

    def reader(src: bytes) -> Callable[[int, Point], bytes]:
        def read(byte_offset: int, _point: Point) -> bytes:
            time.sleep(0)  # hand the GIL over mid-parse
            return src[byte_offset : byte_offset + 64]

        return read

    def work(tid: int) -> None:
        parser = shared if cfg["shared"] else Parser(language)
        with tally.lock:
            tally.parser_ids.add(id(parser))
        for it in range(cfg["iterations"]):
            v = (tid + it) % threads
            try:
                got = _tree_digest(parser.parse(reader(sources[v])))
            except Exception as exc:  # noqa: BLE001 — tallied, not raised
                tally.error(exc)
                continue
            tally.record(got, expected[v])

    _run_racers(threads, cfg["switch_interval"], work)
    return {lang: tally.summary()}


def _race_child_main(raw_config: str) -> None:
    cfg: dict[str, Any] = json.loads(raw_config)
    modes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "direct": _race_direct,
        "walker": _race_walker,
        "callback": _race_callback,
    }
    result = modes[cfg["mode"]](cfg)
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


# ===========================================================================
# T7-1 — parent side
# ===========================================================================

_CHILD_BOOTSTRAP = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('_t7_parser_race_child', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "module._race_child_main(sys.argv[2])\n"
)

# 1 µs: with Python code anywhere inside a parse, the GIL would change hands
# there almost every time. With none (today), it cannot, whatever the value.
_SWITCH = 1e-6


def _run_child(cfg: dict[str, Any], timeout: float = 180.0) -> tuple[int, dict[str, Any], str]:
    proc = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", _CHILD_BOOTSTRAP, __file__, json.dumps(cfg)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result: dict[str, Any] = {}
    lines = proc.stdout.strip().splitlines()
    if proc.returncode == 0 and lines:
        result = json.loads(lines[-1])
    return proc.returncode, result, proc.stderr[-3000:]


def _assert_race_clean(cfg: dict[str, Any], runs_per_lang: int) -> dict[str, Any]:
    """Run a race child and require it clean. A "run" is one ``parse()`` in
    direct mode and one whole-project ``walk()`` in walker mode."""
    code, result, stderr = _run_child(cfg)
    assert code == 0, (
        f"race child exited {code} (negative = killed by that signal: -11 SIGSEGV, "
        f"-6 SIGABRT) — threads overlapped inside a shared tree-sitter parser; "
        f"stderr tail:\n{stderr}"
    )
    assert set(result) == set(cfg["langs"]), result
    for lang, tally in result.items():
        assert tally["error_count"] == 0, (lang, tally)
        assert tally["mismatches"] == 0, (lang, tally)
        assert tally["parses"] == runs_per_lang, (lang, tally)
        # The premise: every racing thread really used the one cached parser.
        assert tally["distinct_parsers"] == 1, (lang, tally)
    return result


def test_shared_parser_concurrent_parses_match_single_threaded(tmp_path: Path) -> None:
    """Eight threads parse different sources through ``get_parser``'s one
    cached ``Parser`` per language at once; every tree must equal the
    single-threaded parse of the same bytes."""
    cfg = {
        "mode": "direct",
        "langs": ["typescript", "tsx", "go", "rust"],
        "threads": 8,
        "iterations": 4,
        "blocks": 20,
        "switch_interval": _SWITCH,
    }
    _assert_race_clean(cfg, runs_per_lang=8 * 4)


def test_shared_parser_concurrent_walks_match_single_threaded(tmp_path: Path) -> None:
    """The same race through the production path: six threads run the real
    TS/Go/Rust walkers over one project at once (cache always missing, so
    every walk parses every file); every graph must equal a single-threaded
    walk's."""
    cfg = {
        "mode": "walker",
        "langs": ["typescript", "go", "rust"],
        "threads": 6,
        "iterations": 3,
        "files": 4,
        "blocks": 15,
        "switch_interval": _SWITCH,
        "workdir": str(tmp_path),
    }
    _assert_race_clean(cfg, runs_per_lang=6 * 3)


@pytest.mark.hammer
def test_shared_parser_hammer(tmp_path: Path) -> None:
    """Many more threads and iterations on both paths."""
    direct = {
        "mode": "direct",
        "langs": ["typescript", "tsx", "go", "rust"],
        "threads": 12,
        "iterations": 16,
        "blocks": 30,
        "switch_interval": _SWITCH,
    }
    _assert_race_clean(direct, runs_per_lang=12 * 16)
    walker = {
        "mode": "walker",
        "langs": ["typescript", "go", "rust"],
        "threads": 8,
        "iterations": 10,
        "files": 6,
        "blocks": 20,
        "switch_interval": _SWITCH,
        "workdir": str(tmp_path),
    }
    _assert_race_clean(walker, runs_per_lang=8 * 10)


@pytest.mark.hammer
@pytest.mark.parametrize("lang", ["typescript", "go", "rust"])
def test_parse_holds_the_gil_for_the_whole_call(lang: str) -> None:
    """The mechanism the singleton's safety rests on: no other Python thread
    runs while ``Parser.parse(bytes)`` is inside the C parser.

    A ticker thread timestamps as fast as it can; across a ~1 MB parse the
    longest silence must cover most of the call (measured locally: 0.886–0.916
    of it over 30 runs; the rest is the ticker's turn at either edge). A
    timing probe, so it runs nightly, not in the gate.
    """
    from grackle.tree_sitter_runtime import get_parser

    source = _source(lang, 0, 5000)
    parser = get_parser(lang)
    stamps: list[float] = []
    stop = threading.Event()

    def tick() -> None:
        while not stop.is_set():
            stamps.append(time.perf_counter())

    ticker = threading.Thread(target=tick)
    ticker.start()
    try:
        while not stamps:
            time.sleep(0.001)
        t0 = time.perf_counter()
        parser.parse(source)
        t1 = time.perf_counter()
    finally:
        stop.set()
        ticker.join()
    window = [t0, *(s for s in stamps if t0 < s < t1), t1]
    longest = max(b - a for a, b in zip(window, window[1:], strict=False))
    assert longest >= 0.5 * (t1 - t0), (
        f"another thread ran during {lang} parse (longest silence {longest:.4f}s of "
        f"{t1 - t0:.4f}s): py-tree-sitter now releases the GIL inside parse(), so "
        "get_parser()'s shared Parser is no longer serialized — see T7-1"
    )


@pytest.mark.hammer
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="deliberately crashes a child process; POSIX reports it as a signal exit",
)
def test_harness_detects_concurrent_entry_into_one_parser() -> None:
    """Positive control for the pins above: when threads really do overlap
    inside the C parser, the harness sees it.

    A read callback that yields the GIL on every 64-byte chunk forces the
    overlap. With one parser per thread every tree is still correct, which
    shows the callback form parses correctly. With one shared parser the
    child crashes or returns wrong trees. So a shared ``Parser`` is safe only
    while the GIL keeps callers out of it.
    """
    base: dict[str, Any] = {
        "mode": "callback",
        "threads": 4,
        "iterations": 8,
        "blocks": 20,
        "switch_interval": 0.005,  # the default: the callback's sleep(0) already yields
    }
    code, own, stderr = _run_child({**base, "shared": False})
    assert code == 0, stderr
    tally = own["typescript"]
    assert (tally["mismatches"], tally["error_count"], tally["parses"]) == (0, 0, 4 * 8)
    assert tally["distinct_parsers"] == 4

    code, shared, _ = _run_child({**base, "shared": True})
    corrupted = code != 0 or (
        shared["typescript"]["mismatches"] + shared["typescript"]["error_count"] > 0
    )
    assert corrupted, f"a shared parser entered concurrently came through clean: {shared}"


# ===========================================================================
# T7-4 — _cache_bounded's benign-race guard
# ===========================================================================


@contextlib.contextmanager
def _handled_in_cache_bounded() -> Iterator[collections.Counter[str]]:
    """Count, by type, the exceptions that reach ``_cache_bounded``'s guard.

    ``sys.monitoring``'s ``EXCEPTION_HANDLED`` fires when an exception is
    routed to an ``except`` block in the given code object — on entry, before
    the clause's type test (an unmatched exception that is re-raised can fire
    it more than once). With the real guard every entry is an absorption; the
    tests show that separately by asserting nothing escaped. Filtering on
    ``_cache_bounded``'s code object isolates the one guard under test without
    touching the production function.
    """
    mon = sys.monitoring
    tool = next((t for t in (4, 5) if mon.get_tool(t) is None), None)
    if tool is None:
        raise RuntimeError("no free sys.monitoring tool id for the guard probe")
    code = server_module._cache_bounded.__code__
    counts: collections.Counter[str] = collections.Counter()
    lock = threading.Lock()

    def on_handled(handled_in: CodeType, _offset: int, exc: BaseException) -> None:
        if handled_in is code:
            with lock:
                counts[type(exc).__name__] += 1

    mon.use_tool_id(tool, "grackle-campaign-t7-4")
    try:
        mon.register_callback(tool, mon.events.EXCEPTION_HANDLED, on_handled)
        mon.set_events(tool, mon.events.EXCEPTION_HANDLED)
        yield counts
    finally:
        mon.set_events(tool, mon.events.NO_EVENTS)
        mon.register_callback(tool, mon.events.EXCEPTION_HANDLED, None)
        mon.free_tool_id(tool)


class _StallingDict(dict[Any, Any]):
    """A dict that, the first time ``stall_thread`` iterates it, runs
    ``interfere`` after the iterator exists and before it is consumed.

    ``_cache_bounded`` evicts with ``next(iter(cache))``; for a dict subclass,
    ``iter()`` calls this ``__iter__``. The real ``dict`` key iterator is created
    first and so records the dict's size; ``interfere`` then lets the other
    thread insert and evict. The ``next()`` that follows is exactly what a
    thread switch between those two calls would produce. That is the window
    the guard exists for, held open deterministically instead of waited for.
    """

    def __init__(
        self,
        items: dict[Any, Any],
        *,
        stall_thread: int,
        interfere: Callable[[], None],
    ) -> None:
        super().__init__(items)
        self._stall_thread = stall_thread
        self._interfere = interfere
        self.stalled = False

    def __iter__(self) -> Iterator[Any]:
        it = super().__iter__()
        if not self.stalled and threading.get_ident() == self._stall_thread:
            self.stalled = True
            self._interfere()
        return it


def _watch_thread() -> concurrent.futures.ThreadPoolExecutor:
    """A stand-in for ``serve()``'s watch executor, down to its thread name."""
    return concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="grackle-watch-rebuild"
    )


def test_cache_bounded_absorbs_an_eviction_raced_between_iter_and_next() -> None:
    """The connect path is mid-eviction when the watch thread inserts and
    evicts on the same cache: its ``next()`` then raises "dictionary changed
    size during iteration". The guard must absorb exactly that, and the cache
    must still be at its cap with both entries in it."""
    limit = 16
    watch = _watch_thread()

    def interfere() -> None:
        watch.submit(server_module._cache_bounded, cache, "watch", "w", limit).result(timeout=10)

    cache = _StallingDict(
        {i: i for i in range(limit)}, stall_thread=threading.get_ident(), interfere=interfere
    )
    try:
        with _handled_in_cache_bounded() as handled:
            server_module._cache_bounded(cache, "connect", "c", limit)
    finally:
        watch.shutdown()

    assert cache.stalled, "probe precondition: the stall never happened inside _cache_bounded"
    assert handled == {"RuntimeError": 1}
    assert len(cache) == limit
    assert (cache["connect"], cache["watch"]) == ("c", "w")


def _python_project(root: Path, name: str) -> Path:
    root.mkdir()
    (root / f"{name}.py").write_text(
        f"def {name}_a():\n    {name}_b()\n\n\ndef {name}_b():\n    {name}_a()\n",
        encoding="utf-8",
    )
    return root


def test_build_static_graph_keeps_every_graph_complete_when_both_caches_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same race through the real ``_build_static_graph`` on both shared
    caches, with the watch thread running whole rebuilds of other graphs
    inside each window.

    Without the guard, the prediction-cache race drops ``predicted_heat`` from
    the connect-path push (the belt-and-braces handler swallows the error),
    and the ``meta_cache`` race raises out of ``_build_static_graph``, which on
    the watch thread would stop watch mode for good. With it, all three graphs
    come out complete and both caches end at their caps.
    """
    from grackle import ml_bridge

    def fake_predict(graph: Any, _model_path: Path) -> dict[str, Any]:
        return {"nodes": sorted(str(n["id"]) for n in graph["nodes"])}

    monkeypatch.setattr(ml_bridge, "learn_available", lambda: True)
    monkeypatch.setattr(ml_bridge, "predict_scores", fake_predict)
    model = tmp_path / "heat-model.npz"
    model.write_bytes(b"not a real checkpoint; predict_scores is stubbed")

    connect_root = _python_project(tmp_path / "connect", "connect")
    pending = [_python_project(tmp_path / n, n) for n in ("watch1", "watch2")]
    watch_graphs: list[Any] = []
    watch = _watch_thread()

    def interfere() -> None:
        root = pending.pop(0)
        rebuild = watch.submit(server_module._build_static_graph, root, meta_cache, predicted_ctx)
        watch_graphs.append(rebuild.result(timeout=30))

    me = threading.get_ident()
    meta_limit = server_module._META_CACHE_MAX
    pred_limit = server_module._PREDICTED_CACHE_MAX
    meta_cache = _StallingDict(
        {(i, i, i): {"hub_score": [], "cycles": []} for i in range(meta_limit)},
        stall_thread=me,
        interfere=interfere,
    )
    predicted_cache = _StallingDict(
        {(i, i, i): {} for i in range(pred_limit)}, stall_thread=me, interfere=interfere
    )
    predicted_ctx = server_module._PredictedHeatContext(
        model_path=model, root=tmp_path, cache=predicted_cache
    )
    try:
        with _handled_in_cache_bounded() as handled:
            graph = server_module._build_static_graph(connect_root, meta_cache, predicted_ctx)
    finally:
        watch.shutdown()

    assert predicted_cache.stalled and meta_cache.stalled, "probe precondition: a stall was missed"
    assert handled == {"RuntimeError": 2}
    assert len(meta_cache) == meta_limit
    assert len(predicted_cache) == pred_limit
    assert len(watch_graphs) == 2
    for built in (graph, *watch_graphs):
        assert built is not None
        metadata = built["metadata"]
        assert metadata["predicted_heat"] == fake_predict(built, model)
        assert "hub_score" in metadata
        assert "cycles" in metadata


def _plain_dict_race(
    calls_per_thread: int, limit: int = 16
) -> tuple[dict[Any, Any], list[BaseException], collections.Counter[str]]:
    """Two threads bound one plain dict through the real ``_cache_bounded``
    with distinct keys, the GIL switch interval shrunk so a switch between
    ``iter()`` and ``next()`` is common rather than rare."""
    cache: dict[Any, Any] = {("seed", i): i for i in range(limit)}
    escaped: list[BaseException] = []

    def work(tid: int) -> None:
        try:
            for n in range(calls_per_thread):
                server_module._cache_bounded(cache, (tid, n), n, limit)
        except BaseException as exc:  # noqa: BLE001 — surfaced by the caller's assert
            escaped.append(exc)

    with _handled_in_cache_bounded() as handled:
        _run_racers(2, _SWITCH, work)
    return cache, escaped, handled


def _assert_bounded(cache: dict[Any, Any], limit: int = 16) -> None:
    # A redundant eviction can leave one entry fewer than the cap; a skipped
    # one is always made up by the other thread's still-running eviction.
    assert limit - 1 <= len(cache) <= limit, len(cache)
    assert all(value == key[1] for key, value in cache.items()), cache


def test_cache_bounded_plain_dict_race_stays_within_the_cap() -> None:
    """Gate-sized sibling of the hammer below: a genuine two-thread race on a
    plain dict. It reaches the guard 136–228 times per run locally (30 runs),
    but asserts only what must hold whether or not it does."""
    cache, escaped, handled = _plain_dict_race(20_000)
    assert escaped == [], f"{escaped[:3]} escaped _cache_bounded (guard reached {handled})"
    _assert_bounded(cache)


@pytest.mark.hammer
def test_cache_bounded_plain_dict_race_hammer() -> None:
    """The benign-race claim under a genuine race: plain dicts, two threads,
    no instrumentation in the dict. The guard must actually be reached (so the
    claim is tested, not assumed), nothing may escape, the cap must hold, and
    only ``RuntimeError`` is ever absorbed — the ``StopIteration`` branch is
    unreachable while the cap exceeds the number of writer threads."""
    cache, escaped, handled = _plain_dict_race(500_000)
    assert escaped == [], f"{escaped[:3]} escaped _cache_bounded (guard reached {handled})"
    assert handled["RuntimeError"] >= 1, f"the race never reached the guard: {handled}"
    assert set(handled) == {"RuntimeError"}, handled
    _assert_bounded(cache)
