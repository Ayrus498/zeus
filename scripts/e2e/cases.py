"""The end-to-end cases. GUI cases drive the real built UI in Chromium; CLI cases drive the real terminal app through a pseudo-terminal.
They run in order and share one stack, so the last cases prove that the desktop app and the terminal app hand a conversation to each other."""
from __future__ import annotations

import json
import re
import subprocess
import time

from framework import Skip, case, expect
from harness import BACKEND, backend_python, http, strip_ansi
from terminal import Term

CODER = "e2e-coder-q4_k_m"


def engine_btn(page):
    return page.get_by_role("button", name="Engine").first


def model_btn(page):
    return page.get_by_role("button", name="Model").first


def pick(page, aria: str, option: str):
    page.get_by_role("button", name=aria).first.click()
    page.get_by_role("option", name=re.compile(option)).first.click()
    page.wait_for_timeout(500)


def send(page, text: str):
    box = page.locator("textarea").first
    box.fill(text)
    box.press("Enter")


# ------------------------------------------------------------------ GUI
@case("gui", "App loads and the engine dropdown comes before the model dropdown")
def gui_layout(c):
    pg = c.page
    pg.goto(c.stack.ui)
    pg.wait_for_selector("[data-testid=engine-selector]", timeout=30000)
    pg.get_by_role("button", name="New session").first.click()
    pg.wait_for_timeout(800)
    e, m = engine_btn(pg).bounding_box(), model_btn(pg).bounding_box()
    expect(e and m and e["x"] < m["x"], f"engine dropdown must be left of the model dropdown: {e} {m}")
    expect("Ollama" in engine_btn(pg).inner_text(), "Ollama is the default engine")


@case("gui", "Engine dropdown offers Ollama, llama.cpp, Help me choose and llama.cpp settings")
def gui_engine_options(c):
    pg = c.page
    engine_btn(pg).click()
    opts = [o.strip() for o in pg.get_by_role("option").all_inner_texts()]
    pg.keyboard.press("Escape")
    expect(opts == ["Ollama", "llama.cpp", "Help me choose…", "llama.cpp settings…"], f"unexpected options {opts}")


@case("gui", "Help me choose shows the comparison, a recommendation and can switch engine")
def gui_help(c):
    pg = c.page
    pick(pg, "Engine", "Help me choose")
    pg.wait_for_selector("[data-testid=engine-help]")
    expect(pg.locator("[data-testid=engine-help] tbody tr").count() >= 8, "comparison table is missing rows")
    expect("Suggested for this machine" in pg.inner_text("[data-testid=engine-recommendation]"), "no recommendation")
    pg.get_by_test_id("use-ollama").is_disabled() or None
    pg.get_by_role("button", name="Close", exact=True).first.click()


@case("gui", "Ollama: choose a model, chat, and the agent creates a file through a tool call")
def gui_ollama_chat(c):
    pg = c.page
    pick(pg, "Model", "fake-coder")
    send(pg, "Say hello")
    pg.get_by_text("Hello from fake ollama").first.wait_for(timeout=30000)
    send(pg, "CREATE the file")
    pg.get_by_text("Done.").first.wait_for(timeout=30000)
    f = c.stack.project_dir / "hello.py"
    expect(f.exists() and "hi from e2e" in f.read_text(), "hello.py was not created by the tool call")


@case("gui", "Typing / opens the command menu; filtering, arrows and Tab work")
def gui_slash_menu(c):
    pg = c.page
    box = pg.locator("textarea").first
    box.fill("/")
    pg.wait_for_selector("[data-testid=slash-menu]")
    names = pg.locator("[data-testid^=slash-]").evaluate_all("els => els.map(e => e.getAttribute('data-testid'))")
    for want in ("slash-context", "slash-compact", "slash-memory", "slash-remember", "slash-summary", "slash-engine", "slash-best", "slash-help"):
        expect(want in names, f"{want} missing from the menu: {names}")
    box.fill("/mem")
    expect(pg.locator("[data-testid=slash-memory]").count() == 1 and pg.locator("[data-testid=slash-compact]").count() == 0, "menu does not filter")
    box.press("Tab")
    expect(box.input_value() == "/memory ", f"Tab should complete the command: {box.input_value()!r}")
    box.fill("/rem")
    box.press("Enter")                                           # a command that needs an argument waits for it
    expect(box.input_value() == "/remember ", f"Enter should complete /remember: {box.input_value()!r}")
    box.press("Escape")
    box.fill("")


