"""The engines Zeus-Agent can run models with (Ollama and llama.cpp) and the choice between them.

* `Engines` holds both providers and the default engine (the `engine` setting, remembered in ~/.zeus/settings.json).
* Each session also stores its own engine, so a conversation always resumes on the engine it was started with.
* `ProviderProxy` is what the agent loop holds as "the provider": every attribute lookup is forwarded to the engine of the
  conversation that is running right now (a ContextVar set when a run starts), so switching engines needs no restart and two
  sessions may use different engines at the same time.
"""
from __future__ import annotations

from contextvars import ContextVar

from ..core import config

ENGINES = ("ollama", "llamacpp")
LABELS = {"ollama": "Ollama", "llamacpp": "llama.cpp"}
current_engine: ContextVar[str | None] = ContextVar("zeus_engine", default=None)


def valid_engine(name: str | None) -> str | None:
    return name if name in ENGINES else None


class Engines:
    def __init__(self, db, ollama, llama):
        self.db = db
        self.ollama = ollama
        self.llama = llama

    def default(self) -> str:
        return valid_engine(self.db.get_setting("engine")) or "ollama"

    def set_default(self, name: str) -> None:
        if not valid_engine(name):
            raise ValueError(f"unknown engine '{name}'")
        self.db.set_setting("engine", name)

    def get(self, name: str | None = None):
        n = valid_engine(name) or valid_engine(current_engine.get()) or self.default()
        return self.llama if n == "llamacpp" else self.ollama

    def name_of(self, session: dict | None) -> str:
        return valid_engine((session or {}).get("engine")) or self.default()

    def last_model(self, engine: str) -> str | None:
        """The model last used with this engine (so switching back restores it)."""
        return self.db.get_setting(f"model_{engine}") or None

    def remember_model(self, engine: str, model: str | None) -> None:
        if model and valid_engine(engine):
            self.db.set_setting(f"model_{engine}", model)


class ProviderProxy:
    """Looks like a provider; forwards to the engine of the conversation that is running (see module doc)."""

    def __init__(self, engines: Engines):
        object.__setattr__(self, "_engines", engines)

    def __getattr__(self, item):
        return getattr(self._engines.get(), item)


def recommend(ollama: dict, llama: dict, models: dict, hardware: dict | None = None) -> dict:
    """Which engine to suggest, and why. Inputs are the engines' status dicts and how many models each has. Pure function."""
    o_ok, l_ok = bool(ollama.get("connected")), bool(llama.get("connected"))
    on, ln = int(models.get("ollama", 0)), int(models.get("llamacpp", 0))
    if o_ok and not l_ok:
        return {"engine": "ollama", "reason": "Ollama is running and llama.cpp is not installed. Ollama is the simplest start: it downloads models for you."}
    if l_ok and not o_ok:
        return {"engine": "llamacpp", "reason": "llama.cpp is installed and Ollama is not running."}
    if not o_ok and not l_ok:
        return {"engine": "ollama", "reason": "Neither engine was found. Ollama is the easiest to install; llama.cpp gives more control once you want it."}
    if ln > 0 and on == 0:
        return {"engine": "llamacpp", "reason": f"You have {ln} GGUF model{'s' if ln != 1 else ''} for llama.cpp and none installed in Ollama."}
    if on > 0 and ln == 0:
        return {"engine": "ollama", "reason": f"You have {on} model{'s' if on != 1 else ''} installed in Ollama and no GGUF files for llama.cpp yet."}
    return {"engine": "ollama", "reason": "Both engines are ready. Ollama is the easy default; switch to llama.cpp when you want to tune GPU layers, KV cache or run a specific GGUF file."}


# Shown by the "Help me choose" dialog (desktop) and `/engine` (terminal): one comparison, one source.
HELP_ROWS = [
    {"topic": "What it is", "ollama": "A friendly app that downloads and runs models for you.", "llamacpp": "The engine underneath many tools. You run model files (GGUF) yourself."},
    {"topic": "Setup", "ollama": "Install it, then pull a model. Zeus-Agent finds it by itself.", "llamacpp": "Install llama.cpp, tell Zeus-Agent where llama-server is, and point it at a folder of .gguf files (or download one)."},
    {"topic": "Models", "ollama": "A library with one-line downloads (ollama.com/library).", "llamacpp": "Any GGUF file: from Hugging Face, from your disk, or one you converted yourself."},
    {"topic": "Control", "ollama": "Sensible defaults; few knobs.", "llamacpp": "Full control: GPU layers, context size, KV-cache quantization, threads, flash attention, any extra flag."},
    {"topic": "Memory", "ollama": "Loads and unloads several models for you.", "llamacpp": "One model at a time, loaded when you choose it and freed when you switch or quit."},
    {"topic": "New models", "ollama": "Appear once Ollama supports them.", "llamacpp": "New llama.cpp features and models usually arrive here first."},
    {"topic": "Tool calling", "ollama": "Built in for models marked tools.", "llamacpp": "Uses the model's own chat template; works when that template supports tools."},
    {"topic": "Auto mode", "ollama": "Available: picks a coding or reasoning model per prompt.", "llamacpp": "Not available: one model runs at a time, so you choose it."},
    {"topic": "Best for", "ollama": "Getting started and trying many models.", "llamacpp": "Tuning for your GPU, a specific GGUF file, or a model that is not on Ollama."},
]
HELP_WHEN = {
    "ollama": ["You are new to local models.", "You want to try many models quickly.", "You like Auto mode picking a model for each prompt."],
    "llamacpp": ["You already have GGUF files, or want one from Hugging Face.", "You want to tune GPU layers, context or KV-cache memory.", "You need a model or feature Ollama does not offer yet."],
}
LLAMACPP_INSTALL = [
    "Download a build for your hardware from github.com/ggml-org/llama.cpp/releases (CUDA for NVIDIA GPUs, Vulkan for most other GPUs, or the plain build for CPU only), or compile it yourself.",
    "Unpack it somewhere permanent and note the full path of the llama-server file inside.",
    "Open the llama.cpp settings in Zeus-Agent and enter that path under \"llama-server\". Zeus-Agent also finds it by itself when it is on your PATH or unpacked or built in your Downloads, Documents, /opt or another folder under your home; use \"Search again\" after installing.",
    "Add a model: put a .gguf file in a folder you list under \"Model folders\", or use \"Download from Hugging Face\".",
]


def help_info() -> dict:
    return {"rows": HELP_ROWS, "when": HELP_WHEN, "install": LLAMACPP_INSTALL}


def engine_info(engines: Engines) -> dict:
    return {"default": engines.default(), "labels": LABELS, "data_dir": str(config.DATA_DIR)}
