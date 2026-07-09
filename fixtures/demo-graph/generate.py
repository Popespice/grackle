"""Generate synthetic graph fixtures of various sizes for the demo branch.

Run from repo root:
    uv run --project packages/agent python fixtures/demo-graph/generate.py

Produces tiny.json / medium.json / large.json / huge.json alongside the
hand-authored graph.json. Each fixture matches the same shape the real
PythonStaticParser will emit in phase 2.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------- preset definitions ----------


@dataclass(frozen=True, slots=True)
class Preset:
    name: str
    label: str
    description: str
    packages: int
    files_per_pkg: int
    classes_per_file: int
    methods_per_class: int
    funcs_per_file: int
    import_density: float  # 0..1 chance a file imports another
    call_density: float  # 0..1 chance a callable calls another
    inherit_chance: float  # 0..1 chance a class inherits from another in the same package
    seed: int = 42


PRESETS: list[Preset] = [
    Preset(
        name="tiny",
        label="Tiny",
        description="single-file utility (~10 nodes)",
        packages=1,
        files_per_pkg=1,
        classes_per_file=1,
        methods_per_class=3,
        funcs_per_file=2,
        import_density=0.0,
        call_density=0.5,
        inherit_chance=0.0,
    ),
    Preset(
        name="medium",
        label="Medium",
        description="small library (~200 nodes)",
        packages=4,
        files_per_pkg=5,
        classes_per_file=2,
        methods_per_class=3,
        funcs_per_file=2,
        import_density=0.06,
        call_density=0.01,
        inherit_chance=0.08,
    ),
    Preset(
        name="large",
        label="Large",
        description="real project (~1500 nodes)",
        packages=8,
        files_per_pkg=10,
        classes_per_file=2,
        methods_per_class=4,
        funcs_per_file=3,
        import_density=0.02,
        call_density=0.002,
        inherit_chance=0.06,
    ),
    Preset(
        name="huge",
        label="Huge",
        description="stress test (~5000 nodes)",
        packages=15,
        files_per_pkg=15,
        classes_per_file=3,
        methods_per_class=5,
        funcs_per_file=3,
        import_density=0.005,
        call_density=0.0004,
        inherit_chance=0.05,
    ),
]

# ---------- name pools ----------

PKG_WORDS = [
    "core", "utils", "api", "models", "services", "db", "auth", "cache",
    "queue", "tasks", "config", "telemetry", "render", "io", "net", "ml",
    "ops", "feeds", "search", "graph", "math", "geo", "ui",
]

FILE_WORDS = [
    "client", "server", "store", "router", "handler", "manager", "factory",
    "builder", "loader", "parser", "writer", "reader", "publisher",
    "subscriber", "scheduler", "worker", "validator", "serializer",
    "transformer", "monitor", "registry", "queue", "stream", "pool",
    "session", "context", "controller", "view", "model", "adapter",
]

CLASS_NAMES = [
    "Service", "Client", "Manager", "Handler", "Router", "Store", "Cache",
    "Queue", "Pool", "Factory", "Builder", "Loader", "Parser", "Worker",
    "Scheduler", "Registry", "Adapter", "Validator", "Controller",
]

METHOD_NAMES = [
    "init", "start", "stop", "run", "process", "handle", "load", "save",
    "fetch", "send", "receive", "parse", "format", "validate", "transform",
    "build", "create", "destroy", "connect", "disconnect", "register",
    "unregister", "subscribe", "publish", "encode", "decode",
]

FUNC_NAMES = [
    "main", "helper", "compute", "hash_password", "log_event", "format_date",
    "parse_config", "load_module", "encode_payload", "decode_token",
    "validate_input", "build_query", "execute_task", "schedule_job",
]


# ---------- generator ----------


def _maybe_dup(name: str, used: set[str], idx: int) -> str:
    """Append a numeric suffix if name has been seen."""
    if name not in used:
        used.add(name)
        return name
    candidate = f"{name}_{idx}"
    used.add(candidate)
    return candidate


def generate_fixture(p: Preset) -> dict[str, Any]:
    rng = random.Random(p.seed)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    file_ids: list[str] = []
    class_ids: list[str] = []
    callable_ids: list[str] = []  # method ids + function ids
    classes_by_pkg: dict[str, list[str]] = {}

    used_pkg_names: set[str] = set()
    used_class_names: set[str] = set()

    for pkg_idx in range(p.packages):
        pkg_word = PKG_WORDS[pkg_idx % len(PKG_WORDS)]
        pkg = _maybe_dup(pkg_word, used_pkg_names, pkg_idx)
        used_file_basenames: set[str] = set()

        for file_idx in range(p.files_per_pkg):
            base = FILE_WORDS[(pkg_idx + file_idx) % len(FILE_WORDS)]
            file_basename = _maybe_dup(base, used_file_basenames, file_idx)
            file_path = f"{pkg}/{file_basename}.py"
            nodes.append(
                {
                    "id": file_path,
                    "kind": "file",
                    "name": f"{file_basename}.py",
                    "path": file_path,
                    "x": rng.uniform(-1.0, 1.0),
                    "y": rng.uniform(-1.0, 1.0),
                }
            )
            file_ids.append(file_path)

            # classes
            for cls_idx in range(p.classes_per_file):
                cls_word = CLASS_NAMES[(pkg_idx + file_idx + cls_idx) % len(CLASS_NAMES)]
                cls = _maybe_dup(f"{cls_word}", used_class_names, len(class_ids))
                cls_id = f"{file_path}:{cls}"
                line = 8 + cls_idx * 30
                nodes.append(
                    {
                        "id": cls_id,
                        "kind": "class",
                        "name": cls,
                        "path": file_path,
                        "line": line,
                        "x": rng.uniform(-1.0, 1.0),
                        "y": rng.uniform(-1.0, 1.0),
                    }
                )
                class_ids.append(cls_id)
                classes_by_pkg.setdefault(pkg, []).append(cls_id)

                # methods
                for m_idx in range(p.methods_per_class):
                    mname = METHOD_NAMES[(file_idx + m_idx) % len(METHOD_NAMES)]
                    if m_idx == 0:
                        mname = "__init__"
                    method_id = f"{cls_id}.{mname}"
                    nodes.append(
                        {
                            "id": method_id,
                            "kind": "method",
                            "name": mname,
                            "path": file_path,
                            "line": line + 4 + m_idx * 6,
                            "metadata": {"parent": cls_id},
                            "x": rng.uniform(-1.0, 1.0),
                            "y": rng.uniform(-1.0, 1.0),
                        }
                    )
                    callable_ids.append(method_id)

            # top-level functions
            for fn_idx in range(p.funcs_per_file):
                fname = FUNC_NAMES[(pkg_idx + file_idx + fn_idx) % len(FUNC_NAMES)]
                func_id = f"{file_path}:{fname}_{fn_idx}" if fn_idx > 0 else f"{file_path}:{fname}"
                nodes.append(
                    {
                        "id": func_id,
                        "kind": "function",
                        "name": fname,
                        "path": file_path,
                        "line": 4 + fn_idx * 8,
                        "x": rng.uniform(-1.0, 1.0),
                        "y": rng.uniform(-1.0, 1.0),
                    }
                )
                callable_ids.append(func_id)

    # ---- import edges between files ----
    for i, src in enumerate(file_ids):
        # Each file imports between 1 and 4 others, biased by import_density
        candidates = file_ids[:i] + file_ids[i + 1 :]
        n_imports = 0
        for tgt in candidates:
            if rng.random() < p.import_density:
                n_imports += 1
                edges.append(
                    {"source": src, "target": tgt, "kind": "import", "metadata": {}}
                )
                if n_imports >= 6:
                    break

    # ---- inheritance edges within packages ----
    for pkg, ids in classes_by_pkg.items():
        for i, cls in enumerate(ids):
            if i == 0:
                continue
            if rng.random() < p.inherit_chance:
                # Pick a sibling earlier in the package as base
                base = rng.choice(ids[:i])
                if base != cls:
                    edges.append(
                        {
                            "source": cls,
                            "target": base,
                            "kind": "inherit",
                            "metadata": {},
                        }
                    )

    # ---- call edges ----
    for caller in callable_ids:
        for callee in callable_ids:
            if caller == callee:
                continue
            if rng.random() < p.call_density:
                edges.append(
                    {
                        "source": caller,
                        "target": callee,
                        "kind": "call",
                        "metadata": {"resolved": True},
                    }
                )

    return {
        "version": 1,
        "language": "python",
        "name": p.name,
        "label": p.label,
        "description": p.description,
        "nodes": nodes,
        "edges": edges,
    }


def main() -> None:
    out_dir = Path(__file__).parent
    for preset in PRESETS:
        fx = generate_fixture(preset)
        path = out_dir / f"{preset.name}.json"
        path.write_text(json.dumps(fx, indent=2))
        print(
            f"  wrote {path.relative_to(out_dir.parent.parent)}: "
            f"{len(fx['nodes'])} nodes / {len(fx['edges'])} edges"
        )


if __name__ == "__main__":
    main()
