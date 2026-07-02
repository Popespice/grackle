"""Tests for python_runtime.tracer's value capture (ADR-0025, chunk 10.2).

Test rows are numbered to match the acceptance matrix in the Phase 10 plan
(chunk 10.2). Each numbered section is one row.
"""

from __future__ import annotations

from pathlib import Path

from grackle.adapters import registry
from grackle.adapters.base import ParseOptions, TraceEvent, TraceOptions
from grackle.python_runtime.node_resolution import NodeResolver
from grackle.python_runtime.tracer import Tracer

_FIXTURE_ROOT = Path(__file__).parents[4] / "fixtures" / "value-capture"
_SCRIPT = _FIXTURE_ROOT / "main.py"


def _make_tracer(options: TraceOptions) -> Tracer:
    graph = registry.get_static("python").parse(_FIXTURE_ROOT, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(_FIXTURE_ROOT, graph)
    return Tracer(resolver, options)


def _run(options: TraceOptions) -> list[TraceEvent]:
    return _make_tracer(options).run(_SCRIPT)


# ---------------------------------------------------------------------------
# 1. Default byte-identical: no --capture-values -> no `values` key anywhere
# ---------------------------------------------------------------------------


def test_no_values_key_by_default() -> None:
    events = _run(TraceOptions())
    assert len(events) > 0
    for e in events:
        assert "values" not in e


# ---------------------------------------------------------------------------
# 2. Args: call events carry values.args with correct names for every
#    signature shape in the fixture
# ---------------------------------------------------------------------------


def test_positional_args_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:add"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name.keys() == {"a", "b"}
    assert args_by_name["a"]["repr"] == "1"
    assert args_by_name["b"]["repr"] == "2"


def test_default_valued_arg_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:greet"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name.keys() == {"name", "greeting"}
    assert args_by_name["name"]["repr"] == "'Ada'"
    assert args_by_name["greeting"]["repr"] == "'hello'"


def test_keyword_only_arg_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:scale"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name.keys() == {"value", "factor"}
    assert args_by_name["value"]["repr"] == "3.0"
    assert args_by_name["factor"]["repr"] == "2.0"


def test_varargs_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:total"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert "numbers" in args_by_name
    assert "(1, 2, 3)" in args_by_name["numbers"]["repr"]


def test_varkeywords_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:describe"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name["fields"]["repr"] == "{'role': 'admin', 'active': True}"


def test_mixed_signature_all_kinds_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:mixed"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name.keys() == {"a", "b", "rest", "tag", "extra"}
    assert args_by_name["a"]["repr"] == "1"
    assert args_by_name["b"]["repr"] == "2"
    assert args_by_name["rest"]["repr"] == "(3, 4)"
    assert args_by_name["tag"]["repr"] == "'y'"
    assert args_by_name["extra"]["repr"] == "{'extra_field': 'z'}"


def test_instance_method_captures_self() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:Widget.rename"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name.keys() == {"self", "new_name"}
    assert args_by_name["self"]["repr"] == "<Widget object>"
    assert args_by_name["new_name"]["repr"] == "'gizmo'"


def test_staticmethod_has_no_declared_params_so_no_values_key() -> None:
    """No declared parameters at all -> no `values` key is attached (an empty
    args list is never attached; the capture budget is not spent either)."""
    events = _run(TraceOptions(capture_values=True))
    calls = [
        e for e in events if e["event"] == "call" and e["node_id"] == "main.py:Widget.describe_kind"
    ]
    assert calls
    assert "values" not in calls[0]


def test_classmethod_captures_cls() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [
        e for e in events if e["event"] == "call" and e["node_id"] == "main.py:Widget.from_default"
    ]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name.keys() == {"cls"}
    assert args_by_name["cls"]["repr"] == "<type object>"


def test_recursive_calls_each_capture_their_own_arg() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:factorial"]
    # factorial(5) recurses 5 times -> 5 distinct call events, each its own n.
    assert len(calls) == 5
    seen_reprs = {a["repr"] for c in calls for a in c["values"]["args"] if a["name"] == "n"}
    assert seen_reprs == {"5", "4", "3", "2", "1"}


def test_async_def_captures_args() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:fetch_value"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name.keys() == {"x"}
    assert args_by_name["x"]["repr"] == "21"


def test_frame_mismatch_degrades_to_no_args_capture(tmp_path: Path) -> None:
    """The ``frame.f_code is code`` identity check inside ``_on_call`` must
    degrade to no-args capture (never crash, never attribute a wrong frame's
    locals to the event) when the just-fetched frame does not match the code
    object PY_START reported — the scenario a dispatch-shape difference
    across Python versions, or a resumed generator/coroutine frame, would
    produce for real (ADR-0025's documented highest-risk path).

    Real sys.monitoring never actually fires PY_START for this fixture's
    generator/async frames a second time (PY_START fires once per frame
    lifetime; resumes are PY_RESUME, which this tracer doesn't subscribe to),
    so the mismatch branch is otherwise unreachable through a normal trace.
    Calling ``_on_call`` directly (bypassing real sys.monitoring dispatch)
    forces it deterministically: ``sys._getframe(1)`` inside ``_on_call``
    then resolves to *this test function's own frame*, whose code object can
    never equal the target function's code object.

    Critically, THIS test function's own local ``x`` is bound to a
    deliberately wrong sentinel value — the same name as the target
    function's declared parameter. Without the ``is`` identity check (e.g.
    if it were accidentally weakened to trust any frame with a matching
    variable name), ``_read_declared_args`` would find ``x`` in this test
    frame's locals and wrongly attribute the sentinel to the event; the
    assertion below only holds because the guard is in effect.
    """
    import importlib.util

    x = "WRONG_CALLER_FRAME_VALUE"  # noqa: F841 - deliberately shadows f's param name; see docstring

    script = tmp_path / "solo.py"
    script.write_text("def f(x: int) -> int:\n    return x\n\nf(1)\n", encoding="utf-8")
    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions(capture_values=True))

    spec = importlib.util.spec_from_file_location("solo", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code = module.f.__code__

    result = tracer._on_call(code, 0)  # noqa: SLF001 - intentionally exercising the private hot path

    assert result is None  # not DISABLE — is_project_file matched
    assert len(tracer._events) == 1
    call_event = tracer._events[0]
    assert call_event["event"] == "call"
    assert call_event["node_id"] == "solo.py:f"
    # Degraded to no-args capture: no `values` key attached, no crash, and
    # critically no leak of this test frame's own `x` local.
    assert "values" not in call_event


# ---------------------------------------------------------------------------
# 3. Returns: return events carry values.ret
# ---------------------------------------------------------------------------


def test_return_value_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    returns = [e for e in events if e["event"] == "return" and e["node_id"] == "main.py:add"]
    assert returns
    assert returns[0]["values"]["ret"] == "3"


def test_async_def_return_captured() -> None:
    events = _run(TraceOptions(capture_values=True))
    returns = [
        e for e in events if e["event"] == "return" and e["node_id"] == "main.py:fetch_value"
    ]
    assert returns
    assert returns[0]["values"]["ret"] == "42"


# ---------------------------------------------------------------------------
# 4. Generator / comprehension frames: don't crash, never capture the `.0`
#    slot, and the underlying iterator is not advanced by capture
# ---------------------------------------------------------------------------


def test_generator_return_captured_without_crashing() -> None:
    events = _run(TraceOptions(capture_values=True))
    returns = [e for e in events if e["event"] == "return" and e["node_id"] == "main.py:squares"]
    assert returns
    assert "values" in returns[0]


def test_no_dot_prefixed_arg_names_anywhere() -> None:
    """The genexpr call site inside sum_of_squares has its own code
    object/frame with an implicit `.0` iterator parameter — it must never
    appear as a captured arg name."""
    events = _run(TraceOptions(capture_values=True))
    for e in events:
        values = e.get("values")
        if values is None:
            continue
        for arg in values.get("args", []):
            assert not arg["name"].startswith(".")


def test_capture_does_not_perturb_generator_expression_result() -> None:
    """If value capture ever consumed the genexpr's .0 iterator argument
    while formatting it, the real computed total would come out wrong — a
    Heisenbug in the debugger itself. sum_of_squares(5) is deterministic:
    sum(x*x for x in range(5)) + sum(squares(5)) == 30 + 30 == 60."""
    events = _run(TraceOptions(capture_values=True))
    ret = next(
        e for e in events if e["event"] == "return" and e["node_id"] == "main.py:sum_of_squares"
    )
    assert ret["values"]["ret"] == "60"


# ---------------------------------------------------------------------------
# 5. Redaction: sensitive-named params -> {repr: "<redacted>", redacted: true};
#    --no-redact (redact_values=False) bypasses it
# ---------------------------------------------------------------------------


def test_password_arg_redacted_by_default() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:login"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name["password"]["repr"] == "<redacted>"
    assert args_by_name["password"]["redacted"] is True
    assert args_by_name["username"]["repr"] == "'ada'"
    assert "redacted" not in args_by_name["username"]


def test_api_key_arg_redacted_by_default() -> None:
    events = _run(TraceOptions(capture_values=True))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:call_api"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name["api_key"]["repr"] == "<redacted>"
    assert args_by_name["api_key"]["redacted"] is True


def test_no_redact_bypasses_redaction() -> None:
    events = _run(TraceOptions(capture_values=True, redact_values=False))
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "main.py:login"]
    assert calls
    args_by_name = {a["name"]: a for a in calls[0]["values"]["args"]}
    assert args_by_name["password"]["repr"] == "'s3cret'"
    assert "redacted" not in args_by_name["password"]


