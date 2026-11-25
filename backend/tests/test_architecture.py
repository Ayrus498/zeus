"""The shape of the backend is checked, so the organisation cannot silently erode: who may import whom, connectors stay independent, every router is mounted,
and tests live in the folder of the package they test. See docs/STRUCTURE.md for the reasoning; this file is the rule."""
import ast
from pathlib import Path

import pytest

from .paths import BACKEND, TESTS

APP = BACKEND / "app"

# package -> the packages it may import. Everything else is a violation. A new package must be added here, on purpose.
# (core is the base; leaf packages depend on it only; toolchain and workspace build on it; agents orchestrate; api wires everything; cli talks to the backend over HTTP.)
ALLOWED = {
    "core": set(),
    "system": {"core"},
    "documents": {"core"},
    "memory": {"core"},
    "maintenance": {"core"},
    "modelhub": {"core"},
    "modelconnectors": {"core"},
    "toolchain": {"core", "memory"},
    "workspace": {"core", "toolchain"},
    "agents": {"core", "memory", "modelconnectors", "system", "toolchain"},                  # Auto mode picks models that fit the hardware
    "mcp": {"core", "toolchain"},
    "api": {"agents", "core", "documents", "maintenance", "memory", "modelconnectors", "modelhub", "system", "toolchain", "workspace"},
    "cli": {"core"},
    "(root)": {"api"},                                       # app/__main__.py starts the server
}
TEST_FOLDERS_WITHOUT_A_PACKAGE = {"fixtures", "packaging"}   # packaging: the install/removal scripts and the version tooling, which are not part of `app`


def modules():
    for f in sorted(APP.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        parts = list(f.relative_to(BACKEND).with_suffix("").parts)
        is_pkg = parts[-1] == "__init__"
        if is_pkg:
            parts = parts[:-1]
        yield f, parts, (parts if is_pkg else parts[:-1])


def imports_of(f: Path, pkg: list[str]):
    """(target module, line) for every import of app code, resolved to an absolute dotted name."""
    for n in ast.walk(ast.parse(f.read_text())):
        if isinstance(n, ast.ImportFrom):
            if n.level:
                base = pkg[: len(pkg) - (n.level - 1)]
                tgt = base + (n.module.split(".") if n.module else [])
            else:
                tgt = (n.module or "").split(".")
            if tgt and tgt[0] == "app":
                if len(tgt) == 1:                                           # `from app import x` / `from .. import x`: x is a package or module
                    for a in n.names:
                        yield "app." + a.name, n.lineno
                else:
                    yield ".".join(tgt), n.lineno
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] == "app":
                    yield a.name, n.lineno


def package_of(mod: str) -> str:
    parts = mod.split(".")
    return parts[1] if len(parts) > 2 or (len(parts) == 2 and (APP / parts[1]).is_dir()) else "(root)"


def graph():
    edges, where = {}, {}
    for f, parts, pkg in modules():
        me = parts[1] if len(parts) > 2 else "(root)"
        for tgt, line in imports_of(f, pkg):
            to = package_of(tgt)
            if to != me:
                edges.setdefault(me, set()).add(to)
                where.setdefault((me, to), f"{f.relative_to(BACKEND)}:{line}")
    return edges, where


def test_every_package_declares_its_place_in_the_layering():
    on_disk = {p.name for p in APP.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert on_disk == set(ALLOWED) - {"(root)"}, f"packages on disk and in ALLOWED differ: {sorted(on_disk ^ (set(ALLOWED) - {'(root)'}))}"
    for p in on_disk:
        assert (APP / p / "__init__.py").exists(), f"{p} is not a package (no __init__.py)"


def test_packages_only_import_what_the_layering_allows():
    edges, where = graph()
    bad = [f"{a} -> {b}   ({where[(a, b)]})" for a, targets in edges.items() for b in sorted(targets) if b not in ALLOWED.get(a, set())]
    assert not bad, "forbidden imports (see docs/STRUCTURE.md):\n  " + "\n  ".join(bad)


def test_there_are_no_dependency_cycles_between_packages():
    edges, _ = graph()
    state: dict[str, int] = {}

    def visit(n, trail):
        state[n] = 1
        for m in sorted(edges.get(n, ())):
            if state.get(m) == 1:
                raise AssertionError("cycle: " + " -> ".join(trail + [n, m]))
            if m not in state:
                visit(m, trail + [n])
        state[n] = 2
    for n in sorted(edges):
        if n not in state:
            visit(n, [])


def test_connectors_do_not_import_each_other():
    """A connector is self-contained: shared code lives in modelconnectors/base.py or core/. (registry, downloads and modelinfo sit above the connectors.)"""
    conn = APP / "modelconnectors"
    for engine in ("ollama", "llamacpp"):
        other = "llamacpp" if engine == "ollama" else "ollama"
        for f in sorted((conn / engine).rglob("*.py")):
            pkg = list(f.relative_to(BACKEND).with_suffix("").parts)[:-1]
            for tgt, line in imports_of(f, pkg):
                assert not tgt.startswith(f"app.modelconnectors.{other}"), f"{f.relative_to(BACKEND)}:{line} imports the {other} connector"
    base_imports = [t for f in [conn / "base.py"] for t, _ in imports_of(f, ["app", "modelconnectors"])]
    assert base_imports == [], "base.py is the shared contract and must not import app code"


def test_every_router_is_mounted_and_has_the_standard_shape():
    from app.api import main
    on_disk = sorted(p.stem for p in (APP / "api/routes").glob("*.py") if p.stem != "__init__")
    mounted = sorted(m.__name__.rsplit(".", 1)[1] for m in main.ROUTERS)
    assert on_disk == mounted, f"routers on disk and mounted differ: {sorted(set(on_disk) ^ set(mounted))}"
    for m in main.ROUTERS:
        assert callable(getattr(m, "router", None)), f"{m.__name__} has no router(ctx)"


def test_tests_live_in_the_folder_of_the_package_they_test():
    folders = {p.name for p in TESTS.iterdir() if p.is_dir() and p.name != "__pycache__"}
    packages = {p.name for p in APP.iterdir() if p.is_dir() and p.name != "__pycache__"}
    stray = sorted(folders - packages - TEST_FOLDERS_WITHOUT_A_PACKAGE)
    assert not stray, f"test folders that match no package: {stray}"
    loose = sorted(p.name for p in TESTS.glob("test_*.py") if p.name != "test_architecture.py")
    assert not loose, f"put these in the folder of the package they test: {loose}"


def test_the_extension_points_exist():
    assert (APP / "mcp/README.md").exists() and (APP / "modelconnectors/base.py").exists()
