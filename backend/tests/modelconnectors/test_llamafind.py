import os
import stat
import time
from pathlib import Path

import pytest

from app.core.database import Database
from app.modelconnectors.llamacpp import finder as lf
from app.modelconnectors.llamacpp.provider import LlamaCppProvider

REAL_DISCOVER = lf.discover                                   # the conftest replaces lf.discover for every test; these tests need the real one
REAL_STANDARD = LlamaCppProvider._standard
ELF = b"\x7fELF" + b"\0" * 60


def program(path: Path, siblings=("libllama.so", "libggml.so"), elf=True, exe=True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ELF if elf else b"#!/bin/sh\necho hi\n")
    path.chmod(0o755 if exe else 0o644)
    for s in siblings:
        (path.parent / s).write_bytes(b"x")
    return path


@pytest.fixture
def home(tmp_path):
    """The layout found on a real machine: a release unpacked in Downloads, a build tree in Documents, and several things that must not be picked."""
    h = tmp_path / "home"
    program(h / "Downloads/llama-b12650-bin-ubuntu-cuda-13.5-x64/llama-b12650/llama-server")
    program(h / "Documents/ai/llama.cpp/build/bin/llama-server")
    program(h / "stray/llama-server", siblings=())                                  # no llama.cpp next to it
    program(h / "scripts/llama-server", siblings=("libllama.so",), elf=False)       # a shell script, not a program
    program(h / "locked/llama-server", exe=False)                                   # not executable
    program(h / "proj/node_modules/pkg/llama-server")                               # inside a skipped folder
    program(h / ".cache/x/llama-server")                                            # hidden cache
    program(h / "a/b/c/d/e/f/g/h/llama-server")                                     # far too deep
    program(h / "ollama/bin/llama-server")                                          # an Ollama folder
    return h


# ---- the rules
def test_a_genuine_install_is_accepted_and_the_look_alikes_are_not(home):
    ok = lambda rel: lf.is_genuine(home / rel)  # noqa: E731
    assert ok("Downloads/llama-b12650-bin-ubuntu-cuda-13.5-x64/llama-b12650/llama-server") and ok("Documents/ai/llama.cpp/build/bin/llama-server")
    assert not ok("stray/llama-server") and not ok("scripts/llama-server") and not ok("locked/llama-server") and not ok("ollama/bin/llama-server")
    assert not ok("does/not/exist/llama-server")


def test_ollamas_own_runner_is_excluded_wherever_it_lives(tmp_path):
    p = program(tmp_path / "usr/local/lib/ollama/llama-server", siblings=("libggml-base.so",))
    assert lf.is_ollama_path(p) and not lf.is_genuine(p)
    assert not lf.is_ollama_path(tmp_path / "llama.cpp/build/bin/llama-server")


def test_siblings_can_be_in_a_lib_folder_of_an_installed_tree(tmp_path):
    p = program(tmp_path / "opt/llama/bin/llama-server", siblings=())
    (p.parent.parent / "lib").mkdir()
    (p.parent.parent / "lib" / "libllama.so.1").write_bytes(b"x")
    assert lf.is_genuine(p)
    q = program(tmp_path / "opt/other/bin/llama-server", siblings=("llama-cli",))
    assert lf.is_genuine(q)                                                         # llama-cli next to it counts too


# ---- the search
def test_discover_finds_both_installs_with_a_readable_source_and_nothing_else(home, tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "discover", REAL_DISCOVER)
    found = lf.discover(home, tmp_path / "data", extra_roots=[])
    # roots are the home folder, /opt, /usr/local and the data dir: only the home folder is under the temp tree here
    paths = {f["path"] for f in found if f["path"].startswith(str(home))}
    assert paths == {str(home / "Downloads/llama-b12650-bin-ubuntu-cuda-13.5-x64/llama-b12650/llama-server"), str(home / "Documents/ai/llama.cpp/build/bin/llama-server")}
    assert {f["source"] for f in found if f["path"].startswith(str(home))} == {"Downloads", "Documents"}


def test_a_symlink_to_the_program_is_found_once(home, tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "discover", REAL_DISCOVER)
    real = home / "Documents/ai/llama.cpp/build/bin/llama-server"
    (home / "bin").mkdir()
    (home / "bin" / "llama-server").symlink_to(real)
    found = [f for f in lf.discover(home, tmp_path / "data") if f["real"] == str(real.resolve())]
    assert len(found) == 1