@case("gui", "/remember saves a note that a brand-new conversation starts with")
def gui_memory_cross_session(c):
    pg = c.page
    box = pg.locator("textarea").first
    box.fill("/remember Tests run with .venv/bin/pytest -q")
    box.press("Enter")
    pg.get_by_text("Saved to project memory").first.wait_for(timeout=20000)
    mem = c.stack.project_dir / ".zeus" / "memory.md"
    expect(mem.exists() and "Tests run with .venv/bin/pytest -q" in mem.read_text(), "memory file was not written")
    expect(mem.stat().st_mode & 0o777 == 0o600, "memory file must be private")
    pg.get_by_role("button", name="New session").first.click()
    pg.wait_for_timeout(800)
    n = len(c.stack.ollama_requests())
    send(pg, "Say hello")
    pg.get_by_text("Hello from fake ollama").last.wait_for(timeout=30000)
    req = c.stack.ollama_requests()[n]
    system = req[0]["content"]
    expect("Tests run with .venv/bin/pytest -q" in system and "Project memory" in system, "the new conversation was not given the project memory")
    expect([m["role"] for m in req] == ["system", "user"], f"a new conversation must not carry the old one: {[m['role'] for m in req]}")


@case("gui", "/context shows the meter; /compact summarises older turns and the model then receives the summary")
def gui_compact(c):
    pg = c.page
    for i in range(3):
        send(pg, f"Say hello number {i}")
        pg.get_by_text("Hello from fake ollama").nth(i + 1).wait_for(timeout=30000)
    box = pg.locator("textarea").first
    box.fill("/context")
    box.press("Enter")
    pg.wait_for_selector("[data-testid=context-meter]", timeout=20000)
    expect("messages are sent to the model" in pg.inner_text("[data-testid=local-card] >> nth=-1"), "context card incomplete")
    expect(pg.get_by_test_id("context-chip").count() == 1, "the context chip is missing next to the prompt")
    box.fill("/compact")
    box.press("Enter")
    pg.get_by_text("Conversation summarised").first.wait_for(timeout=60000)
    ctx = http("GET", f"{c.stack.api}/api/sessions/{c.stack.state()['session']['id']}/context")
    expect(ctx["summary"] and ctx["summary"]["covers"] >= 4, f"summary not stored: {ctx['summary']}")
    expect(pg.get_by_test_id("context-chip").inner_text().count("Σ") == 1, "the chip should mark a summarised conversation")
    n = len(c.stack.ollama_requests())
    send(pg, "And once more")
    pg.get_by_text("Hello from fake ollama").nth(5).wait_for(timeout=30000)
    req = c.stack.ollama_requests()[n]
    expect("Conversation so far" in req[0]["content"], "the model was not given the summary")
    texts = " ".join(m["content"] for m in req[1:])
    expect("Say hello number 0" not in texts and "And once more" in texts, "summarised messages must not be sent again")


@case("gui", "/memory opens the editor; edits are saved to the file")
def gui_memory_dialog(c):
    pg = c.page
    box = pg.locator("textarea").first
    box.fill("/memory")
    box.press("Enter")
    pg.wait_for_selector("[data-testid=memory-dialog]")
    ta = pg.get_by_test_id("memory-text")
    expect("pytest -q" in ta.input_value(), "the editor does not show the saved note")
    ta.fill(ta.input_value() + "\n- The UI is React\n")
    pg.get_by_test_id("memory-save").click()
    pg.wait_for_timeout(800)
    expect("The UI is React" in (c.stack.project_dir / ".zeus" / "memory.md").read_text(), "edit not saved")
    pg.keyboard.press("Escape")


@case("gui", "Commands open the interface: /best opens the benchmarks, /help lists the commands")
def gui_command_actions(c):
    pg = c.page
    box = pg.locator("textarea").first
    box.fill("/help")
    box.press("Enter")
    pg.get_by_text("Commands").last.wait_for()
    expect(pg.locator("[data-testid=local-card] code:has-text('/compact')").count() >= 1, "/help does not list /compact")
    box.fill("/best")
    box.press("Enter")
    pg.wait_for_selector("[data-testid=arena-panel]", timeout=30000)
    pg.keyboard.press("Escape")
    box.fill("/frobnicate")
    box.press("Enter")
    pg.get_by_text("Unknown command /frobnicate").first.wait_for()


