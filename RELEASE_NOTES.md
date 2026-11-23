# Zeus-Agent release notes

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
