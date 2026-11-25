# Zeus-Agent release notes

## 0.2.2: UNRELEASED, llama.cpp engine, project memory, rolling summary, command menu, model benchmarks (2026-11-23)

### New
- **Download and uninstall llama.cpp models, and reuse Ollama's.** The llama.cpp window's Download tab shows the most downloaded GGUF models on Hugging Face at once, searches as you type, and for every file says how it would run on your computer (GPU, GPU plus RAM, CPU only, too large, with an estimated speed) and suggests the best one. Downloads show progress and can be cancelled. Each model in the list says where it came from, and the models Zeus-Agent downloaded (or that sit in Hugging Face's or llama.cpp's cache) can be uninstalled after a confirmation that names the files and the space freed; models in your own folders are never deleted. In the terminal: `/llamacpp` → Download a model / Uninstall a model.
- **Models Ollama already downloaded work with llama.cpp, with no second download.** They are listed as `ollama/<name>` (marked "from Ollama · shared"), run directly from Ollama's own file (read only: nothing is copied or changed, Ollama keeps working), and are removed in Ollama (`ollama rm`), never by Zeus-Agent. A setting turns the list off.
- **Reset to a fresh installation.** Settings → Reset Zeus-Agent… (or `/reset`, also in the terminal) lists what Zeus-Agent has saved with sizes and removes only what you choose, after you type `RESET`: conversations and settings, each project's `.zeus` history and memory, the `~/.zeus` copies, logs, and optionally downloaded GGUF models. Your project files, Ollama and its models are never touched.
- **Project memory.** Every project has a short notes file (`.zeus/memory.md`) that every conversation starts with, so a new session no longer forgets the stack, conventions and your preferences. Edit it in the app (`/memory`), by hand, or ask Zeus-Agent to "remember" something (it asks for approval first). Private to your computer and never committed.
- **Rolling summary.** Long conversations are summarised automatically at 75% of the context window: the older part becomes a short structured summary and only that plus the newest messages are sent, so the model keeps the thread instead of silently losing the start. `/compact`, `/summary`, `/context` and a context meter beside the prompt box. The full transcript is always kept.
- **Commands in the desktop prompt box.** Type `/` for a menu of commands (↑↓, Tab, Enter) that open the matching window or show their result in the conversation: `/context`, `/compact`, `/memory`, `/remember`, `/engine`, `/best`, `/permissions`, `/help` and more. The same memory and context commands exist in the terminal.
- The prompt box no longer assumes Ollama: it is enabled when the conversation's own engine (Ollama or llama.cpp) is available.
- **Best models for your hardware.** The Models window has a new view with Arena's human-vote rankings for coding, agentic coding, agents, chat, vision and documents. Zeus-Agent reads your GPU and RAM and tells you, for every model, whether it runs on the GPU, spills into RAM, runs on the CPU or is too large, with an estimated speed and a table of every quantization (size, where it runs, quality loss). It picks the best model that is comfortable to use, the highest-quality one that runs slowly, and the matching `ollama pull` command. Each model family's page shows its scores too. Terminal: `/best`. Source: Arena (CC BY 4.0) via freellmarena.com; a snapshot ships with the app and Refresh fetches the newest.
- **End-to-end test agent** (`scripts/e2e/`): drives the real GUI in Chromium and the real terminal app in a pseudo-terminal, including the GUI-to-CLI `--continue` hand-over, and gates `build-deb.sh`.
- **Engine dropdown (desktop and terminal)** before the model dropdown: Ollama or llama.cpp, with **Help me choose…** (comparison, recommendation for your machine, llama.cpp setup steps). Each conversation remembers its engine.
- **llama.cpp window:** models found in your folders (GGUF details, tools/thinking badges), Use/Load/Unload, per-model and default settings (context, GPU layers, threads, flash attention, KV-cache types, extra flags), `llama-server` path, model folders, Hugging Face downloads with progress, server log. Terminal: `/engine`, `/llamacpp`, `--engine`.
- **`~/.zeus/state.json`** records the project, conversation, engine, model and context you were using, from either client. `zeus --cli --continue` resumes the conversation you had in the desktop app.