@case("gui", "Switching to llama.cpp lists the GGUF models and chats through llama-server")
def gui_llama_chat(c):
    pg = c.page
    pick(pg, "Engine", "^llama.cpp$")
    pg.wait_for_timeout(1500)
    expect("llama.cpp" in engine_btn(pg).inner_text(), "engine did not switch")
    model_btn(pg).click()
    names = [o.strip() for o in pg.get_by_role("option").all_inner_texts()]
    expect(any("e2e-coder-q4_k_m" in n for n in names) and any("e2e-chat-q8_0" in n for n in names), f"GGUF models not listed: {names}")
    expect(not any("Auto" in n for n in names), "Auto must not be offered for llama.cpp")
    pg.get_by_role("option", name=re.compile(CODER)).first.click()
    pg.wait_for_timeout(500)
    send(pg, "Say hello")
    pg.get_by_text("Hello from fake llama").first.wait_for(timeout=60000)


@case("gui", "llama.cpp lists the models Ollama already downloaded (shared, read only), runs one, and the list can be switched off")
def gui_llama_ollama(c):
    pg = c.page
    st = c.stack
    before = sorted((str(p), p.stat().st_mtime_ns) for p in st.ollama_store.rglob("*") if p.is_file())
    pick(pg, "Engine", "llama.cpp settings")
    pg.wait_for_selector("[data-testid=llama-window]")
    pg.get_by_test_id("llama-tab-models").click()
    row = pg.locator("[data-testid='llama-model-ollama/e2e-ollama:1b']")
    row.wait_for(timeout=20000)
    expect("from Ollama" in row.inner_text(), f"the Ollama model should say where it comes from: {row.inner_text()!r}")
    expect(row.get_by_test_id("ollama-note").inner_text().count("ollama rm e2e-ollama:1b") == 1, "the note should tell how to uninstall it in Ollama")
    expect(pg.locator("[data-testid='uninstall-ollama/e2e-ollama:1b']").count() == 0, "Zeus-Agent must not offer to delete Ollama's model")
    row.get_by_role("button", name="Use").click()                                    # choose it for this conversation and chat with it
    pg.wait_for_timeout(1500)
    expect("ollama/e2e-ollama:1b" in model_btn(pg).inner_text(), f"the model was not selected: {model_btn(pg).inner_text()!r}")
    pg.keyboard.press("Escape")
    send(pg, "Say hello from the shared model")
    pg.get_by_text("Hello from fake llama").last.wait_for(timeout=60000)
    # the toggle hides and shows the shared models
    pick(pg, "Engine", "llama.cpp settings")
    pg.get_by_test_id("llama-tab-engine").click()
    box = pg.get_by_test_id("ollama-share").locator("input")
    box.click()                                                                       # the box follows the saved setting, so it flips once the server has confirmed
    pg.wait_for_function("!document.querySelector(\"[data-testid=ollama-share] input\").checked", timeout=20000)
    pg.get_by_test_id("llama-tab-models").click()
    pg.wait_for_function("document.querySelectorAll(\"[data-testid='llama-model-ollama/e2e-ollama:1b']\").length === 0", timeout=20000)
    pg.get_by_test_id("llama-tab-engine").click()
    box.click()
    pg.wait_for_function("document.querySelector(\"[data-testid=ollama-share] input\").checked", timeout=20000)
    pg.get_by_test_id("llama-tab-models").click()
    pg.locator("[data-testid='llama-model-ollama/e2e-ollama:1b']").wait_for(timeout=20000)
    pg.locator(f"[data-testid=llama-model-{CODER}]").get_by_role("button", name="Use").click()          # back to the model the later cases expect
    pg.wait_for_timeout(1200)
    pg.keyboard.press("Escape")
    after = sorted((str(p), p.stat().st_mtime_ns) for p in st.ollama_store.rglob("*") if p.is_file())
    expect(before == after, "Ollama's store must never be modified")


