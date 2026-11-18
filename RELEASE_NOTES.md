# ZEUS release notes

## 0.2.0: terminal UI, model management and settings (2026-11-18)

The headline of this release is **`zeus -code`**, a full terminal version of ZEUS, plus a much better Models window
and a new Settings menu in the desktop app.

### New: `zeus -code` (terminal UI)
- Run `zeus -code` in any folder to open ZEUS in your terminal. That folder is the project; `/project` changes it.
- Welcome screen with the ZEUS thunder-bolt drawn as terminal pixels, with session and device information
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
- `zeus -code` mouse clicks need a terminal with mouse reporting; hold Shift to select text, or use `/copy`.
- Small models (about 7B) can still pick the wrong tool now and then; a larger tool-calling model such as `qwen3-coder` is more reliable.
- Tested on Ubuntu 24.04 (amd64) with Ollama 0.34.

## 0.1.0: first release
Desktop app (Tauri 2 + React) with a local FastAPI backend and Ollama: projects, chat timeline, inline approval of every
write and command, missions from a Markdown spec, document attachments converted with MarkItDown, live GPU/VRAM/CPU
usage, session memory in `.zeus/` folders, and an offline-installable `.deb`.