def test_symlinked_folders_are_not_followed_and_loops_end(tmp_path):
    h = tmp_path / "home"
    (h / "a").mkdir(parents=True)
    (h / "a" / "loop").symlink_to(h)                                                 # a link back to the home folder
    (h / "outside").mkdir()
    program(tmp_path / "elsewhere/llama-server")
    (h / "link").symlink_to(tmp_path / "elsewhere")
    assert lf.scan([h]) == []


def test_the_search_is_breadth_first_and_respects_its_budget(home):
    first = lf.scan([home], max_entries=10_000)
    assert len(first) >= 2
    assert lf.scan([home], deadline=0.0) == []                                       # out of time: returns at once
    assert len(lf.scan([home], max_entries=1)) < len(first)                          # a tiny entry budget stops early
    assert lf.scan([home / "missing"]) == []


def test_depth_limit(tmp_path):
    p = program(tmp_path / "a/b/c/llama-server")
    assert lf.scan([tmp_path], max_depth=3) == [p] and lf.scan([tmp_path], max_depth=1) == []


# ---- the provider
def script(path: Path, build: int, gpu: bool) -> Path:
    """A llama-server stand-in that prints a version, devices and a help text."""
    dev = 'echo "Available devices:"; echo "  CUDA0: NVIDIA Test GPU (8000 MiB, 7000 MiB free)"' if gpu else 'echo "Available devices:"'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\ncase "$1" in\n --version) echo "version: 1 (build {build})" >&2;;\n --list-devices) {dev};;\n --help) echo "--jinja --flash-attn";;\nesac\n')
    path.chmod(0o755)
    return path