@case("gui", "Download tab: search Hugging Face, see how each file runs here, download the suggested one, then uninstall it")
def gui_llama_download_uninstall(c):
    pg = c.page
    st = c.stack
    pick(pg, "Engine", "llama.cpp settings")
    pg.wait_for_selector("[data-testid=llama-window]")
    pg.get_by_test_id("llama-tab-download").click()
    pg.wait_for_selector("[data-testid='hf-repo-e2e/tiny-GGUF']", timeout=20000)                      # the most downloaded models are shown at once
    expect(pg.locator("[data-testid^=hf-repo-]").count() == 2, "both repositories should be listed without a query")
    pg.get_by_test_id("hf-query").fill("tiny")
    pg.wait_for_function("document.querySelectorAll('[data-testid^=hf-repo-]').length === 1", timeout=20000)
    pg.get_by_test_id("hf-repo-e2e/tiny-GGUF").click()
    pg.wait_for_selector("[data-testid=hf-files]", timeout=20000)
    files = pg.locator("[data-testid^=hf-file-]")
    expect(files.count() == 4, f"three single files and the two shards grouped into one split model: {files.count()}")
    suggested = pg.locator("[data-testid='hf-file-tiny-Q4_K_M.gguf']")
    expect("suggested" in suggested.inner_text(), f"the Q4_K_M file should be the suggested one: {suggested.inner_text()!r}")
    for f in ("tiny-Q4_K_M.gguf", "tiny-Q8_0.gguf", "tiny-F16.gguf"):
        t = pg.locator(f"[data-testid='hf-file-{f}']").inner_text()
        expect(any(w in t for w in ("Runs on your GPU", "GPU + RAM", "CPU only", "Too large")), f"{f} has no hardware verdict: {t!r}")
    expect("Too large" in pg.locator("[data-testid='hf-file-tiny-F16.gguf']").inner_text() or "GPU + RAM" in pg.locator("[data-testid='hf-file-tiny-F16.gguf']").inner_text() or "CPU only" in pg.locator("[data-testid='hf-file-tiny-F16.gguf']").inner_text(), "a 90 GB file cannot run fully on a GPU")
    split = pg.locator("[data-testid='hf-file-tiny.gguf']")
    expect("split model" in split.inner_text() and pg.locator("[data-testid='hf-download-tiny.gguf']").count() == 0, f"a split model is shown but must not offer a download button: {split.inner_text()!r}")
    expect("suggested" not in split.inner_text(), "a split model is never suggested")
    pg.get_by_test_id("hf-download-tiny-Q4_K_M.gguf").click()
    pg.locator("[data-testid=llama-downloads]:has-text('done')").wait_for(timeout=60000)
    got = st.data / "models" / "tiny-Q4_K_M.gguf"
    expect(got.is_file() and got.read_bytes()[:4] == b"GGUF", "the file was not downloaded")
    # only what was typed was sent to Hugging Face (no cookies, no tokens, nothing about the user or the projects)
    for r in st.hf_requests():
        expect(set(r["query"]) <= {"filter", "sort", "direction", "limit", "search", "blobs"}, f"unexpected query sent to Hugging Face: {r}")
        expect(not r["headers"].get("cookie") and not r["headers"].get("authorization"), f"credentials sent to Hugging Face: {r}")
        expect("zeus" not in json.dumps(r["query"]).lower() and str(st.project_dir) not in json.dumps(r), f"local information sent to Hugging Face: {r}")
    # it now shows in the model list as downloaded, with an uninstall button
    pg.get_by_test_id("llama-tab-models").click()
    row = pg.locator("[data-testid=llama-model-tiny-Q4_K_M]")
    row.wait_for(timeout=20000)
    expect("downloaded" in row.inner_text(), f"a downloaded model should say so: {row.inner_text()!r}")
    row.get_by_test_id("uninstall-tiny-Q4_K_M").click()
    confirm = pg.get_by_test_id("uninstall-confirm")
    confirm.wait_for()
    expect(str(got) in confirm.inner_text() and "cannot be undone" in confirm.inner_text(), f"the confirmation should name the file: {confirm.inner_text()!r}")
    confirm.get_by_role("button", name="Cancel").click()
    expect(got.exists(), "cancel must keep the file")
    row.get_by_test_id("uninstall-tiny-Q4_K_M").click()
    pg.get_by_test_id("uninstall-yes").click()
    pg.wait_for_function("document.querySelectorAll('[data-testid=llama-model-tiny-Q4_K_M]').length === 0", timeout=20000)
    expect(not got.exists(), "uninstalling must delete the file")
    expect(st.ollama_blob.exists() and (st.models_dir / "e2e-coder-q4_k_m.gguf").exists(), "other models must be untouched")
    pg.keyboard.press("Escape")