### Changed
- **`sudo apt remove zeus` now cleans up after itself.** It closes Zeus-Agent (and the llama-server it started, which used to keep running from deleted files and write its data again), then removes, from every user's home folder, the settings, conversation history, logs and desktop window data. A project's own `.zeus` folder (saved conversations and project memory), your project files, Ollama and its models are never touched; downloaded llama.cpp models are kept by `remove` and deleted by `apt purge`. Create `~/.zeus/keep-data` to opt out; upgrades and reinstalls remove nothing.

### Fixed
- **"HI" made small models call tools and loop.** A model such as Gemma 1B answered a greeting with `list_files`, repeated it on the empty folder, and the run ended with "the model kept repeating the same action". Now: messages that are only conversation (hi, thanks, how are you, what can you do) are answered without tools and with a short chat prompt that does not list any tool; a model that repeats a tool call is first told what the tool answered and asked to take another step or answer; and before giving up it gets one last turn that must answer in plain words. The error appears only if even that fails.
- **Choosing a model with no project open did nothing.** On a fresh install or right after a reset, picking a model in the dropdown was silently ignored. The choice is now remembered for your first conversation, the dropdown shows it, and a message says so; a new conversation without a project explains that a project is needed.
- **llama.cpp models with strict chat templates (Gemma and others) failed with "Conversation roles must alternate".** For models without native tool calling the history is now sent as plain alternating turns (a tool call as the JSON the model was taught, a tool result as a user turn), and if a template still refuses, the request is retried once with the system prompt folded into the first turn.
- The sidebar and status bar showed "Ollama connected" while you were on llama.cpp; they now show the status of the engine in use.
- **llama.cpp is found wherever you installed it.** Zeus-Agent used to look only on PATH and five fixed folders, so a llama.cpp unpacked in Downloads or built in Documents showed "llama-server not found". It now searches your home folder, /opt and /usr/local (a few levels deep, within a time budget), accepts only real llama.cpp installs (an executable program with its libraries next to it; Ollama's own runner is skipped), shows what it found with version and GPU support, picks the build that can use your GPU (then the newest) automatically, lets you choose another or search again, and tells you when a path you typed is wrong.
- Links to llama.cpp, Hugging Face, FreeLLMArena and the Zeus-Agent project now open in your browser; only Ollama links did before ("Could not open the browser").


## 0.2.1: Zeus-Agent name, Ollama library in the Models window, detached launch and scrolling (2026-11-23)

### New
- **The Models window shows the whole Ollama library.** Models that are not in Zeus-Agent's built-in guide, for example `ornith`, are now listed under "Ollama library" (about 270 families from ollama.com, most popular first, refreshable) with their description, capabilities, parameter sizes, pull count, `ollama pull` command and a Download button for each version with its size and whether it fits your machine.
- **Search reaches ollama.com.** Typing in the Models search box also searches the Ollama library for official and community models (community models are marked and carry a warning). Only the text you type is sent. The list works offline from the last copy.
- Installed models are attached to their library family instead of appearing separately.

### Changed
- **`zeus` frees the terminal.** Typing `zeus` opens the app in its own session and returns the prompt at once. Closing the terminal no longer closes the app, and its output goes to `~/.local/share/zeus/zeus.log`. `zeus --foreground` keeps it attached; launching from the application menu is unchanged.
- **Scrolling while Zeus-Agent writes.** You can scroll up in the conversation, in a command's output and in a streaming code block, and the view stays where you put it. It follows the newest text again when you scroll back to the end, or press the new "Jump to latest" button.
- **The product is named Zeus-Agent** in the app, the terminal UI and the docs, and `zeus --cli` opens the terminal UI. The command is still `zeus`; `zeus -code` keeps working.
- Run, execute and launch now mean executing a file for the agent (it runs `./swap.out` instead of reading it).

### Download and upgrade
`zeus_0.2.1_amd64.deb` (about 107 MiB, Ubuntu/Debian amd64)  
SHA-256: `19ca559948b857560947681edef3df6f0df42a34658e2edd63e2905ecc9898ce`
```bash
sudo apt install --reinstall ./zeus_0.2.1_amd64.deb
```
Your projects, conversations and settings are kept.

### Docs
- New `docs/agentic/`: requirements, design, development, testing and agent orchestration for developing Zeus-Agent with a team of specialised agents.

## 0.2.0: terminal UI, model management and settings (2026-11-18)

The headline of this release is **`zeus --cli`**, a full terminal version of Zeus-Agent, plus a much better Models window
and a new Settings menu in the desktop app.

### New: `zeus --cli` (terminal UI)
- Run `zeus --cli` in any folder to open Zeus-Agent in your terminal. That folder is the project; `/project` changes it.
- Welcome screen with the Zeus-Agent thunder-bolt drawn as terminal pixels, with session and device information
  (model, context, permissions, project, Ollama, CPU, GPU/VRAM, RAM, OS) beside it.
- Full-screen chat layout: scrolling message window on top, the input box pinned to the bottom with a placeholder.
- Slash commands with a live menu: `/settings`, `/ollama-model`, `/ollama-ctx`, `/permissions`, `/project`,
  `/mission`, `/new`, `/resume`, `/changes`, `/usage`, `/status`, `/doctor`, `/about`, `/copy`, `/clear`, `/help`, `/exit`.
- **`/shell <command>`** (or `!command`): run a shell command in the project folder and see its output in a box with
  the exit status and time (green when it worked, red when it failed), with a live preview while it runs.
- **Organised output:** the model's reply is parsed as Markdown into a titled answer card, code blocks get their own
  box, tool-call JSON is hidden, reads and edits are one-line rows, and your messages are shaded bubbles.
- **Fold and unfold:** click the arrow row of a prompt to hide or show that prompt's output; `/fold` and `/unfold`
  do all of them at once.
- Inline approvals for file changes and commands (Yes / Yes for this session / No), shared permissions with the desktop app.
- Options: `-m`, `--ctx`, `--permission-mode`, `-c` (continue), `-p` (one prompt, print the answer, exit).
- Full reference with screenshots: [docs/COMMANDS.md](docs/COMMANDS.md).

### Improved: Models window and Settings
- **Models window** (Settings → Models): searchable, with collapsible families (Qwen, DeepSeek, Llama, Gemma…).
- **Details for every model:** what it is for, example prompts, and the latest details fetched live from ollama.com.
- **Uninstall** button next to Use, to remove models you no longer need (with a confirm step).
- **Settings drop-up menu** with Models, Ollama endpoint, Current model, Installed models and **About**
  (version, founder, GitHub, platform, CPU, GPU and VRAM, RAM and swap, read from the real machine).

### Fixed
- "Run `swap.out` and print the output" made small models `cat` the file instead of running it. The agent's
  instructions now say that run/execute/launch means executing the file (`./swap.out`), while "show" still reads it.
- Command output (from `/shell` and from the agent) is shown in boxes in the terminal app.

### Download
`zeus_0.2.0_amd64.deb` (about 107 MiB, Ubuntu/Debian amd64)  
SHA-256: `6d64063df1ed6c83b18b067250a6f191a2c3597748ae1b6c1780a8dc09ff60c6`

### Upgrade
```bash
sudo apt install --reinstall ./zeus_0.2.0_amd64.deb
```
Your projects, conversations and settings are kept. Installing needs no network.

### Known limits
- `zeus --cli` mouse clicks need a terminal with mouse reporting; hold Shift to select text, or use `/copy`.
- Small models (about 7B) can still pick the wrong tool now and then; a larger tool-calling model such as `qwen3-coder` is more reliable.
- Tested on Ubuntu 24.04 (amd64) with Ollama 0.34.

## 0.1.0: first release
Desktop app (Tauri 2 + React) with a local FastAPI backend and Ollama: projects, chat timeline, inline approval of every
write and command, missions from a Markdown spec, document attachments converted with MarkItDown, live GPU/VRAM/CPU
usage, session memory in `.zeus/` folders, and an offline-installable `.deb`.
