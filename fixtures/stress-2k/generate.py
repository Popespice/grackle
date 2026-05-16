"""
Generate a synthetic Python codebase for stress-testing grackle's static parser.

Target: ~2 000 nodes across ~200 files, realistic class/method/closure mix,
random call edges with ~70 % in-file ratio.

Run from the repo root:
    python3 fixtures/stress-2k/generate.py

Output files are written to fixtures/stress-2k/src/ and are committed so the
fixture is reproducible without running the generator again.
"""

import random
import textwrap
from pathlib import Path

SEED = 42
NUM_PACKAGES = 8
FILES_PER_PACKAGE = 25  # 8 * 25 = 200 files
CLASSES_PER_FILE = 2
METHODS_PER_CLASS = 4  # 200 * 2 * 4 = 1600 method nodes
FUNCTIONS_PER_FILE = 3  # 200 * 3 = 600 function nodes
CALL_EDGES_PER_FUNCTION = 3  # extra call refs sprinkled inside bodies
IN_FILE_RATIO = 0.7  # 70 % of calls stay in-file

OUT_DIR = Path(__file__).parent / "src"

PACKAGE_NAMES = [
    "auth",
    "core",
    "data",
    "events",
    "gateway",
    "models",
    "tasks",
    "utils",
]

MODULE_ADJECTIVES = [
    "base",
    "common",
    "config",
    "constants",
    "exceptions",
    "factory",
    "helpers",
    "interfaces",
    "manager",
    "mixins",
    "parsers",
    "registry",
    "schemas",
    "services",
    "signals",
    "storage",
    "types",
    "validators",
    "views",
    "workers",
    "cache",
    "client",
    "pipeline",
    "serializer",
    "transport",
]

DECORATOR_POOL = [
    "staticmethod",
    "classmethod",
    "property",
    "abstractmethod",
    "cache",
    "lru_cache",
    "dataclass",
]


def rng() -> random.Random:
    return random.Random(SEED)


def make_class_name(pkg: str, module: str, idx: int) -> str:
    return f"{pkg.capitalize()}{module.capitalize()}{idx}"


def make_method_name(idx: int) -> str:
    names = [
        "process",
        "validate",
        "transform",
        "load",
        "save",
        "render",
        "fetch",
        "build",
        "parse",
        "encode",
        "decode",
        "register",
        "dispatch",
        "emit",
        "handle",
        "resolve",
        "compute",
        "apply",
        "run",
        "execute",
    ]
    return names[idx % len(names)] + (f"_{idx // len(names)}" if idx >= len(names) else "")


def make_function_name(pkg: str, idx: int) -> str:
    prefixes = ["get", "set", "create", "delete", "update", "check", "format", "merge"]
    return f"{prefixes[idx % len(prefixes)]}_{pkg}_{idx}"


def generate() -> None:
    r = rng()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Collect all function/method qualnames for cross-file call generation.
    all_callables: list[tuple[str, str]] = []  # (module_path, qualname)

    # First pass: collect structure.
    structure: list[tuple[str, str, list[str], list[str]]] = []
    # (pkg, module, class_qualnames, func_qualnames)

    packages = PACKAGE_NAMES[:NUM_PACKAGES]
    adjectives = (MODULE_ADJECTIVES * 4)[:FILES_PER_PACKAGE]

    for pkg in packages:
        for adj in adjectives:
            module_path = f"{pkg}/{adj}.py"
            class_qnames = [
                make_class_name(pkg, adj, i) for i in range(CLASSES_PER_FILE)
            ]
            func_qnames = [
                make_function_name(pkg, i) for i in range(FUNCTIONS_PER_FILE)
            ]
            for cq in class_qnames:
                for mi in range(METHODS_PER_CLASS):
                    all_callables.append((module_path, f"{cq}.{make_method_name(mi)}"))
            for fq in func_qnames:
                all_callables.append((module_path, fq))
            structure.append((pkg, adj, class_qnames, func_qnames))

    # Second pass: write files.
    for pkg, adj, class_qnames, func_qnames in structure:
        pkg_dir = OUT_DIR / pkg
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "__init__.py").touch()

        module_path = f"{pkg}/{adj}.py"
        lines: list[str] = [
            '"""Auto-generated stress fixture — do not edit by hand."""',
            "from __future__ import annotations",
            "",
        ]

        # Collect in-file callables for cross-call targets.
        in_file: list[str] = []

        for cls_idx, cq in enumerate(class_qnames):
            decorator = r.choice(DECORATOR_POOL)
            base = "Exception" if r.random() < 0.05 else ""
            lines.append(f"class {cq}({base}):")
            for mi in range(METHODS_PER_CLASS):
                mname = make_method_name(mi)
                in_file.append(f"{cq}.{mname}")
                indent = "    "
                lines.append(f"{indent}def {mname}(self):")
                # Optionally call another in-file callable.
                if in_file and r.random() < IN_FILE_RATIO:
                    callee = r.choice(in_file)
                    # Emit a fake call comment (parser will see it as a def body).
                    lines.append(f"{indent}    _ = None  # would call {callee}")
                else:
                    lines.append(f"{indent}    pass")
                lines.append("")
            lines.append("")

        for fq in func_qnames:
            in_file.append(fq)
            lines.append(f"def {fq}():")
            if in_file and r.random() < IN_FILE_RATIO:
                callee = r.choice(in_file)
                lines.append(f"    _ = None  # would call {callee}")
            else:
                lines.append("    pass")
            lines.append("")

        (pkg_dir / f"{adj}.py").write_text("\n".join(lines) + "\n")

    # Write top-level __init__.py imports.
    for pkg in packages:
        pkg_dir = OUT_DIR / pkg
        init_lines = [f'"""Package: {pkg}."""', ""]
        for adj in adjectives:
            init_lines.append(f"from . import {adj}  # noqa: F401")
        (pkg_dir / "__init__.py").write_text("\n".join(init_lines) + "\n")

    total_files = len(packages) * len(adjectives)
    estimated_nodes = total_files * (
        1  # file node
        + CLASSES_PER_FILE  # class nodes
        + CLASSES_PER_FILE * METHODS_PER_CLASS  # method nodes
        + FUNCTIONS_PER_FILE  # function nodes
    )
    print(
        f"Generated {total_files} files → estimated ~{estimated_nodes} AST nodes "
        f"in {OUT_DIR}"
    )


if __name__ == "__main__":
    generate()