# ---------------------------------------------------------------------------
# 6. Per-node budget: capture_first_n bounds *capture*, never *emission*
# ---------------------------------------------------------------------------


def test_per_node_budget_bounds_capture_not_emission(tmp_path: Path) -> None:
    script = tmp_path / "hot.py"
    script.write_text(
        "def hot(i: int) -> int:\n    return i * 2\n\nfor _n in range(300):\n    hot(_n)\n",
        encoding="utf-8",
    )
    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions(capture_values=True, capture_first_n=100))
    events = tracer.run(script)

    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "hot.py:hot"]
    returns = [e for e in events if e["event"] == "return" and e["node_id"] == "hot.py:hot"]
    # All 300 calls and 300 returns are still emitted regardless of capture.
    assert len(calls) == 300
    assert len(returns) == 300

    captured_calls = [e for e in calls if "values" in e]
    captured_returns = [e for e in returns if "values" in e]
    # Budget is shared per node_id across call+return (the MVP posture) —
    # exactly capture_first_n total captures, never zero, never all 600.
    total_captured = len(captured_calls) + len(captured_returns)
    assert total_captured == 100
    assert 0 < len(captured_calls) <= 100
    assert 0 < len(captured_returns) <= 100


def test_per_node_budget_is_independent_per_node(tmp_path: Path) -> None:
    """A second, distinct function gets its own full, independent budget —
    not a single budget shared globally across every node_id.

    If both had `> 0` asserted alone this wouldn't discriminate a real
    per-node_id budget from one global counter shared across all nodes (both
    would still end up non-empty either way with this call pattern). Asserting
    the exact combined total does discriminate it: under a shared/global
    budget of 100, the combined total across both nodes would be capped at
    100; under the real per-node_id budget, each node gets its own full 100.
    """
    script = tmp_path / "two.py"
    script.write_text(
        "def a(i: int) -> int:\n    return i\n\n"
        "def b(i: int) -> int:\n    return i\n\n"
        "for _n in range(150):\n    a(_n)\n    b(_n)\n",
        encoding="utf-8",
    )
    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions(capture_values=True, capture_first_n=100))
    events = tracer.run(script)

    def _captured(node_id: str) -> int:
        return sum(1 for e in events if e["node_id"] == node_id and "values" in e)

    a_total = _captured("two.py:a")
    b_total = _captured("two.py:b")
    assert a_total == 100
    assert b_total == 100
    assert a_total + b_total == 200