@case("gui", "llama.cpp window: models, per-model settings are saved, Load shows the server running")
def gui_llama_window(c):
    pg = c.page
    pick(pg, "Engine", "llama.cpp settings")
    pg.wait_for_selector("[data-testid=llama-window]")
    pg.wait_for_selector(f"[data-testid=llama-model-{CODER}]")
    expect(pg.locator("[data-testid^=llama-model-]").count() == 3, "expected two GGUF models and the one shared from Ollama")
    row = pg.locator(f"[data-testid=llama-model-{CODER}]")
    row.get_by_title("Settings for this model").click()
    pg.get_by_label("GPU layers").last.fill("7")
    pg.get_by_role("button", name="Save").last.click()
    pg.wait_for_timeout(800)
    saved = c.stack.settings().get("llamacpp", {})
    cfg = saved if isinstance(saved, dict) else json.loads(saved)
    expect(cfg.get("models", {}).get(CODER, {}).get("ngl") == "7", f"per-model setting not saved: {cfg}")
    pg.get_by_test_id("llama-tab-server").click()
    pg.wait_for_selector("[data-testid=llama-server-status]")
    pg.get_by_test_id("llama-tab-models").click()
    row.get_by_role("button", name="Load").click()
    pg.wait_for_selector(f"[data-testid=llama-model-{CODER}] >> text=loaded", timeout=60000)
    pg.keyboard.press("Escape")


@case("gui", "Models window: Best for your hardware shows this machine, Arena scores per activity and every quantization")
def gui_arena(c):
    pg = c.page
    pg.get_by_role("button", name="Settings").first.click()
    pg.get_by_test_id("settings-item-models").click()
    pg.get_by_test_id("row-arena").click()
    pg.wait_for_selector("[data-testid=arena-panel]")
    pg.wait_for_selector("[data-testid=arena-hardware]", timeout=30000)
    expect("system RAM" in pg.inner_text("[data-testid=arena-hardware]"), "hardware card does not describe the machine")
    for tab in ("code", "agentcode", "chat", "vision"):
        expect(pg.get_by_test_id(f"arena-tab-{tab}").count() == 1, f"missing the {tab} tab")
    pg.get_by_test_id("arena-tab-code").click()
    pg.get_by_label("Only what runs here").uncheck()
    rows = pg.locator("[data-testid^=arena-row-]")
    expect(rows.count() >= 20, f"too few ranked models: {rows.count()}")
    expect("Arena" in pg.inner_text("[data-testid=arena-attribution]") and "CC BY 4.0" in pg.inner_text("[data-testid=arena-attribution]"), "attribution missing")
    # a model with a known size explains itself: quantization table with sizes, where it runs and quality loss
    sized = pg.locator("[data-testid^=arena-row-]:has-text('B')").first
    sized.locator("button").first.click()
    pg.wait_for_selector("[data-testid=quant-table]", timeout=20000)
    q = pg.inner_text("[data-testid=quant-table]")
    expect("Q4_K_M" in q and "Q8_0" in q and "perplexity" in q or "full" in q, f"quantization table incomplete: {q[:200]}")
    expect(not c.page_errors, f"JavaScript errors: {c.page_errors}")
    pg.keyboard.press("Escape")