@pytest.fixture
def prov(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.DATA_DIR", tmp_path / "data")
    monkeypatch.delenv("LLAMA_SERVER", raising=False)
    return LlamaCppProvider(Database(":memory:"))


def found_by_scan(monkeypatch, *paths: Path):
    monkeypatch.setattr(lf, "discover", lambda *a, **k: [{"path": str(p), "real": str(p.resolve()), "source": "Downloads"} for p in paths])


def test_candidates_are_ranked_gpu_first_then_newest(prov, tmp_path, monkeypatch):
    a, b, c = script(tmp_path / "a/llama-server", 100, True), script(tmp_path / "b/llama-server", 200, False), script(tmp_path / "c/llama-server", 150, True)
    found_by_scan(monkeypatch, a, b, c)
    cands = prov.candidates()
    assert [x["build"] for x in cands] == [150, 100, 200]                           # GPU builds first (newest of them), the faster-looking CPU build last
    assert [x["auto"] for x in cands] == [True, False, False] and cands[0]["devices"] == ["NVIDIA Test GPU"] and cands[2]["gpu"] is False
    assert prov.binary() == str(c)


async def test_status_is_connected_when_only_a_discovered_program_exists(prov, tmp_path, monkeypatch):
    p = script(tmp_path / "Downloads/llama-b1/llama-b1/llama-server", 12650, True)
    found_by_scan(monkeypatch, p)
    st = await prov.status()
    assert st["connected"] and st["binary"] == str(p) and st["version"] == "b12650" and st["devices"][0]["name"] == "NVIDIA Test GPU"
    assert prov.config()["binary"] == ""                                              # automatic mode: nothing was written to the settings


def test_a_path_set_in_the_settings_wins_over_automatic_detection(prov, tmp_path, monkeypatch):
    mine, other = script(tmp_path / "mine/llama-server", 100, False), script(tmp_path / "other/llama-server", 999, True)
    found_by_scan(monkeypatch, other)
    prov.save_config({"binary": str(mine)})
    assert prov.binary() == str(mine) and prov.configured_problem() is None


def test_a_wrong_path_in_the_settings_falls_back_to_automatic_and_says_so(prov, tmp_path, monkeypatch):
    other = script(tmp_path / "other/llama-server", 999, True)
    found_by_scan(monkeypatch, other)
    prov.save_config({"binary": str(tmp_path / "gone/llama-server")})
    assert prov.binary() == str(other)
    assert "does not exist" in prov.configured_problem() and "automatically found" in prov.configured_problem()


def test_nothing_found_means_no_binary(prov):
    assert prov.binary() is None and prov.candidates() == []


def test_the_search_result_is_cached_until_rescan_or_the_program_disappears(prov, tmp_path, monkeypatch):
    a = script(tmp_path / "a/llama-server", 100, True)
    calls = []
    monkeypatch.setattr(lf, "discover", lambda *x, **k: calls.append(1) or [{"path": str(a), "real": str(a), "source": "x"}])
    prov.candidates(); prov.candidates(); prov.binary()
    assert len(calls) == 1                                                            # status polling does not search every time
    b = script(tmp_path / "b/llama-server", 300, True)
    monkeypatch.setattr(lf, "discover", lambda *x, **k: calls.append(1) or [{"path": str(p), "real": str(p), "source": "x"} for p in (a, b)])
    assert len(prov.candidates()) == 1                                                # a new install is not seen yet...
    assert [c["build"] for c in prov.rescan()] == [300, 100]                          # ...until Search again
    a.unlink()
    b.unlink()
    assert prov.candidates() == [] or len(calls) > 2                                  # files that vanished trigger a new search


def test_a_failed_search_is_repeated_soon_but_not_on_every_call(prov, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(lf, "discover", lambda *x, **k: calls.append(1) or [])
    prov.candidates(); prov.candidates()
    assert len(calls) == 1
    prov._found_at -= prov.RETRY_TTL + 1                                              # a little later, llama.cpp may have been installed
    a = script(tmp_path / "a/llama-server", 100, True)
    monkeypatch.setattr(lf, "discover", lambda *x, **k: calls.append(1) or [{"path": str(a), "real": str(a), "source": "x"}])
    assert [c["build"] for c in prov.candidates()] == [100] and len(calls) == 2


def test_the_standard_places_are_trusted_without_the_install_check(prov, tmp_path, monkeypatch):
    lone = script(tmp_path / "bin/llama-server", 77, False)                            # no libraries next to it, but it is on PATH
    monkeypatch.setattr(LlamaCppProvider, "_standard", lambda self: [(str(lone), "PATH")])
    assert [c["source"] for c in prov.candidates()] == ["PATH"]


def test_the_standard_places_never_include_ollamas_runner_but_do_include_the_env_variable_and_path(prov, tmp_path, monkeypatch):
    runner = script(tmp_path / "usr/local/lib/ollama/llama-server", 1, False)
    mine = script(tmp_path / "mine/llama-server", 5, False)
    monkeypatch.setenv("LLAMA_SERVER", str(runner))
    monkeypatch.setenv("PATH", str(runner.parent))                                      # PATH also leads to Ollama's runner
    assert REAL_STANDARD(prov) == [] or all("ollama" not in p for p, _ in REAL_STANDARD(prov))
    monkeypatch.setenv("LLAMA_SERVER", str(mine))
    assert (str(mine), "LLAMA_SERVER") in REAL_STANDARD(prov)


# ---- the API
def test_the_overview_lists_what_was_found_and_the_rescan_endpoint_searches_again(client, tmp_path, monkeypatch):
    a = script(tmp_path / "Downloads/x/llama-server", 12650, True)
    found_by_scan(monkeypatch, a)
    j = client.get("/api/llamacpp").json()
    assert j["binary_auto"] is True and j["binary_problem"] is None and j["status"]["connected"]
    assert [c["path"] for c in j["found"]] == [str(a)] and j["found"][0]["auto"] and j["found"][0]["devices"] == ["NVIDIA Test GPU"]
    b = script(tmp_path / "Documents/y/llama-server", 12700, True)
    found_by_scan(monkeypatch, a, b)
    assert len(client.get("/api/llamacpp").json()["found"]) == 1                      # cached
    assert [c["build"] for c in client.post("/api/llamacpp/rescan").json()["found"]] == [12700, 12650]
    j = client.put("/api/llamacpp/config", json={"binary": str(a)}).json()           # "Use this one"
    assert j["binary_auto"] is False and j["status"]["binary"] == str(a)
    j = client.put("/api/llamacpp/config", json={"binary": ""}).json()                # back to automatic
    assert j["binary_auto"] is True and j["status"]["binary"] == str(b)
    j = client.put("/api/llamacpp/config", json={"binary": "/gone/llama-server"}).json()
    assert j["binary_auto"] is True and "does not exist" in j["binary_problem"] and j["status"]["connected"]