# ---------------------------------------------------------------------------
# 7. Size caps: an over-limit arg is truncated
# ---------------------------------------------------------------------------


def test_long_arg_value_truncated(tmp_path: Path) -> None:
    script = tmp_path / "longarg.py"
    script.write_text(
        "def take(s: str) -> str:\n    return s\n\ntake('x' * 500)\n",
        encoding="utf-8",
    )
    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions(capture_values=True, max_value_len=50))
    events = tracer.run(script)
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "longarg.py:take"]
    assert calls
    arg = next(a for a in calls[0]["values"]["args"] if a["name"] == "s")
    assert arg["truncated"] is True
    assert len(arg["repr"]) <= 50


def test_deep_arg_value_truncated(tmp_path: Path) -> None:
    script = tmp_path / "deeparg.py"
    script.write_text(
        "def take(x: object) -> object:\n    return x\n\n"
        "nested = 0\n"
        "for _ in range(20):\n    nested = [nested]\n"
        "take(nested)\n",
        encoding="utf-8",
    )
    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions(capture_values=True, max_value_depth=3))
    events = tracer.run(script)
    calls = [e for e in events if e["event"] == "call" and e["node_id"] == "deeparg.py:take"]
    assert calls
    arg = next(a for a in calls[0]["values"]["args"] if a["name"] == "x")
    assert arg["truncated"] is True