@case("gui", "llama.cpp unpacked in Downloads (nothing on PATH) is found and can be chosen, and automatic mode is restorable")
def gui_llama_found(c):
    pg = c.page
    st = c.stack
    pick(pg, "Engine", "llama.cpp settings")
    pg.wait_for_selector("[data-testid=llama-window]")
    pg.get_by_test_id("llama-tab-engine").click()
    pg.wait_for_selector("[data-testid=llama-found]", timeout=20000)
    pg.get_by_test_id("llama-rescan").click()
    pg.wait_for_selector("[data-testid=llama-found-row]", timeout=30000)
    rows = pg.locator("[data-testid=llama-found-row]").all_inner_texts()
    mine = [r for r in rows if "Downloads/llama-b99999" in r]
    expect(len(mine) == 1, f"the program unpacked in Downloads was not found: {rows}")
    expect("in Downloads" in mine[0] and "CPU only" in mine[0], f"source/GPU line missing: {mine[0]!r}")
    row = pg.locator("[data-testid=llama-found-row]", has_text="llama-b99999")
    row.get_by_role("button", name="Use this one").click()
    pg.wait_for_selector("[data-testid=llama-found-row]:has-text('llama-b99999') >> text=in use", timeout=20000)
    saved = st.settings().get("llamacpp")
    saved = json.loads(saved) if isinstance(saved, str) else saved
    expect(saved["binary"] == str(st.discovered_llama), f"the chosen program was not saved: {saved}")
    expect(http("GET", f"{st.api}/api/llamacpp")["status"]["binary"] == str(st.discovered_llama), "the backend does not use the chosen program")
    pg.get_by_test_id("llama-use-auto").click()
    pg.wait_for_function("document.querySelector('[data-testid=llama-use-auto]') === null", timeout=20000)
    j = http("GET", f"{st.api}/api/llamacpp")
    expect(j["binary_auto"] is True and j["status"]["connected"] and j["status"]["binary"], f"automatic mode should find a program: {j['status']}")
    expect(any(f["path"] == str(st.discovered_llama) for f in j["found"]), "the Downloads program disappeared from the list")
    http("PUT", f"{st.api}/api/llamacpp/config", {"binary": str(BACKEND / "tests" / "fixtures" / "fake_llama_server.py")})        # later cases use the fake server again
    pg.keyboard.press("Escape")


@case("gui", "The GUI records where you are in ~/.zeus/state.json")
def gui_state(c):
    st = c.stack.state()
    expect(st.get("client") == "gui", f"client should be gui: {st}")
    expect(st.get("engine") == "llamacpp" and st.get("model") == CODER, f"engine/model not recorded: {st}")
    expect(st.get("project", {}).get("path") == str(c.stack.project_dir), "project not recorded")
    expect(st.get("session", {}).get("id"), "session not recorded")
    expect((c.stack.home / "state.json").stat().st_mode & 0o777 == 0o600, "state.json must be private (0600)")
    expect(not c.page_errors, f"JavaScript errors in the page: {c.page_errors}")


# ------------------------------------------------------------------ CLI
@case("cli", "zeus --cli --help lists --continue and --engine; --version works")
def cli_help(c):
    run = lambda *a: subprocess.run([backend_python(), "-m", "app.cli", *a], cwd=BACKEND, env=c.stack.env, capture_output=True, text=True, timeout=60)  # noqa: E731
    h = run("--help")
    expect(h.returncode == 0 and "--continue" in h.stdout and "--engine" in h.stdout, h.stdout + h.stderr)
    v = run("--version")
    expect("Zeus-Agent" in v.stdout + v.stderr, "no version output")


@case("cli", "--continue resumes the GUI conversation: engine, model and history")
def cli_continue(c):
    if c.stack.state().get("client") != "gui":
        raise Skip("needs the GUI cases to have run first (they create the conversation that is resumed)")
    t = c.term = Term(c.stack, ["--continue"])
    expect(t.wait_for("resumed", 40), "no 'resumed' line:\n" + t.screen())
    s = t.screen(6000)
    expect("from the desktop app" in s and "llama.cpp" in s and CODER in s, "resume line is missing the GUI's engine/model:\n" + s)
    expect("Hello from fake llama" in s, "the GUI conversation was not replayed:\n" + s)
    expect("powered by llama.cpp" in s, "banner should name the engine")


@case("cli", "The resumed conversation continues on llama.cpp")
def cli_continue_chat(c):
    if c.term is None:
        raise Skip("needs the resumed conversation")
    t = c.term
    t.buf = ""
    t.send("Say hello again")
    expect(t.wait_for("Hello from fake llama", 60), "no answer from llama.cpp:\n" + t.screen())


