"""The interactive `zeus --cli` application: prompt loop, slash commands, approvals."""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import sys
from pathlib import Path

from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.formatted_text import FormattedText
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from ..core import config
from .commands import best as bst
from .commands import llamamodels as lm
from .commands import memory as mem
from .commands import reset as rs
from .ui import banner as bn
from .commands import engines as en
from .client import Api, ApiError, DEFAULT_PORT, start_backend, stop_backend
from .ui.fmt import clip, fmt_bytes, fmt_ctx, fmt_num, parse_ctx, short_path
from .commands.models import catalog_options, group_of, installed_options, norm
from .ui.cards import THEME, output_card, preview_text, user_card, clean_output
from .ui.render import Renderer, permission_panel
from .ui.chooser import Option, ask_text, choose, confirm
from .ui.screen import FullUI
from .ui.theme import DIM, ERR, OK, PURPLE, WARN

PERM_OPTIONS = [
    ("read_only", "Read only", "Zeus-Agent can read files but cannot change anything or run commands"),
    ("approval", "Approval mode", "ask before every file change or command (recommended)"),
    ("trusted", "Trusted workspace", "allow edits and commands in this folder; still asks to delete, install or git push"),
]
CTX_PRESETS = [8192, 16384, 32768, 65536, 131072]

# name -> (aliases, description). Handlers are methods named cmd_<name with - as _>.
COMMANDS: dict[str, tuple[tuple[str, ...], str]] = {
    "help": ((), "Show all commands"),
    "settings": ((), "Models, Ollama endpoint, current model, installed models, About"),
    "ollama-model": (("model",), "Choose the model from your installed Ollama models"),
    "engine": (("engines",), "Choose the engine that runs the models: Ollama or llama.cpp (with a help screen)"),
    "context": ((), "How full the context window is, what is summarised, what memory is in use"),
    "compact": ((), "Summarise the older messages now to free context window"),
    "summary": ((), "Show the rolling summary of this conversation (/summary reset forgets it)"),
    "memory": (("mem",), "Project memory: notes every new conversation starts with (/memory add <note>)"),
    "remember": ((), "Save one note to project memory:  /remember Tests run with pytest -q"),
    "reset": ((), "Return Zeus-Agent to a fresh installation (shows what is removed, asks you to type RESET)"),
    "best": (("benchmarks",), "Which model is best for coding, chat, vision or agents (Arena scores) and whether it runs on this computer"),
    "llamacpp": (("llama",), "llama.cpp: server path, model folders, download a GGUF model, stop the server"),
    "ollama-ctx": (("ollama-ctxt", "ctx"), "Set the context window size"),
    "permissions": (("perm",), "Choose what Zeus-Agent is allowed to do"),
    "project": (("cd",), "Change the project folder (default: where you started zeus)"),
    "shell": (("sh",), "Run a shell command in the project folder and show its output  (e.g. /shell ls -la)"),
    "mission": ((), "Run a Markdown/Word/PDF document as a goal-driven mission"),
    "new": ((), "Start a new conversation"),
    "resume": (("sessions",), "Continue an earlier conversation in this project"),
    "changes": (("diff",), "List files Zeus-Agent changed (use /changes <file> for the diff)"),
    "usage": ((), "Token usage for this conversation"),
    "status": ((), "Show the welcome screen with current settings"),
    "doctor": ((), "Check Ollama, the backend and the selected model"),
    "about": ((), "Version, founder, GitHub, device information"),
    "copy": ((), "Copy the last answer or command output to the clipboard"),
    "fold": (("collapse",), "Fold all earlier prompts to one line each (click a ▾ row to fold or unfold one)"),
    "unfold": (("expand",), "Unfold every prompt and its output"),
    "clear": ((), "Clear the screen"),
    "exit": (("quit", "q"), "Leave Zeus-Agent"),
}
ALIAS = {a: n for n, (al, _) in COMMANDS.items() for a in (n, *al)}


class SlashCompleter(Completer):
    """Completes /commands with descriptions, and folders/files for /project and /mission."""

    def __init__(self):
        self.dirs = PathCompleter(only_directories=True, expanduser=True)
        self.files = PathCompleter(expanduser=True)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or "\n" in text:
            return
        if " " not in text:
            for name, (al, desc) in COMMANDS.items():
                for n in (name, *al):
                    if ("/" + n).startswith(text) and not (n != name and text == "/"):
                        yield Completion("/" + n, start_position=-len(text), display="/" + n, display_meta=desc if n == name else f"alias of /{name}")
            return
        cmd, _, rest = text.partition(" ")
        target = ALIAS.get(cmd[1:])
        from prompt_toolkit.document import Document
        sub = Document(rest, len(rest))
        if target == "project":
            yield from self.dirs.get_completions(sub, complete_event)
        elif target == "mission":
            yield from self.files.get_completions(sub, complete_event)


def resolve_dir(p: str, base: Path | None = None) -> Path:
    q = Path(os.path.expandvars(os.path.expanduser(p.strip().strip("'\""))))
    if not q.is_absolute():
        q = (base or Path.cwd()) / q
    return q.resolve()


SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SHELL_TIMEOUT = 120