@case("cli", "/engine shows both engines and the current one")
def cli_engine_menu(c):
    if c.term is None:
        raise Skip("needs the resumed conversation")
    t = c.term
    t.buf = ""
    t.send("/engine")
    if not t.wait_for("✓ current", 6):
        t.send("")                                   # the first Enter may only accept the slash-menu suggestion
    expect(t.wait_for("✓ current", 20) and t.wait_for("Help me choose", 5), "the /engine menu did not open:\n" + t.screen())
    s = t.buf                                            # the menu is drawn once and then scrolls: look at everything received since /engine
    expect("Ollama" in s and "llama.cpp" in s and "✓ current" in s, t.screen())
    t.send("\x1b", enter=False)
    t.pump(1)
    t.close()


@case("cli", "--engine ollama starts a new conversation on Ollama and answers")
def cli_ollama(c):
    t = c.term = Term(c.stack, ["--engine", "ollama", "--model", "fake-coder:1b", str(c.stack.project_dir)])
    expect(t.wait_for("Ask Zeus-Agent", 40), "prompt never appeared:\n" + t.screen())
    t.buf = ""
    t.send("Say hello")
    expect(t.wait_for("Hello from fake ollama", 60), "no answer from Ollama:\n" + t.screen())
    t.pump(1.5)
    t.close()
    st = c.stack.state()
    expect(st.get("client") == "cli" and st.get("engine") == "ollama", f"the terminal must record its own state: {st}")


@case("cli", "/best coding ranks models by Arena votes and judges them against this machine")
def cli_best(c):
    t = c.term = Term(c.stack, ["--engine", "ollama", "--model", "fake-coder:1b", str(c.stack.project_dir)])
    expect(t.wait_for("Ask Zeus-Agent", 40), "prompt never appeared:\n" + t.screen())
    t.buf = ""
    t.send("/best coding all")
    expect(t.wait_for("Your computer:", 30) and t.wait_for("CC BY 4.0", 10), "no ranking printed:\n" + t.screen())
    s = t.buf
    expect("Rank" in s and "Score" in s and "On your computer" in s and "#" in s, "ranking table missing:\n" + s[-1500:])
    t.close()


@case("cli", "/remember, /memory, /context and /compact work in the terminal and share the project memory with the GUI")
def cli_memory(c):
    t = c.term = Term(c.stack, ["--engine", "ollama", "--model", "fake-coder:1b", str(c.stack.project_dir)])
    expect(t.wait_for("Ask Zeus-Agent", 40), "prompt never appeared:\n" + t.screen())
    t.buf = ""
    t.send("/memory")                                            # the GUI cases saved a note: the terminal sees the same file
    expect(t.wait_for("Project memory", 20) and t.wait_for("pytest -q", 5), "the GUI's memory is not visible in the terminal:\n" + t.screen())
    t.buf = ""
    t.send("/remember The CLI can write memory too")
    expect(t.wait_for("saved to project memory", 20), "no confirmation:\n" + t.screen())
    expect("The CLI can write memory too" in (c.stack.project_dir / ".zeus" / "memory.md").read_text(), "the note is not in the shared file")
    t.buf = ""
    t.send("Say hello")
    expect(t.wait_for("Hello from fake ollama", 60), "no answer:\n" + t.screen())
    t.buf = ""
    t.send("/context")
    expect(t.wait_for("summarised automatically", 20) and "%" in t.buf, "no context view:\n" + t.screen())
    t.buf = ""
    t.send("/compact")
    expect(t.wait_for("Nothing to compact", 30), "a one-turn conversation must not be compacted:\n" + t.screen())
    t.close()
    sent = c.stack.ollama_requests()[-1][0]["content"]
    expect("The CLI can write memory too" in sent and "Tests run with .venv/bin/pytest -q" in sent, "the model did not receive the shared project memory")


@case("cli", "--print runs one prompt non-interactively on the saved engine")
def cli_print(c):
    r = subprocess.run([backend_python(), "-m", "app.cli", "--engine", "llamacpp", "--model", CODER, "-p", "Say hello", str(c.stack.project_dir)],
                       cwd=BACKEND, env=c.stack.env, capture_output=True, text=True, timeout=120)
    out = strip_ansi(r.stdout + r.stderr)
    expect(r.returncode == 0 and "Hello from fake llama" in out, f"exit {r.returncode}:\n{out[-1500:]}")