def copy_to_clipboard(text: str, output=None) -> str | None:
    """Copy via wl-copy / xclip / xsel / pbcopy, else the terminal's OSC 52 sequence. Returns how it was done, or None."""
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["pbcopy"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
                return cmd[0]
            except (OSError, subprocess.SubprocessError):
                continue
    if output is not None:
        import base64
        try:
            output.write_raw("\x1b]52;c;" + base64.b64encode(text.encode()).decode() + "\x07")
            output.flush()
            return "terminal clipboard (OSC 52)"
        except Exception:
            return None
    return None


class Cli:
    def __init__(self, api: Api, console: Console, cwd: Path, *, model: str | None = None, ctx: int | None = None,
                 perm: str | None = None, resume: bool = False, engine: str | None = None, explicit_path: bool = False):
        self.api, self.c, self.cwd = api, console, cwd
        self.opt_model, self.opt_ctx, self.opt_perm, self.opt_resume = model, ctx, perm, resume
        self.opt_engine, self.explicit_path = engine, explicit_path
        self.engine = "ollama"                   # what runs the models: "ollama" or "llamacpp"
        self.engines: dict[str, dict] = {}       # status of both engines (from /api/engines)
        self.engine_help: dict = {}
        self.recommendation: dict = {}
        self.resumed: dict | None = None         # set when --continue picked up a conversation from state.json
        self.project: dict | None = None
        self.session: dict | None = None
        self.model: str | None = None
        self.ctx = 16384
        self.perm = "approval"
        self.ollama: dict = {}
        self.about: dict | None = None
        self.installed: list[dict] = []
        self.usage = {"in": 0, "out": 0}
        self.should_exit = False
        self.ui: FullUI | None = None            # the full-screen layout (None in -p print mode)
        self.queue: asyncio.Queue | None = None
        self.working = False                    # an agent run or shell command is in progress
        self.shell_proc: asyncio.subprocess.Process | None = None
        self.last_copy = ""                     # last answer or command output, for /copy
        self.r = Renderer(console)

    # ------------------------------------------------------------------ state
    @property
    def snapshot(self) -> dict:
        return {"model": self.model, "ctx": self.ctx, "perm": self.perm, "project_path": self.project["path"] if self.project else str(self.cwd),
                "ollama": self.ollama, "version": config.VERSION, "engine": self.engine, "engines": self.engines}

    @property
    def engine_ok(self) -> bool:
        e = self.engines.get(self.engine)
        return bool(e["available"]) if e else bool(self.ollama.get("connected"))

    async def refresh(self):
        self.ollama = await self.api.get("/api/ollama/status")
        try:
            j = await self.api.get("/api/engines")
            self.engines = {e["id"]: e for e in j["engines"]}
            self.engine_help, self.recommendation = j.get("help", {}), j.get("recommendation", {})
        except ApiError:
            self.engines = {}
        try:
            self.installed = await self.api.get("/api/models", engine=self.engine) if self.engine_ok else []
        except ApiError:
            self.installed = []

    async def start(self):
        await self.api.get("/api/health")
        s = await self.api.get("/api/settings")
        self.engine = en.engine_of(self.opt_engine or "") or en.engine_of(s.get("engine") or "") or "ollama"
        await self.use_project(self.cwd, announce=False)
        if self.opt_resume:
            await self.resume()                  # may switch project, engine, model and context to where you left off
        await self.refresh()
        try:
            self.about = await self.api.get("/api/about")
        except ApiError:
            self.about = None
        if not self.resumed or not self.model:
            self.ctx = self.opt_ctx or self.ctx_from(s)
            self.model = self.pick_model(s)
        if self.opt_ctx:
            self.ctx = self.opt_ctx
        if self.opt_model:
            self.model = self.pick_model(s)
        if self.opt_perm:
            await self.set_perm(self.opt_perm, announce=False)

    def ctx_from(self, s: dict) -> int:
        return int(s.get("default_num_ctx") or 16384)

    def pick_model(self, s: dict) -> str | None:
        """The model to start with: the one asked for, else the one used last with this engine, else something sensible."""
        names = {m["name"] for m in self.installed}
        if self.engine == "llamacpp":
            last = (self.engines.get("llamacpp") or {}).get("last_model")
            want = self.opt_model or last
            return want if want in names else (next(iter(sorted(names)), None))
        dm = self.opt_model or s.get("default_model")
        if dm == "auto" or (dm and dm in names):
            return dm
        return "auto" if any(not m["name"].lower().startswith(("nomic", "mxbai", "bge", "all-minilm")) for m in self.installed) else None

    async def resume(self):
        """--continue: pick up the conversation remembered in ~/.zeus/state.json (whichever client you used last), and fall back to
        the latest conversation of this folder. With an explicit folder argument only that folder's latest conversation is considered."""
        if not self.explicit_path:
            try:
                st = await self.api.get("/api/state")
            except ApiError:
                st = {}
            r = st.get("resolved")
            if r and r.get("project") and Path(r["project"]["path"]).is_dir():
                if str(Path(r["project"]["path"])) != str(self.cwd):
                    await self.use_project(Path(r["project"]["path"]), announce=False)
                    os.chdir(self.cwd)
                sess = r.get("session")
                self.engine = en.engine_of(r.get("engine") or (sess or {}).get("engine") or "") or self.engine
                if sess:
                    self.session = sess
                    self.model, self.ctx = sess.get("model") or r.get("model"), sess.get("num_ctx") or r.get("num_ctx") or self.ctx
                else:
                    self.model, self.ctx = r.get("model"), r.get("num_ctx") or self.ctx
                self.resumed = {"client": (st.get("state") or {}).get("client"), "title": (sess or {}).get("title"), "path": r["project"]["path"]}
                if sess:
                    await self.api.post(f"/api/sessions/{sess['id']}/open")          # this client is now the one that last used it
                return
        sess = await self.api.get("/api/sessions", project_id=self.project["id"])
        if sess:
            self.session = sess[0]
            self.engine = en.engine_of(self.session.get("engine") or "") or self.engine
            self.model, self.ctx = self.session.get("model") or self.model, self.session.get("num_ctx") or self.ctx
            self.resumed = {"client": None, "title": self.session.get("title"), "path": self.project["path"]}
            await self.api.post(f"/api/sessions/{self.session['id']}/open")

    async def use_project(self, path: Path, announce: bool = True):
        if not path.is_dir():
            raise ApiError(f"not a folder: {path}")
        self.project = await self.api.post("/api/projects", {"path": str(path)})
        self.perm = self.project.get("permission_mode") or "approval"
        self.session = None
        self.cwd = path
        if announce:
            self.c.print(Text.assemble(("  ✓ ", OK), "project is now ", (short_path(path, 70), f"bold {PURPLE}")))

    async def ensure_session(self) -> dict:
        if self.session is None:
            self.session = await self.api.post("/api/sessions", {"project_id": self.project["id"], "model": self.model, "num_ctx": self.ctx, "engine": self.engine})
        return self.session

    async def set_model(self, name: str):
        self.model = name
        if self.session:
            self.session = await self.api.patch(f"/api/sessions/{self.session['id']}", {"model": name})
        if self.engine == "ollama":
            await self.api.put("/api/settings", {"default_model": name})          # (llama.cpp remembers its last model per engine itself)
        self.c.print(Text.assemble(("  ✓ ", OK), "model: ", (name, f"bold {PURPLE}")))

    async def set_ctx(self, n: int):
        self.ctx = n
        if self.session:
            self.session = await self.api.patch(f"/api/sessions/{self.session['id']}", {"num_ctx": n})
        await self.api.put("/api/settings", {"default_num_ctx": n})
        self.c.print(Text.assemble(("  ✓ ", OK), "context window: ", (f"{n:,} tokens", f"bold {PURPLE}")))

    async def set_perm(self, mode: str, announce: bool = True):
        await self.api.put(f"/api/projects/{self.project['id']}/permissions", {"mode": mode})
        self.perm = mode
        if announce:
            label = next(l for m, l, _ in PERM_OPTIONS if m == mode)
            self.c.print(Text.assemble(("  ✓ ", OK), "permissions: ", (label, f"bold {PURPLE}")))

    # ------------------------------------------------------------------ dialogs
    # Selection lists and text prompts are drawn inside the full-screen layout, above the (disabled) input box.
    async def _choose(self, *a, **k):
        return await (self.ui.choose(*a, **k) if self.ui else choose(*a, **k))

    async def _ask(self, *a, **k):
        return await (self.ui.ask_text(*a, **k) if self.ui else ask_text(*a, **k))

    async def _confirm(self, *a, **k):
        return await (self.ui.confirm(*a, **k) if self.ui else confirm(*a, **k))

    # ------------------------------------------------------------------ screens
    def show_banner(self) -> None:
        self.c.print(bn.banner(self.snapshot, self.about, self.c.width, color=self.c.color_system is not None))
        self.c.print(bn.tips())
        self.c.print()

    def toolbar(self):
        m = self.model or "no model"
        ok = self.engine_ok
        label = next((l for k, l, _ in PERM_OPTIONS if k == self.perm), self.perm)
        name = en.LABEL.get(self.engine, self.engine)
        frags = [("fg:ansibrightblack", "  "), ("fg:#a78bfa bold", "⚡ "), ("fg:ansibrightblack", f"{name} · "), ("", f"{m}"),
                 ("fg:ansibrightblack", f" · ctx {fmt_ctx(self.ctx)} · "), ("", label), ("fg:ansibrightblack", f" · {short_path(self.project['path'] if self.project else self.cwd, 40)} · "),
                 ("fg:ansigreen" if ok else "fg:ansired", f"● {name}" if ok else f"● {name} offline")]
        if self.working:
            frame = SPINNER[int(time.time() * 10) % len(SPINNER)]
            frags = [("fg:#a78bfa bold", f"  {frame} Thunderstorm is approaching…"), ("fg:ansibrightblack", "   Ctrl+C to stop"), ("fg:ansibrightblack", "  ·  "), *frags[2:]]
        return FormattedText(frags)

    # ------------------------------------------------------------------ commands
    async def run_command(self, line: str):
        name, _, arg = line[1:].partition(" ")
        key = ALIAS.get(name.lower())
        if not key:
            near = [n for n in ALIAS if n.startswith(name.lower())][:3]
            self.c.print(Text(f"  unknown command /{name}" + (f" – did you mean {', '.join('/' + n for n in near)}?" if near else " – try /help"), style=ERR))
            return
        await getattr(self, "cmd_" + key.replace("-", "_"))(arg.strip())

    async def cmd_help(self, arg):
        t = Table.grid(padding=(0, 3))
        t.add_column(style=f"bold {PURPLE}", no_wrap=True)
        t.add_column(style=DIM)
        for n, (al, d) in COMMANDS.items():
            t.add_row("/" + n, d + (f"  (also {', '.join('/' + a for a in al)})" if al else ""))
        t.add_row("!<command>", "Run a shell command in the project folder")
        t.add_row("Alt+Enter", "New line in your prompt")
        t.add_row("Ctrl+C", "Stop a running answer (or clear the prompt)")
        self.c.print(t)

    async def cmd_status(self, arg):
        await self.refresh()
        self.show_banner()

    async def cmd_fold(self, arg):
        if self.ui is None or not self.ui.turns:
            self.c.print(Text("  Nothing to fold yet.", style=DIM))
            return
        n = self.ui.fold_all(True, keep_last=True)
        self.c.print(Text(f"  ▸ folded {max(0, n - 1)} earlier prompt(s). Click a row (or /unfold) to open them again.", style=DIM))

    async def cmd_unfold(self, arg):
        if self.ui is not None:
            self.ui.fold_all(False)

    async def cmd_clear(self, arg):
        if self.ui is None:
            self.c.clear()
            return
        self.ui.clear_log()
        self.show_banner()

    async def cmd_copy(self, arg):
        text = self.last_copy
        if not text:
            self.c.print(Text("  Nothing to copy yet.", style=DIM))
            return
        how = await asyncio.to_thread(copy_to_clipboard, text, self.ui.app.output if self.ui else None)
        self.c.print(Text(f"  ✓ copied {len(text):,} characters ({how})" if how else "  ✗ no clipboard tool found (install wl-clipboard or xclip), or select with Shift+drag", style=OK if how else WARN))

    async def cmd_shell(self, arg):
        await self.run_shell(arg)

    async def cmd_exit(self, arg):
        self.should_exit = True

    async def cmd_about(self, arg):
        about = await self.api.get("/api/about")
        self.about = about
        t = Table.grid(padding=(0, 3))
        t.add_column(style=DIM)
        t.add_column()
        for k, v in [("Zeus-Agent", f"v{about['version']} · {about['tagline']}"), ("Founder", about["founder"]), ("GitHub", about["github"]),
                     ("Website", about["website"]), ("License", about["license"]), ("", ""), *bn.device_rows(about),
                     ("Kernel", f"{about['platform']['kernel']}"), ("Ollama", about["platform"].get("ollama") or "not connected"),
                     ("Swap", f"{fmt_bytes(about['memory'].get('swap_total'))}")]:
            t.add_row(k, v)
        self.c.print(t)

    async def cmd_ollama_model(self, arg):
        """Choose the model for the current engine: Ollama's installed models, or the GGUF files llama.cpp can run."""
        await self.refresh()
        name = en.LABEL[self.engine]
        if not self.engine_ok:
            e = self.engines.get(self.engine) or {}
            err = e.get("error") or self.ollama.get("error", "unknown error")
            hint = "Check /settings → Ollama endpoint." if self.engine == "ollama" else "Run /engine → Help me choose for the install steps, then /llamacpp."
            self.c.print(Text(f"  {name} is not available ({err}). {hint}", style=ERR))
            return
        if arg:                                           # /model qwen3:8b
            names = [m["name"] for m in self.installed]
            hit = arg if arg in names or (arg == "auto" and self.engine == "ollama") else next((n for n in names if n.startswith(arg)), None)
            if not hit:
                where = "/settings → Models → Download" if self.engine == "ollama" else "/llamacpp → Download a model"
                self.c.print(Text(f"  '{arg}' is not available in {name}. Run /model to pick, or {where}.", style=ERR))
                return
            await self.set_model(hit)
            return
        if self.engine == "llamacpp":
            if not self.installed:
                self.c.print(Text("  No GGUF models found. Use /llamacpp to add a model folder or download one.", style=WARN))
                return
            opts = [Option(m["name"], m["name"], " · ".join(x for x in (fmt_bytes(m.get("size")), m.get("parameter_size"), m.get("quantization"),
                                                                       f"{fmt_ctx(m['context_length'])} ctx" if m.get("context_length") else None,
                                                                       "tools" if m.get("tools") else None) if x)) for m in self.installed if not m.get("embedding")]
            v = await self._choose("Choose a model  (GGUF files llama.cpp can run)", opts, current=self.model, searchable=True)
            if v:
                await self.set_model(v)
            return
        ov = await self.api.get("/api/ollama/overview")
        opts = installed_options(self.installed, ov.get("loaded"))
        if len(opts) <= 1:
            self.c.print(Text("  No models installed yet. Open /settings → Models → Download a model.", style=WARN))
            return
        v = await self._choose("Choose a model  (installed in Ollama)", opts, current=self.model, searchable=True)
        if v:
            await self.set_model(v)

    # ------------------------------------------------------------------ engines
    async def cmd_engine(self, arg):
        await self.refresh()
        want = en.engine_of(arg) if arg else None
        if arg and not want:
            self.c.print(Text("  Use /engine ollama or /engine llamacpp (or just /engine to choose).", style=ERR))
            return
        while not want:
            v = await self._choose("Engine  (what runs the models)", en.options(self.engines, self.recommendation.get("engine")), current=self.engine)
            if v == "help":
                self.c.print(en.help_screen(self.engine_help, self.recommendation, self.engine, self.engines))
                continue
            if not v:
                return
            want = v
        await self.set_engine(want)

    async def set_engine(self, name: str, announce: bool = True):
        out = await self.api.put("/api/engine", {"engine": name, "session_id": self.session["id"] if self.session else None})
        self.engine = name
        self.engines = {e["id"]: e for e in out["engines"]}
        self.recommendation = out.get("recommendation", {})
        if self.session:
            sess = next((x for x in await self.api.get("/api/sessions", project_id=self.project["id"]) if x["id"] == self.session["id"]), None)
            if sess:
                self.session = sess
        await self.refresh()
        self.model = (self.session or {}).get("model") or self.engines.get(name, {}).get("last_model") or self.pick_model({})
        if self.session and self.model and self.session.get("model") != self.model:
            self.session = await self.api.patch(f"/api/sessions/{self.session['id']}", {"model": self.model})
        if announce:
            self.c.print(Text.assemble(("  ✓ ", OK), "engine: ", (en.LABEL[name], f"bold {PURPLE}"), (f" · model: {self.model}" if self.model else "", DIM)))
            if not self.engine_ok:
                self.c.print(Text(f"  {en.LABEL[name]} is not available yet. Run /engine → Help me choose.", style=WARN))
            elif not self.model:
                self.c.print(Text("  No model yet. Run /model to choose one.", style=WARN))

    async def cmd_reset(self, arg):
        try:
            plan = await self.api.get("/api/reset")
        except Exception as e:  # noqa: BLE001
            self.c.print(mem.error(f"Could not read what Zeus-Agent has saved: {e}"))
            return
        self.c.print(rs.plan_view(plan))
        answer = await self._ask(f"Type {plan['confirm']} to remove the items marked ✓ (add `models` to include downloaded models); Enter cancels", default="")
        ids, include_models = rs.parse_answer(answer or "", plan)
        if ids is None:
            self.c.print(Text("  Nothing was removed.", style=DIM))
            return
        try:
            report = await self.api.post("/api/reset", {"confirm": plan["confirm"], "items": ids})
        except Exception as e:  # noqa: BLE001
            self.c.print(mem.error(f"Reset failed: {e}"))
            return
        self.c.print(rs.report_view(report))
        self.c.print(Text("  Restart zeus --cli to begin again.", style=DIM))
        await self.cmd_exit("")

    async def cmd_context(self, arg):
        if not self.session:
            self.c.print(mem.error("Nothing sent yet: there is no conversation to measure."))
            return
        self.c.print(mem.context_view(await self.api.get(f"/api/sessions/{self.session['id']}/context")))

    async def cmd_compact(self, arg):
        if not self.session:
            self.c.print(mem.error("Nothing sent yet: there is no conversation to summarise."))
            return
        self.c.print(Text("  Summarising the older messages…", style=DIM))
        try:
            r = await self.api.post(f"/api/sessions/{self.session['id']}/compact")
        except Exception as e:  # noqa: BLE001
            self.c.print(mem.error(f"Could not compact: {e}"))
            return
        self.c.print(mem.compact_result(r))

    async def cmd_summary(self, arg):
        if not self.session:
            self.c.print(mem.error("Nothing sent yet."))
            return
        sid = self.session["id"]
        if arg.strip() == "reset":
            await self.api.req("DELETE", f"/api/sessions/{sid}/summary")
            self.c.print(Text("  ✓ Summary forgotten: the whole conversation is sent to the model again.", style=OK))
            return
        c = await self.api.get(f"/api/sessions/{sid}/context")
        if c.get("summary"):
            self.c.print(mem.summary_view(c["summary"]["text"], c["summary"]["covers"]))
        else:
            self.c.print(Text(f"  No summary yet. The conversation is summarised at {c['trigger_percent']}% of the context window, or on request with /compact.", style=DIM))

    async def cmd_memory(self, arg):
        if not self.project:
            return
        pid = self.project["id"]
        sub, _, rest = arg.strip().partition(" ")
        if sub == "add":
            return await self.cmd_remember(rest)
        if sub == "clear":
            if await self._confirm("Erase all project memory?"):
                await self.api.put(f"/api/projects/{pid}/memory", {"text": ""})
                self.c.print(Text("  ✓ Project memory cleared.", style=OK))
            return
        self.c.print(mem.memory_view(await self.api.get(f"/api/projects/{pid}/memory")))
        self.c.print(Text("  /memory add <note> · /memory clear · or edit the file directly", style=DIM))

    async def cmd_remember(self, arg):
        if not self.project:
            return
        if not arg.strip():
            self.c.print(mem.error("Write the note after the command, for example /remember Tests run with pytest -q"))
            return
        try:
            r = await self.api.post(f"/api/projects/{self.project['id']}/memory/note", {"note": arg})
        except Exception as e:  # noqa: BLE001
            self.c.print(mem.error(str(e)))
            return
        self.c.print(Text.assemble(("  ✓ ", OK), ("saved to project memory" if r["added"] else "already in project memory", "bold"), (f"  {r['line'][2:]}", DIM)))

    async def cmd_best(self, arg):
        words = (arg or "").split()
        everything = "all" in words
        words = [w for w in words if w != "all"]
        act = bst.activity_of(words[0]) if words else None
        if words and not act:
            self.c.print(Text("  Use /best coding, agentic, agents, chat, vision or documents (add `all` to include models that do not fit).", style=ERR))
            return
        try:
            ov = await self.api.get("/api/arena", ctx=(self.session or {}).get("num_ctx") or 8192)
        except Exception as e:  # noqa: BLE001
            self.c.print(Text(f"  Could not read the model rankings: {e}", style=ERR))
            return
        if not act:
            act = await self._choose("Best model for…", bst.options(ov), current="agentcode")
            if not act:
                return
        self.c.print(bst.render(ov, act, only_fit=not everything))

    async def cmd_llamacpp(self, arg):
        """llama.cpp settings: where llama-server is, which folders hold models, download a model, stop the server."""
        while True:
            j = await self.api.get("/api/llamacpp")
            st, cfg, srv = j["status"], j["config"], j["server"]
            opts = [
                Option("llama-server", "binary", (st.get("binary") or cfg.get("binary") or "not found") + (f" · {st['version']}" if st.get("version") else "")
                       + ("  (found automatically)" if j.get("binary_auto") and st.get("binary") else "")),
                Option("Found on this computer", "found", f"{len(j.get('found', []))} llama-server program{'' if len(j.get('found', [])) == 1 else 's'} · choose one or search again"),
                Option("Model folders", "folders", f"{len(j['folders'])} folders · {lm.summary(j['models'])}"),
                Option("Download a model", "download", "search Hugging Face; see how each file runs on this computer"),
                Option("Uninstall a model", "uninstall", f"{len(lm.removable_options(j['models']))} can be deleted · Ollama's models are removed in Ollama"),
                Option("Use llama.cpp as the engine", "use", "switch this conversation to llama.cpp"),
                Option("Stop the server", "stop", f"loaded: {srv['model']}" if srv.get("running") else "nothing loaded"),
            ]
            v = await self._choose("llama.cpp", opts)
            if not v:
                return
            try:
                if v == "binary":
                    t = await self._ask("Full path of llama-server (empty = find it automatically)", default=cfg.get("binary") or "")
                    if t is not None:
                        await self.api.put("/api/llamacpp/config", {"binary": t.strip()})
                        self.c.print(Text("  ✓ saved", style=OK))
                elif v == "found":
                    await self.choose_llama_server(j)
                elif v == "folders":
                    t = await self._ask("Add a folder with .gguf files (Tab completes)", default=str(Path.home()) + os.sep, paths=True)
                    if t:
                        await self.api.put("/api/llamacpp/config", {"models_dirs": [*cfg.get("models_dirs", []), t.strip()]})
                        self.c.print(Text("  ✓ folder added", style=OK))
                elif v == "download":
                    await self.download_gguf()
                elif v == "uninstall":
                    await self.uninstall_gguf(j)
                elif v == "use":
                    await self.set_engine("llamacpp")
                    return
                elif v == "stop":
                    await self.api.post("/api/llamacpp/server/stop")
                    self.c.print(Text("  ✓ server stopped", style=OK))
            except ApiError as e:
                self.c.print(Text(f"  {e}", style=ERR))

    async def choose_llama_server(self, j: dict):
        """Pick which llama-server to use from the programs found on this computer, go back to the automatic choice, or search again."""
        if j.get("binary_problem"):
            self.c.print(Text(f"  {j['binary_problem']}", style=WARN))
        opts = [Option(f["path"], f["path"], " · ".join(x for x in (f.get("version"), ("GPU: " + ", ".join(f["devices"])) if f.get("gpu") else "CPU only", f"in {f['source']}",
                                                                      "automatic choice" if f.get("auto") and j.get("binary_auto") else "") if x)) for f in j.get("found", [])]
        opts += [Option("Use the automatic choice", "__auto", "prefers a build that can use your GPU, then the newest"), Option("Search the computer again", "__rescan", "after installing or building llama.cpp")]
        v = await self._choose("llama-server programs", opts, current=(j["status"].get("binary") if not j.get("binary_auto") else "__auto"))
        if not v:
            return
        if v == "__rescan":
            r = await self.api.post("/api/llamacpp/rescan")
            self.c.print(Text(f"  ✓ found {len(r.get('found', []))} llama-server program{'' if len(r.get('found', [])) == 1 else 's'}", style=OK))
        else:
            await self.api.put("/api/llamacpp/config", {"binary": "" if v == "__auto" else v})
            self.c.print(Text("  ✓ saved", style=OK))

    async def download_gguf(self):
        """Search Hugging Face (or type owner/name), choose a repository, then a file: each file shows how it would run on this computer."""
        text = await self._ask("Search Hugging Face for a GGUF model (e.g. qwen coder), or type owner/name  (Enter = most downloaded)", default="")
        if text is None:
            return
        text = text.strip()
        if lm.looks_like_repo(text):
            repo = text
        else:
            try:
                found = await self.api.get("/api/llamacpp/search", q=text)
            except ApiError as e:
                self.c.print(Text(f"  {e}", style=ERR))
                return
            if not found["results"]:
                self.c.print(Text(f"  No GGUF models found for “{text}”.", style=WARN))
                return
            repo = await self._choose(f"GGUF models{' for ' + text if text else ''} (most downloaded first)", lm.search_options(found["results"]))
            if not repo:
                return
        info = await self.api.get("/api/llamacpp/hf", repo=repo)
        opts = lm.file_options(info)
        if not opts:
            self.c.print(Text("  No single-file GGUF models in that repository.", style=WARN))
            return
        hw = info.get("hardware") or {}
        v = await self._choose(f"Files in {info['repo']}  ({hw.get('gpu') or 'no GPU'}, {hw.get('ram_gb', '?')} GB RAM)", opts, current=info.get("suggested"))
        if not v:
            return
        await self.api.post("/api/llamacpp/download", {"repo": info["repo"], "file": v})
        self.c.print(Text(f"  ⇣ downloading {v} in the background; it appears in /model when finished (watch it in /llamacpp).", style=DIM))

    async def uninstall_gguf(self, j: dict):
        """Delete a model Zeus-Agent downloaded (or one in a download cache). Models in your own folders and Ollama's are never deleted."""
        opts = lm.removable_options(j["models"])
        if not opts:
            self.c.print(Text("  Nothing here can be deleted by Zeus-Agent. Models in your own folders are yours to remove, and Ollama's are removed in Ollama (ollama rm <name>).", style=DIM))
            return
        name = await self._choose("Uninstall which model?", opts)
        if not name:
            return
        m = next(x for x in j["models"] if x["name"] == name)
        files = "\n".join("    " + f for f in m["removable"]["files"])
        if not await self._confirm(f"Delete {name} ({fmt_bytes(m['removable']['bytes'] or m['size'])})? This cannot be undone.\n{files}"):
            return
        r = await self.api.req("DELETE", "/api/llamacpp/models", json_body={"model": name})
        self.c.print(Text(f"  ✓ {name} uninstalled, {fmt_bytes(r['deleted']['bytes'])} freed", style=OK))

    async def cmd_ollama_ctx(self, arg):
        if arg:
            n = parse_ctx(arg) if arg.lower() != "max" else await self._max_ctx()
            if not n:
                self.c.print(Text("  Give a number such as 32768 or 32k (512 to 4M), or 'max'.", style=ERR))
                return
            await self.set_ctx(n)
            return
        mx = await self._max_ctx()
        opts = [Option(f"{fmt_ctx(n)} tokens", n, "uses more memory as it grows" if n >= 65536 else "") for n in CTX_PRESETS]
        if mx:
            opts.append(Option(f"Max supported ({fmt_ctx(mx)})", mx, f"{self.model}'s native limit; needs lots of memory"))
        opts.append(Option("Custom…", "custom"))
        v = await self._choose("Context window  (how much conversation the model can see)", opts, current=self.ctx)
        if v == "custom":
            t = await self._ask("Tokens (e.g. 24576 or 24k)")
            v = parse_ctx(t or "")
            if t and not v:
                self.c.print(Text("  Not a valid size.", style=ERR))
        if v:
            await self.set_ctx(int(v))
            await self._ctx_warning()

    async def _max_ctx(self) -> int | None:
        if not self.model or self.model == "auto":
            return None
        try:
            return (await self.api.get("/api/models/info", name=self.model, engine=self.engine)).get("context_length")
        except ApiError:
            return None

    async def _ctx_warning(self):
        if not self.model or self.model == "auto":
            return
        try:
            w = ((await self.api.get("/api/models/info", name=self.model, num_ctx=self.ctx, engine=self.engine)).get("context_check") or {}).get("warning")
        except ApiError:
            return
        if w:
            self.c.print(Text("  ⚠ " + w, style=WARN))

    async def cmd_permissions(self, arg):
        if arg:
            hit = next((m for m, l, _ in PERM_OPTIONS if arg.lower().replace(" ", "_").replace("-", "_") in (m, l.lower().replace(" ", "_"))), None)
            if not hit:
                self.c.print(Text("  Use one of: read_only, approval, trusted", style=ERR))
                return
            await self.set_perm(hit)
            return
        v = await self._choose("Permissions  (what Zeus-Agent may do in this project)", [Option(l, m, h) for m, l, h in PERM_OPTIONS], current=self.perm)
        if v:
            await self.set_perm(v)

    async def cmd_project(self, arg):
        if not arg:
            projs = await self.api.get("/api/projects")
            opts = [Option("Enter a path…", "__path__")]
            opts += [Option(p["name"], p["path"], short_path(p["path"], 60)) for p in projs if Path(p["path"]).is_dir()][:15]
            v = await self._choose(f"Project folder  (now: {short_path(self.cwd, 50)})", opts, current=str(self.cwd))
            if v == "__path__":
                v = await self._ask("Folder path (Tab completes)", default=str(self.cwd) + os.sep, paths=True)
            if not v:
                return
            arg = v
        p = resolve_dir(arg, self.cwd)
        if not p.exists():
            if await self._confirm(f"{p} does not exist. Create it?"):
                p.mkdir(parents=True, exist_ok=True)
            else:
                return
        if not self.r.edited or True:
            await self.use_project(p)
        os.chdir(p)

    async def cmd_new(self, arg):
        self.session = None
        self.usage = {"in": 0, "out": 0}
        self.c.print(Text("  ✓ new conversation", style=OK))

    async def cmd_resume(self, arg):
        sess = await self.api.get("/api/sessions", project_id=self.project["id"])
        if not sess:
            self.c.print(Text("  No earlier conversations in this project.", style=DIM))
            return
        v = await self._choose("Resume a conversation", [Option(clip(s["title"], 70), s["id"], f"{s.get('model') or ''}") for s in sess[:30]],
                         current=self.session["id"] if self.session else None, searchable=True)
        if not v:
            return
        self.session = next(s for s in sess if s["id"] == v)
        self.model = self.session.get("model") or self.model
        self.ctx = self.session.get("num_ctx") or self.ctx
        eng = en.engine_of(self.session.get("engine") or "")
        if eng and eng != self.engine:                    # a conversation resumes on the engine it was started with
            self.engine = eng
            await self.refresh()
        await self.api.post(f"/api/sessions/{v}/open")
        msgs = await self.api.get(f"/api/sessions/{v}/messages")
        for m in msgs[-6:]:
            who = "you" if m.get("role") == "user" else "zeus" if m.get("role") == "assistant" else None
            if who and m.get("content"):
                self.c.print(Text.assemble((f"  {who}: ", f"bold {PURPLE}" if who == "zeus" else "bold"), (clip(m["content"], 160), DIM)))
        self.c.print(Text(f"  ✓ resumed '{clip(self.session['title'], 50)}'", style=OK))

    async def cmd_changes(self, arg):
        rows = await self.api.get(f"/api/projects/{self.project['id']}/changes")
        if not rows:
            self.c.print(Text("  No changes recorded in this project.", style=DIM))
            return
        if arg:
            d = await self.api.get(f"/api/projects/{self.project['id']}/changes/diff", path=arg)
            import difflib
            from rich.syntax import Syntax
            diff = "".join(difflib.unified_diff(d["before"].splitlines(True), d["after"].splitlines(True), f"a/{arg}", f"b/{arg}"))
            self.c.print(Syntax(diff or "(no textual difference)", "diff", theme="ansi_dark"))
            return
        for r in rows:
            col = {"created": OK, "modified": WARN, "deleted": ERR}.get(r["status"], DIM)
            self.c.print(Text.assemble((f"  {r['status']:<9}", col), (r["path"], "bold")))
        self.c.print(Text("  /changes <file> shows the diff", style=DIM))

    async def cmd_usage(self, arg):
        s = self.session
        if not s:
            self.c.print(Text("  Nothing sent yet in this conversation.", style=DIM))
            return
        s = await self.api.get("/api/sessions", project_id=self.project["id"]) and next((x for x in await self.api.get("/api/sessions", project_id=self.project["id"]) if x["id"] == s["id"]), s)
        self.c.print(Text.assemble(("  tokens in ", DIM), (fmt_num(s.get("input_tokens")), "bold"), ("   out ", DIM), (fmt_num(s.get("output_tokens")), "bold"),
                                   ("   context ", DIM), (fmt_ctx(self.ctx), "bold")))

    async def cmd_doctor(self, arg):
        await self.refresh()
        ok = lambda b: Text("✓", style=OK) if b else Text("✗", style=ERR)  # noqa: E731
        self.c.print(Text.assemble(ok(True), f" backend v{config.VERSION} on port {self.api.port}"))
        self.c.print(Text.assemble(ok(self.ollama.get("connected")), f" Ollama {self.ollama.get('version', '')} at {self.ollama.get('endpoint', '?')}"
                                   + ("" if self.ollama.get("connected") else f" – {self.ollama.get('error', '')}")))
        ll = self.engines.get("llamacpp") or {}
        self.c.print(Text.assemble(ok(ll.get("available")), f" llama.cpp {ll.get('version') or ''} ({ll.get('models', 0)} GGUF models)" + ("" if ll.get("available") else f" – {ll.get('error', 'not found')}")))
        self.c.print(Text.assemble(("  ", ""), ("engine in use: ", DIM), (en.LABEL.get(self.engine, self.engine), "bold")))
        self.c.print(Text.assemble(ok(bool(self.installed)), f" {len(self.installed)} models installed"))
        if self.model and self.model != "auto":
            have = self.model in {m["name"] for m in self.installed}
            self.c.print(Text.assemble(ok(have), f" selected model {self.model}" + ("" if have else " is not installed")))
        self.c.print(Text.assemble(ok(Path(self.project["path"]).is_dir()), f" project folder {short_path(self.project['path'], 70)}"))
        try:
            ci = await self.api.get("/api/convert/formats")
            self.c.print(Text.assemble(ok(ci.get("installed")), f" document conversion (MarkItDown {ci.get('version') or 'missing'})"))
        except ApiError:
            pass

    # ------------------------------------------------------------------ /settings
    async def cmd_settings(self, arg):
        while True:
            await self.refresh()
            opts = [
                Option("Engine", "engine", f"{en.LABEL.get(self.engine, self.engine)} · switch to Ollama or llama.cpp, with help"),
                Option("llama.cpp", "llama", "server path, model folders, download a GGUF model"),
                Option("Models", "models", f"{len(self.installed)} available · download, switch, unload, uninstall"),
                Option("Ollama endpoint", "endpoint", ("connected" if self.ollama.get("connected") else "not connected") + f" · {self.ollama.get('endpoint', '')}"),
                Option("Current model", "current", self.model or "none"),
                Option("Installed models", "installed", f"{len(self.installed)} models"),
                Option("Context window", "ctx", f"{self.ctx:,} tokens"),
                Option("Permissions", "perm", next((l for k, l, _ in PERM_OPTIONS if k == self.perm), self.perm)),
                Option("About Zeus-Agent", "about", f"v{config.VERSION} · device & memory"),
            ]
            v = await self._choose("Settings", opts)
            if not v:
                return
            fn = {"engine": lambda: self.cmd_engine(""), "llama": lambda: self.cmd_llamacpp(""), "models": self.set_models, "endpoint": self.set_endpoint, "current": self.show_current, "installed": self.manage_installed,
                  "ctx": lambda: self.cmd_ollama_ctx(""), "perm": lambda: self.cmd_permissions(""), "about": lambda: self.cmd_about("")}[v]
            await fn()

    async def set_endpoint(self):
        t = await self._ask("Ollama endpoint", default=self.ollama.get("endpoint", "http://localhost:11434"))
        if not t:
            return
        try:
            await self.api.put("/api/settings", {"ollama_url": t})
        except ApiError as e:
            self.c.print(Text(f"  {e}", style=ERR))
            return
        await self.refresh()
        self.c.print(Text.assemble(("  ✓ ", OK), "endpoint saved · ", ("connected", OK) if self.ollama.get("connected") else ("not connected", ERR)))

    async def show_current(self):
        if not self.model:
            self.c.print(Text("  No model selected.", style=DIM))
            return
        if self.model == "auto":
            plan = await self.api.get("/api/auto/plan")
            self.c.print(Text.assemble(("  Auto mode", f"bold {PURPLE}"), f" · {plan.get('chat_models', 0)} chat models available"))
            for role, r in (plan.get("roles") or {}).items():
                self.c.print(Text.assemble((f"  {role:<6}", DIM), (r["name"] if r else "none", "bold"), (f"  {clip((r or {}).get('why', ''), 90)}" if r else "", DIM)))
            for w in plan.get("warnings", []):
                self.c.print(Text("  ⚠ " + clip(w, 160), style=WARN))
            return
        meta = await self.api.get("/api/models/info", name=self.model, num_ctx=self.ctx)
        info = next((m for m in self.installed if m["name"] == self.model), {})
        t = Table.grid(padding=(0, 3))
        t.add_column(style=DIM)
        t.add_column()
        for k, v in [("Model", self.model), ("Size", fmt_bytes(info.get("size"))), ("Parameters", info.get("parameter_size") or "?"),
                     ("Quantization", info.get("quantization") or "?"), ("Native context", fmt_num(meta.get("context_length"))),
                     ("Session context", f"{self.ctx:,}"), ("Tool calling", {True: "yes (native)", False: "no (text fallback)", None: "unknown"}[meta.get("tools_supported")]),
                     ("Capabilities", ", ".join(meta.get("capabilities") or []) or "?")]:
            t.add_row(k, str(v))
        self.c.print(t)
        w = (meta.get("context_check") or {}).get("warning")
        if w:
            self.c.print(Text("  ⚠ " + w, style=WARN))

    async def set_models(self):
        while True:
            v = await self._choose("Models", [Option("Use an installed model", "use", "same as /ollama-model"), Option("Download a model", "dl", "browse the Zeus-Agent guide by family"),
                                         Option("Manage installed models", "manage", "use · unload · uninstall"), Option("Auto mode", "auto", "what Auto would pick right now")])
            if not v:
                return
            if v == "use":
                await self.cmd_ollama_model("")
            elif v == "dl":
                await self.download_flow()
            elif v == "manage":
                await self.manage_installed()
            else:
                await self.set_model("auto")
                await self.show_current()

    async def manage_installed(self):
        while True:
            await self.refresh()
            ov = await self.api.get("/api/ollama/overview")
            opts = installed_options(self.installed, ov.get("loaded"), with_auto=False, chat_only=False)
            if not [o for o in opts if o.group is None]:
                self.c.print(Text("  No models installed.", style=DIM))
                return
            name = await self._choose("Installed models  (Enter to manage)", opts, current=self.model, searchable=True)
            if not name:
                return
            m = next((x for x in self.installed if x["name"] == name), {})
            loaded = any(norm(l["name"]) == norm(name) for l in ov.get("loaded", []))
            acts = [Option("Use this model", "use", disabled=group_of(name) == "embeddings")]
            if loaded:
                acts.append(Option("Unload from memory", "unload"))
            acts += [Option(f"Uninstall ({fmt_bytes(m.get('size'))})", "rm"), Option("Back", None)]
            a = await self._choose(name, acts)
            try:
                if a == "use":
                    await self.set_model(name)
                elif a == "unload":
                    await self.api.post("/api/ollama/unload", {"model": name})
                    self.c.print(Text(f"  ✓ {name} unloaded", style=OK))
                elif a == "rm" and await self._confirm(f"Delete {name} ({fmt_bytes(m.get('size'))}) from disk?"):
                    await self.api.post("/api/ollama/delete", {"model": name})
                    self.c.print(Text(f"  ✓ {name} uninstalled", style=OK))
                    if self.model == name:
                        self.model = "auto"
            except ApiError as e:
                self.c.print(Text(f"  ✗ {e}", style=ERR))

    async def download_flow(self):
        cat = await self.api.get("/api/ollama/catalog")
        have = {m["name"] for m in self.installed}
        m = await self._choose("Download a model  (type to search)", catalog_options(cat["models"], have), searchable=True, page=14)
        if not m:
            return
        self.c.print(Text.assemble((f"  {m['title']}", f"bold {PURPLE}"), (f" · {m['maker']}", DIM)))
        self.c.print(Text("  " + clip(m.get("about") or m["description"], 400), style=DIM))
        self.c.print(Text("  Best for: " + m["best_for"], style=DIM))
        for e in (m.get("examples") or [])[:2]:
            self.c.print(Text.assemble(("   e.g. ", DIM), (clip(e["prompt"], 100), "italic")))
        tag = await self._choose("Choose a size", [Option(t["tag"].split(":")[-1], t["tag"], f"{t['size_gb']} GB download" + ("  (default)" if t["latest"] else "")) for t in m["tags"]],
                           current=next((t["tag"] for t in m["tags"] if t["latest"]), None))
        if tag:
            await self.pull(tag)

    async def pull(self, tag: str):
        try:
            row = await self.api.post("/api/ollama/pull", {"model": tag})
        except ApiError as e:
            self.c.print(Text(f"  ✗ {e}", style=ERR))
            return
        from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
        prog = Progress(TextColumn(f"  [bold {PURPLE}]{tag}[/]"), BarColumn(complete_style=PURPLE), DownloadColumn(), TransferSpeedColumn(),
                        TextColumn("[grey62]{task.fields[detail]}"), console=self.c, transient=True)
        task = prog.add_task("dl", total=None, detail="starting")
        loop = asyncio.get_running_loop()
        task_cancel = False
        with prog:
            try:
                while True:
                    await asyncio.sleep(0.7)
                    ov = await self.api.get("/api/ollama/overview")
                    d = next((x for x in ov["downloads"] if x["id"] == row["id"]), None)
                    if not d:
                        break
                    prog.update(task, total=d["total"] or None, completed=d["completed"], detail=clip(d["detail"], 30))
                    if d["status"] in ("completed", "failed", "cancelled", "interrupted"):
                        break
            except (KeyboardInterrupt, asyncio.CancelledError):
                await self.api.post(f"/api/ollama/downloads/{row['id']}/cancel")
                task_cancel = True
        if task_cancel:
            self.c.print(Text(f"  ■ download of {tag} cancelled", style=WARN))
        elif d and d["status"] == "completed":
            self.c.print(Text(f"  ✓ {tag} downloaded", style=OK))
            await self.refresh()
        else:
            self.c.print(Text(f"  ✗ {tag}: {(d or {}).get('error') or 'download did not finish'}", style=ERR))

    # ------------------------------------------------------------------ prompts / runs
    async def cmd_mission(self, arg):
        if not arg:
            self.c.print(Text("  Usage: /mission <file.md|.docx|.pdf|...> [extra instructions]", style=DIM))
            return
        path_s, _, note = arg.partition(" ") if not arg.startswith(("'", '"')) else (shlex.split(arg)[0], "", " ".join(shlex.split(arg)[1:]))
        f = resolve_dir(path_s, self.cwd)
        if not f.is_file():
            self.c.print(Text(f"  no such file: {f}", style=ERR))
            return
        if f.suffix.lower() in (".md", ".markdown"):
            name, content = f.name, f.read_text(errors="replace")
        else:
            self.c.print(Text(f"  converting {f.name} with MarkItDown…", style=DIM))
            try:
                r = await self.api.req("POST", "/api/convert", params={"filename": f.name}, content=f.read_bytes())
            except ApiError as e:
                self.c.print(Text(f"  ✗ {e}", style=ERR))
                return
            name, content = r["name"], r["markdown"]
        await self.run({"type": "mission", "name": name, "content": content, "note": note.strip(), "max_rounds": 5, "verifier": "same"})

    async def send(self, text: str):
        await self.run({"type": "prompt", "content": text})

    async def run(self, msg: dict):
        if not self.model:
            self.c.print(Text("  No model selected. Run /ollama-model or /settings → Models.", style=ERR))
            return
        if not self.ollama.get("connected"):
            await self.refresh()
            if not self.ollama.get("connected"):
                self.c.print(Text(f"  Ollama is not reachable ({self.ollama.get('error', '')}). Start it with `ollama serve` or fix /settings → Ollama endpoint.", style=ERR))
                return
        s = await self.ensure_session()
        self.r = Renderer(self.c, self.ui)
        self.r.wait()
        self.working = True
        try:
            async for ev in self.api.events(s["id"], msg):
                if ev.get("type") == "state":
                    for p in ev.get("pending", []):
                        await self.approve(p)
                    continue
                if ev.get("type") == "permission_request":
                    self.r.quiet()
                    await self.approve(ev)
                    self.r.wait()
                    continue
                self.r.handle(ev)
        except ApiError as e:
            self.r.quiet()
            self.c.print(Text(f"✗ {e}", style=ERR))
        except Exception as e:
            self.r.quiet()
            self.c.print(Text(f"✗ connection to the backend was lost: {type(e).__name__}", style=ERR))
        finally:
            self.working = False
            self.r.quiet()
            self.r._end_stream()
            if self.r.answer:
                self.last_copy = "\n\n".join(self.r.answer)
            elif self.r.last_output:
                self.last_copy = self.r.last_output
        await self.refresh_session()

    async def refresh_session(self):
        try:
            sess = await self.api.get("/api/sessions", project_id=self.project["id"])
            self.session = next((x for x in sess if x["id"] == self.session["id"]), self.session)
        except ApiError:
            pass

    async def approve(self, ev: dict):
        self.c.print(permission_panel(ev))
        v = await self._choose("Allow this?", [Option("Yes", "once", "just this once"), Option("Yes, for this session", "session", "don't ask again for this kind of action"),
                                          Option("No", "reject", "tell Zeus-Agent it was denied")], current="once", hint="↑/↓ · Enter · Esc = No")
        await self.api.respond(ev["id"], v or "reject")
        self.c.print(Text("  ✓ allowed" if v in ("once", "session") else "  ✗ denied", style=OK if v in ("once", "session") else ERR))

    async def run_shell(self, cmd: str):
        """Run `cmd` in the project folder; the output is shown live, then as one box with the exit status."""
        cmd = cmd.strip()
        if not cmd:
            self.c.print(Text("  Usage: /shell <command>   e.g. /shell ls -la", style=DIM))
            return
        if re.match(r"^cd(\s|$)", cmd):
            self.c.print(Text("  Each command runs in its own shell, so cd does not stick. Use /project <folder> to change the project folder.", style=WARN))
            return
        cwd = self.project["path"] if self.project else str(self.cwd)
        env = {**os.environ, "PAGER": "cat", "GIT_PAGER": "cat", "LESS": "-FRX", "NO_COLOR": "1"}
        try:
            proc = await asyncio.create_subprocess_shell(cmd, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.STDOUT, start_new_session=True, limit=2**20)
        except OSError as e:
            self.c.print(Text(f"  ✗ could not start: {e}", style=ERR))
            return
        self.shell_proc = proc
        self.working = True
        lines: list[str] = []
        status = "done"
        t0 = time.time()
        if self.ui:
            self.ui.set_live(preview_text(cmd, lines), glyph="")

        async def pump():
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    return
                lines.append(raw.decode("utf-8", "replace").rstrip("\r\n"))
                del lines[:-5000]
                if self.ui:
                    self.ui.set_live(preview_text(cmd, [clean_output(l) for l in lines[-8:]]), glyph="")

        try:
            await asyncio.wait_for(pump(), timeout=SHELL_TIMEOUT)
            await proc.wait()
        except asyncio.TimeoutError:
            status = "timeout"
            self._kill_shell()
        except asyncio.CancelledError:
            self._kill_shell()
            raise
        finally:
            self.working = False
            self.shell_proc = None
            if self.ui:
                self.ui.set_live("")
        if self._shell_stopped:
            status, self._shell_stopped = "stopped", False
        out = "\n".join(lines)
        self.last_copy = clean_output(out)
        self.c.print(output_card(cmd, out, exit_code=proc.returncode, duration=time.time() - t0, status=status, cwd=cwd))

    _shell_stopped = False

    def _kill_shell(self):
        p = self.shell_proc
        if p and p.returncode is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    # ------------------------------------------------------------------ input handling (pinned box)
    def submit(self, text: str):
        if self.working and self.queue is not None:
            self.c.print(Text(f"  ⏳ queued until Zeus-Agent is done: {clip(text, 70)}", style=DIM))
        self.queue.put_nowait(text)

    def interrupt(self):
        """Ctrl+C while something is running: stop the shell command or the agent run."""
        if self.shell_proc is not None and self.shell_proc.returncode is None:
            self._shell_stopped = True
            self.c.print(Text("  stopping…", style=WARN))
            self._kill_shell()
        elif self.working:
            self.c.print(Text("  stopping…", style=WARN))
            asyncio.ensure_future(self.api.stop())

    def on_ctrl_c(self, buf):
        if self.working:
            self.interrupt()
        elif buf.text:
            buf.reset()
        else:
            self.c.print(Text("  (Ctrl+D or /exit to leave)", style=DIM))

    def on_eof(self):
        self.should_exit = True
        self.interrupt()
        if self.ui:
            self.ui.exit()

    async def worker(self):
        while not self.should_exit:
            text = (await self.queue.get()).strip()
            if not text:
                continue
            self.echo(text)
            try:
                if text.startswith("/"):
                    await self.run_command(text)
                elif text.startswith("!"):
                    await self.run_shell(text[1:])
                elif text in ("exit", "quit"):
                    self.should_exit = True
                else:
                    await self.send(text)
            except ApiError as e:
                self.c.print(Text(f"  ✗ {e}", style=ERR))
            except Exception as e:  # never let one bad command end the session
                self.c.print(Text(f"  ✗ {type(e).__name__}: {e}", style=ERR))
        if self.ui:
            self.ui.exit()

    # ------------------------------------------------------------------ main loop
    async def loop(self):
        hist = Path(os.environ.get("ZEUS_DATA_DIR", Path.home() / ".local" / "share" / "zeus"))
        hist.mkdir(parents=True, exist_ok=True)
        self.queue = asyncio.Queue()
        self.ui = FullUI(hist / "cli_history", SlashCompleter(), self.toolbar, on_submit=self.submit, on_ctrl_c=self.on_ctrl_c, on_eof=self.on_eof,
                         placeholder="Ask Zeus-Agent to build, fix or explain code…  (type / for commands)")
        real_console, self.c = self.c, self.ui.make_console()      # from here on everything is printed into the message window
        self.show_banner()
        if self.resumed:
            src = {"gui": "the desktop app", "cli": "the terminal"}.get(self.resumed.get("client") or "", None)
            self.c.print(Text.assemble(("  ↻ ", f"bold {PURPLE}"), "resumed ", (f"'{clip(self.resumed.get('title') or 'your last conversation', 50)}'", "bold"),
                                       (f" from {src}" if src else "", DIM), (f" · {en.LABEL.get(self.engine, self.engine)} · {self.model or 'no model'}", DIM)))
            if self.session:
                try:
                    for m in (await self.api.get(f"/api/sessions/{self.session['id']}/messages"))[-4:]:
                        who = "you" if m.get("role") == "user" else "zeus" if m.get("role") == "assistant" else None
                        if who and m.get("content"):
                            self.c.print(Text.assemble((f"    {who}: ", f"bold {PURPLE}" if who == "zeus" else "bold"), (clip(m["content"], 150), DIM)))
                except ApiError:
                    pass
        if not self.engine_ok:
            e = self.engines.get(self.engine) or {}
            if self.engine == "llamacpp":
                self.c.print(Text(f"  llama.cpp is not available ({e.get('error') or 'llama-server not found'}). Run /engine → Help me choose, or /engine ollama.", style=WARN))
            else:
                self.c.print(Text(f"  Ollama is not connected ({self.ollama.get('error', 'unknown')}). Start it with `ollama serve`, or set the endpoint in /settings.", style=WARN))
        elif not self.installed:
            self.c.print(Text("  No models available yet. " + ("Open /settings → Models → Download a model." if self.engine == "ollama" else "Use /llamacpp to add a model folder or download a model."), style=WARN))
        app_task = asyncio.create_task(self.ui.run())
        work_task = asyncio.create_task(self.worker())
        await asyncio.wait({app_task, work_task}, return_when=asyncio.FIRST_COMPLETED)
        self.should_exit = True
        self.ui.exit()
        for t in (work_task, app_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(work_task, app_task, return_exceptions=True)
        self.ui, self.c = None, real_console
        self.c.print(Text("  ⚡ goodbye", style=f"bold {PURPLE}"))

    def echo(self, text: str):
        """What the user sent, as a shaded bubble in the message window."""
        if self.ui:
            self.ui.begin_turn(text.splitlines()[0] if text.splitlines() else text)
        self.c.print(user_card(text))


# ---------------------------------------------------------------------- entry point
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zeus --cli", description="Zeus-Agent in the terminal: a local coding agent powered by Ollama or llama.cpp.")
    p.add_argument("path", nargs="?", help="project folder (default: the current directory)")
    p.add_argument("-m", "--model", help="model name (Ollama: 'qwen3:8b' or 'auto'; llama.cpp: the GGUF file name without .gguf)")
    p.add_argument("--engine", choices=["ollama", "llamacpp"], help="what runs the models (default: the one you used last)")
    p.add_argument("--ctx", type=parse_ctx_arg, help="context window, e.g. 32768 or 32k")
    p.add_argument("--permission-mode", choices=["read_only", "approval", "trusted"], help="what Zeus-Agent may do")
    p.add_argument("-c", "--continue", dest="resume", action="store_true", help="resume the conversation you had last, in the desktop app or here (project, engine, model and context included); with a folder: its latest conversation")
    p.add_argument("-p", "--print", dest="print_prompt", metavar="PROMPT", help="run one prompt non-interactively, print the answer and exit")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=argparse.SUPPRESS)
    p.add_argument("-V", "--version", action="version", version=f"Zeus-Agent {config.VERSION}")
    return p


def parse_ctx_arg(s: str) -> int:
    v = parse_ctx(s)
    if not v:
        raise argparse.ArgumentTypeError("expected a number like 32768 or 32k")
    return v


async def amain(args) -> int:
    console = Console(theme=THEME)
    proc = None
    api = None
    try:
        try:
            proc = start_backend(args.port)
        except ApiError as e:
            console.print(Text(f"✗ {e}", style=ERR))
            return 1
        api = Api(args.port)
        cwd = resolve_dir(args.path) if args.path else Path.cwd()
        cli = Cli(api, console, cwd, model=args.model, ctx=args.ctx, perm=args.permission_mode, resume=args.resume, engine=args.engine,
                  explicit_path=bool(args.path))
        try:
            await cli.start()
        except ApiError as e:
            console.print(Text(f"✗ {e}", style=ERR))
            return 1
        if args.print_prompt:
            return await print_mode(cli, args.print_prompt)
        await cli.loop()
        return 0
    finally:
        if api:
            await api.close()
        stop_backend(proc)


async def print_mode(cli: Cli, prompt: str) -> int:
    """Non-interactive: run one prompt, stream the answer, deny anything that would need approval."""
    async def deny(ev):
        cli.c.print(Text(f"  permission denied (non-interactive): {ev.get('action')} {ev.get('target')}. Re-run with --permission-mode trusted.", style=WARN))
        await cli.api.respond(ev["id"], "reject")
    cli.approve = deny  # type: ignore[method-assign]
    await cli.send(prompt)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 130