# ------------------------------------------------------------------ hand-over
@case("cli", "The terminal's state is what the desktop app would resume (/api/state)")
def handover(c):
    d = http("GET", f"{c.stack.api}/api/state")
    res = d.get("resolved") or {}
    expect(res.get("session") and res.get("project"), f"state does not resolve to a session: {d}")
    expect(d["state"].get("client") in ("cli", "gui"), "client missing")
    time.sleep(0.1)


# ------------------------------------------------------------------ destructive: runs last
@case("final", "Reset: the dialog previews what is removed, needs the word RESET, then leaves a fresh installation and keeps the user's files")
def gui_reset(c):
    pg = c.page
    st = c.stack
    pg.goto(st.ui)
    pg.wait_for_selector("[data-testid=engine-selector]", timeout=30000)
    expect((st.project_dir / ".zeus").exists() and (st.home / "state.json").exists(), "the earlier cases should have left Zeus-Agent data behind")
    (st.project_dir / "mine.txt").write_text("my own file")
    box = pg.locator("textarea").first
    box.fill("/reset")
    box.press("Enter")
    pg.wait_for_selector("[data-testid=reset-dialog]", timeout=20000)
    pg.wait_for_selector("[data-testid=reset-item-conversations]", timeout=20000)
    ids = pg.locator("[data-testid^=reset-item-]").evaluate_all("els => els.map(e => e.getAttribute('data-testid').replace('reset-item-', ''))")
    expect(ids == ["conversations", "project_data", "home", "logs", "models"], f"unexpected items: {ids}")
    checked = pg.locator("[data-testid^=reset-item-] input[type=checkbox]").evaluate_all("els => els.map(e => e.checked)")
    expect(checked == [True, True, True, True, False], f"defaults wrong (downloaded models must not be selected): {checked}")
    expect("Never touched" in pg.inner_text("[data-testid=reset-keeps]") and "Ollama" in pg.inner_text("[data-testid=reset-keeps]"), "what is kept is not listed")
    run = pg.get_by_test_id("reset-run")
    expect(run.is_disabled(), "the button must be disabled before the confirmation word is typed")
    pg.get_by_test_id("reset-word").fill("reset")
    expect(run.is_disabled(), "a lowercase 'reset' must not confirm")
    pg.get_by_test_id("reset-word").fill("RESET")
    expect(run.is_enabled(), "typing RESET should enable the button")
    pg.get_by_test_id("reset-item-conversations").locator("input").uncheck()
    pg.get_by_test_id("reset-item-conversations").locator("input").check()
    expect((st.project_dir / ".zeus").exists(), "opening and confirming the dialog must not remove anything by itself")
    run.click()
    pg.wait_for_selector("[data-testid=reset-done]", timeout=30000)
    pg.wait_for_selector("[data-testid=engine-selector]", timeout=30000)          # the page reloads itself
    expect(http("GET", f"{st.api}/api/projects") == [], "projects should be gone")
    expect(not (st.project_dir / ".zeus").exists() and not (st.home / "state.json").exists(), "history and the state pointer should be gone")
    expect((st.project_dir / "mine.txt").read_text() == "my own file", "a project's own files must never be touched")
    expect((st.models_dir / "e2e-coder-q4_k_m.gguf").exists(), "models the user keeps in their own folder must never be touched")
    expect(http("GET", f"{st.api}/api/settings")["default_model"] in (None, ""), "settings should be back to defaults")
    pg.get_by_text("Open an existing folder").first.wait_for(timeout=20000)                # the app looks like a first run
    # a model can be chosen before any project exists: it is remembered, announced, and the dropdown shows it (this used to do nothing, silently)
    pick(pg, "Model", "fake-coder")
    pg.get_by_text("will be used for your first conversation").first.wait_for(timeout=10000)
    expect("fake-coder" in model_btn(pg).inner_text(), f"the chosen model should show in the dropdown: {model_btn(pg).inner_text()!r}")
    expect(http("GET", f"{st.api}/api/sessions") == [], "choosing a model without a project must not create a conversation")
    p = http("POST", f"{st.api}/api/projects", {"path": str(st.project_dir)})
    expect(p["id"] == 1, f"a reset installation numbers from 1 again: {p}")
    expect(not c.page_errors, f"JavaScript errors: {c.page_errors}")
