"""Curated catalog of well-known models in the Ollama library, with purposes and ZEUS-fit guidance.

This is a hand-maintained snapshot, not a live feed: sizes are approximate download sizes of the default
quantization (Q4) in GB, and capability flags are indicative. Installed models are always checked against Ollama's own
metadata (/api/show) when selected. Every entry links to https://ollama.com/library/<name> for the authoritative page.

fit (for the ZEUS coding agent, which needs reliable tool calling):
  best    - strong coding / agent model with native tool calls
  good    - works well for chat or reasoning, tool use is fair
  limited - usable but weak at multi-step tool use (tiny or reasoning-only models)
  no      - not a chat model (e.g. embeddings) - cannot drive the agent
"""
from __future__ import annotations

LIBRARY_URL = "https://ollama.com/library"

PURPOSES = {
    "coding": "Coding", "agent": "Agent / tools", "reasoning": "Reasoning", "chat": "General chat",
    "vision": "Vision", "embedding": "Embeddings", "small": "Small & fast", "multilingual": "Multilingual",
}


def _m(name, title, maker, desc, best_for, purposes, fit, tags, caps=(), ctx=None, note=None):
    """tags: list of (tag, approx_gb[, is_latest])."""
    return {
        "name": name, "title": title, "maker": maker, "description": desc, "best_for": best_for,
        "purposes": list(purposes), "capabilities": list(caps), "fit": fit, "context": ctx, "note": note,
        "url": f"{LIBRARY_URL}/{name}",
        "tags": [{"tag": f"{name}:{t[0]}", "size_gb": t[1], "latest": bool(len(t) > 2 and t[2])} for t in tags],
    }


CATALOG: list[dict] = [
    # ---------------- coding / agent
    _m("qwen3-coder", "Qwen3 Coder", "Alibaba", "Mixture-of-experts coding model built for agentic workflows and tool calling.",
       "Best all-round pick for ZEUS: multi-file edits, running commands, long agent runs.", ["coding", "agent"], "best",
       [("30b", 19, True)], ["tools"], "256K", "Needs ~20 GB of combined VRAM+RAM; runs partly on CPU on smaller GPUs."),
    _m("qwen2.5-coder", "Qwen2.5 Coder", "Alibaba", "Code-specialised Qwen2.5 in many sizes; strong at generation, repair and explanation.",
       "Great coding quality per GB. 7B/14B are the sweet spot for 8-16 GB GPUs.", ["coding", "agent", "small"], "best",
       [("1.5b", 1.0), ("3b", 1.9), ("7b", 4.7, True), ("14b", 9.0), ("32b", 20)], ["tools"], "32K"),
    _m("devstral", "Devstral", "Mistral AI", "Agentic coding model tuned for software-engineering tasks over many files.",
       "Repository-level tasks and tool-driven workflows.", ["coding", "agent"], "best", [("24b", 14, True)], ["tools"], "128K"),
    _m("deepseek-coder-v2", "DeepSeek Coder V2", "DeepSeek", "Mixture-of-experts coding model supporting 300+ languages.",
       "Broad language coverage and solid completions; weaker tool use than Qwen coders.", ["coding"], "good", [("16b", 8.9, True)], [], "160K"),
    _m("codellama", "Code Llama", "Meta", "Llama 2 fine-tuned for code generation and infilling.",
       "Older but light; fine for simple scripts and completions.", ["coding"], "limited", [("7b", 3.8, True), ("13b", 7.4), ("34b", 19)], [], "16K"),
    _m("starcoder2", "StarCoder2", "BigCode", "Open code model trained on 600+ languages.",
       "Code completion; not tuned for chat or tools.", ["coding"], "limited", [("3b", 1.7, True), ("7b", 4.0), ("15b", 9.1)], [], "16K"),
    _m("granite-code", "Granite Code", "IBM", "IBM's code models for generation, explanation and bug fixing.",
       "Permissively licensed coding help on modest hardware.", ["coding", "small"], "limited", [("3b", 2.0), ("8b", 4.6, True)], [], "128K"),
    # ---------------- general + reasoning
    _m("qwen3", "Qwen3", "Alibaba", "Hybrid thinking / non-thinking models from 0.6B to 32B with tool support.",
       "Strong general model for planning plus code; try 8B or 14B.", ["chat", "reasoning", "agent", "multilingual"], "best",
       [("0.6b", 0.5), ("1.7b", 1.4), ("4b", 2.5), ("8b", 5.2, True), ("14b", 9.3), ("30b", 19), ("32b", 20)], ["tools", "thinking"], "40K"),
    _m("deepseek-r1", "DeepSeek-R1", "DeepSeek", "Reasoning model that thinks step by step before answering (distilled sizes).",
       "Hard problems, math, debugging logic. Slower; tool calling is weaker than Qwen3.", ["reasoning"], "good",
       [("1.5b", 1.1), ("7b", 4.7), ("8b", 5.2, True), ("14b", 9.0), ("32b", 20), ("70b", 43)], ["thinking"], "128K"),
    _m("qwq", "QwQ", "Alibaba", "Qwen reasoning model; long chain-of-thought for hard problems.",
       "Complex reasoning where speed does not matter.", ["reasoning"], "good", [("32b", 20, True)], ["tools", "thinking"], "40K"),
    _m("gpt-oss", "gpt-oss", "OpenAI", "OpenAI's open-weight reasoning models with tool use and adjustable reasoning effort.",
       "Agentic tasks and reasoning; 20B fits 16 GB-class machines.", ["reasoning", "agent", "chat"], "best",
       [("20b", 14, True), ("120b", 65)], ["tools", "thinking"], "128K"),
    # ---------------- general chat
    _m("llama3.1", "Llama 3.1", "Meta", "Meta's flagship open models with solid tool calling and long context.",
       "General assistant and light coding; 8B is a good default.", ["chat", "agent", "multilingual"], "good",
       [("8b", 4.9, True), ("70b", 43)], ["tools"], "128K"),
    _m("llama3.2", "Llama 3.2", "Meta", "Compact Llama models for edge devices.",
       "Quick answers and summaries on CPU or small GPUs.", ["chat", "small"], "limited", [("1b", 1.3), ("3b", 2.0, True)], ["tools"], "128K"),
    _m("llama3.3", "Llama 3.3", "Meta", "70B model with quality close to much larger Llama models.",
       "High-quality general chat if you have 48 GB+ of memory.", ["chat", "agent"], "good", [("70b", 43, True)], ["tools"], "128K"),
    _m("qwen2.5", "Qwen2.5", "Alibaba", "General Qwen models, 0.5B-72B, strong multilingual and structured output.",
       "Multilingual chat, JSON output, summarising.", ["chat", "multilingual", "agent"], "good",
       [("0.5b", 0.4), ("1.5b", 1.0), ("3b", 1.9), ("7b", 4.7, True), ("14b", 9.0), ("32b", 20), ("72b", 47)], ["tools"], "32K"),
    _m("gemma3", "Gemma 3", "Google", "Google's multimodal models (text + images from 4B up) with large context.",
       "Writing, summarising and image questions; good quality for its size.", ["chat", "vision", "multilingual"], "good",
       [("1b", 0.8), ("4b", 3.3, True), ("12b", 8.1), ("27b", 17)], ["vision"], "128K", "No native tool calling; ZEUS falls back to text-format tool calls."),
    _m("gemma2", "Gemma 2", "Google", "Efficient Google models, strong for their size.",
       "Lightweight chat and writing.", ["chat", "small"], "limited", [("2b", 1.6), ("9b", 5.4, True), ("27b", 16)], [], "8K"),
    _m("mistral", "Mistral 7B", "Mistral AI", "Fast, capable 7B model with tool support.",
       "Everyday chat and light automation on modest GPUs.", ["chat", "agent", "small"], "good", [("7b", 4.1, True)], ["tools"], "32K"),
    _m("mistral-nemo", "Mistral NeMo", "Mistral AI / NVIDIA", "12B model with 128K context and tool support.",
       "Long documents and multilingual chat.", ["chat", "multilingual", "agent"], "good", [("12b", 7.1, True)], ["tools"], "128K"),
    _m("mistral-small", "Mistral Small", "Mistral AI", "24B model with strong instruction following and tool calling.",
       "High-quality assistant if you have ~16 GB+ of memory.", ["chat", "agent"], "good", [("24b", 14, True)], ["tools"], "128K"),
    _m("phi4", "Phi-4", "Microsoft", "14B model strong at reasoning and logic for its size.",
       "Compact reasoning and Q&A.", ["chat", "reasoning"], "good", [("14b", 9.1, True)], [], "16K"),
    _m("phi3", "Phi-3", "Microsoft", "Small, efficient models that punch above their weight.",
       "Low-memory chat and classification.", ["chat", "small"], "limited", [("mini", 2.2, True), ("medium", 7.9)], [], "128K"),
    # ---------------- small & fast
    _m("smollm2", "SmolLM2", "Hugging Face", "Tiny models (135M-1.7B) for very constrained devices.",
       "Quick tests and edge devices. Not suitable for agent work.", ["small"], "limited", [("135m", 0.3), ("360m", 0.7), ("1.7b", 1.8, True)], [], "8K"),
    _m("tinyllama", "TinyLlama", "TinyLlama", "1.1B model trained on 3T tokens.",
       "Smoke tests only.", ["small"], "limited", [("1.1b", 0.6, True)], [], "2K"),
    # ---------------- vision
    _m("llama3.2-vision", "Llama 3.2 Vision", "Meta", "Image + text model for describing and reasoning about pictures.",
       "Screenshots, diagrams, OCR-style questions.", ["vision", "chat"], "limited", [("11b", 7.8, True)], ["vision"], "128K"),
    _m("llava", "LLaVA", "LLaVA team", "Popular open vision-language model.",
       "Describing images and answering questions about them.", ["vision"], "limited", [("7b", 4.7, True), ("13b", 8.0)], ["vision"], "32K"),
    _m("qwen2.5vl", "Qwen2.5-VL", "Alibaba", "Vision-language model with strong document and chart understanding.",
       "Reading screenshots, UI mock-ups, scanned docs.", ["vision", "multilingual"], "limited", [("3b", 3.2), ("7b", 6.0, True)], ["vision"], "125K"),
    _m("minicpm-v", "MiniCPM-V", "OpenBMB", "Efficient multimodal model for on-device use.",
       "Image Q&A on modest hardware.", ["vision", "small"], "limited", [("8b", 5.5, True)], ["vision"], "32K"),
    # ---------------- embeddings
    _m("nomic-embed-text", "Nomic Embed Text", "Nomic AI", "High-quality text embedding model with long context.",
       "Semantic search / RAG (planned for a later ZEUS phase). Cannot chat.", ["embedding"], "no", [("latest", 0.27, True)], ["embedding"], "8K"),
    _m("mxbai-embed-large", "mxbai-embed-large", "Mixedbread", "State-of-the-art large English embedding model.",
       "Retrieval quality over speed. Cannot chat.", ["embedding"], "no", [("latest", 0.67, True)], ["embedding"], "512"),
    _m("bge-m3", "BGE-M3", "BAAI", "Multilingual, multi-granularity embedding model.",
       "Multilingual retrieval. Cannot chat.", ["embedding", "multilingual"], "no", [("latest", 1.2, True)], ["embedding"], "8K"),
    _m("all-minilm", "all-MiniLM", "Sentence-Transformers", "Very small, fast embedding model.",
       "Lightweight similarity search. Cannot chat.", ["embedding", "small"], "no", [("latest", 0.05, True)], ["embedding"], "256"),
]


# ---------------------------------------------------------------------------------------------------------------------
# Detailed write-ups and example prompts shown in the Models window. `about` explains what the model is and when to pick
# it; `examples` are prompts you can paste into ZEUS with a short note on what to expect. The live ollama.com details
# (see modelinfo.py) are shown next to these and may be newer.
ABOUT = {
    "qwen3-coder": "A 30B mixture-of-experts model (about 3B parameters active per token) trained specifically for agentic coding: reading a repository, planning edits, calling tools and fixing its own test failures. It keeps long sessions coherent thanks to a 256K context window. Because only a few experts run per token it is far faster than a dense 30B model, but the whole file still has to fit in memory (about 19 GB), so on smaller GPUs part of it runs on the CPU.",
    "qwen2.5-coder": "Alibaba's code-specialised Qwen2.5 family, available from 1.5B to 32B. It writes, explains and repairs code in dozens of languages and handles tool calls well. The 7B and 14B sizes give the best quality per GB of memory, which makes them the practical choice for laptops and 8-16 GB GPUs. Use the 32B if you have the memory and want the strongest code quality in this family.",
    "devstral": "Mistral's 24B model built with All Hands AI for software-engineering agents. It is tuned to explore a codebase, edit several files and run commands, rather than just answer coding questions. It is a good fit when the task is a change across a repository instead of a single function.",
    "deepseek-coder-v2": "A mixture-of-experts code model from DeepSeek that supports over 300 programming languages and long files. It writes solid completions and explanations, but its tool calling is less dependable than Qwen's coder models, so ZEUS may need its text-format fallback for multi-step runs.",
    "codellama": "Meta's code-tuned Llama 2. It is an older generation: good for short scripts, completions and explaining snippets, but it does not drive multi-step tool use reliably. Pick it only when you need something light and familiar.",
    "starcoder2": "A pure code-completion model trained on 600+ languages. It continues code well, but it is not an instruction-following chat model, so it is a poor choice for the ZEUS agent loop.",
    "granite-code": "IBM's Apache-licensed code models for generation, explanation and bug fixing. The 3B and 8B sizes run on modest hardware, which suits teams that need a permissive licence.",
    "qwen3": "A general model family with a switchable thinking mode: it can reason step by step for hard questions or answer directly for quick ones, and it supports tool calling at every size. 8B is a strong default for planning plus code on a single GPU; 14B and 32B are noticeably better at long plans. Sizes below 4B are fast but too weak for agent work.",
    "deepseek-r1": "A reasoning model that writes out a long chain of thought before it answers. The sizes on Ollama are distilled from the full model into Qwen and Llama bases. It is excellent for maths, logic puzzles and tracking down subtle bugs, but it is slow because it thinks first, and tool calling is weaker than Qwen3, so use it for thinking steps rather than driving the whole agent.",
    "qwq": "Alibaba's 32B reasoning model. Like DeepSeek-R1 it spends many tokens thinking, which pays off on hard problems and costs time on easy ones. Needs about 20 GB of memory.",
    "gpt-oss": "OpenAI's open-weight reasoning models. The 20B version fits 16 GB-class machines and supports tool use and a configurable reasoning effort (low, medium, high), so you can trade speed for depth. A strong all-rounder for agent tasks.",
    "llama3.1": "Meta's dependable general model with tool calling and a 128K context window. The 8B is a good default assistant on modest hardware; the 70B approaches large hosted models but needs about 43 GB.",
    "llama3.2": "Compact 1B and 3B Llama models for phones and laptops. Good for summaries, rewriting and short answers; too small to run multi-step agent work reliably.",
    "llama3.3": "A 70B model that matches the quality of much larger Llama models. Excellent general chat and instruction following if you have 48 GB or more of combined memory.",
    "qwen2.5": "A general-purpose multilingual family from 0.5B to 72B. It is especially good at following formats: JSON output, tables and structured summaries. A solid choice for non-coding text work in many languages.",
    "gemma3": "Google's multimodal family: from 4B up it accepts images as well as text, with a 128K context. It writes clearly and describes pictures well. It has no native tool calling, so ZEUS falls back to text-format tool calls, which is slower and less reliable.",
    "gemma2": "An efficient earlier Gemma. A good lightweight writer and chatter, with a short 8K context and no tool support.",
    "mistral": "A fast 7B model with tool support. It is a good everyday assistant on a modest GPU, and a reasonable first model to try for light automation.",
    "mistral-nemo": "A 12B model built with NVIDIA that handles 128K tokens of context and many languages. A good pick for working through long documents.",
    "mistral-small": "A 24B model with strong instruction following and dependable tool calling. It needs roughly 14 GB, and gives high-quality answers when a 7B model is not enough.",
    "phi4": "Microsoft's 14B model, trained heavily on reasoning-style data. It punches above its size on logic and Q&A, but its 16K context and lack of tool calling limit agent use.",
    "phi3": "Small Microsoft models (mini is about 3.8B) that perform well for their size. Useful for low-memory chat and classification.",
    "smollm2": "Tiny models from 135M to 1.7B parameters made for devices with very little memory. Good for experiments and quick transformations, not for agent work.",
    "tinyllama": "A 1.1B model that is mainly useful as a smoke test: it loads in seconds so you can check that Ollama and ZEUS are wired up correctly.",
    "llama3.2-vision": "Llama 3.2 with an image encoder. Give it a screenshot, chart or diagram and ask questions about it. It is better at describing and reasoning about pictures than reading dense text.",
    "llava": "A popular open vision-language model. It describes images and answers simple questions about them; it is older and less accurate on text in images than newer vision models.",
    "qwen2.5vl": "A vision-language model that is particularly good with documents, charts, tables and UI screenshots, and can locate things within an image. A strong choice for reading mock-ups and scanned pages.",
    "minicpm-v": "An efficient 8B multimodal model that runs on modest hardware and handles image Q&A and OCR-style tasks reasonably well.",
    "nomic-embed-text": "An embedding model: it turns text into vectors so that similar passages can be found by meaning. It cannot chat. ZEUS keeps it listed because semantic search over a project is planned; today it is only useful to other tools that talk to Ollama.",
    "mxbai-embed-large": "A large English embedding model that favours retrieval quality over speed. It cannot chat.",
    "bge-m3": "A multilingual embedding model that handles many languages and long passages. It cannot chat.",
    "all-minilm": "A very small, fast embedding model for lightweight similarity search. It cannot chat.",
}

EXAMPLE_SETS = {
    "agentic-coder": [
        {"prompt": "Add input validation to the signup form in this project and write tests for it. Run the tests and fix any failures.",
         "shows": "Reads the form and existing tests, edits several files, runs the test command, then iterates until it passes."},
        {"prompt": "Find why `npm run build` fails and fix it.",
         "shows": "Runs the build, reads the error, locates the file, patches it and re-runs to confirm."},
        {"prompt": "Create a small FastAPI app with a /health route and a Dockerfile, in a new folder called api/.",
         "shows": "Creates multiple files from scratch and saves them in your project."},
    ],
    "coder": [
        {"prompt": "Write a Python function that parses a CSV file and returns the total per category. Include a unit test.",
         "shows": "A complete function with a test you can save straight into the project."},
        {"prompt": "Explain what this function does and point out any bugs: <paste code>",
         "shows": "A line-by-line explanation and a list of likely issues with suggested fixes."},
        {"prompt": "Convert this JavaScript callback code to async/await.",
         "shows": "A refactor that keeps behaviour the same."},
    ],
    "reasoning": [
        {"prompt": "This function sometimes returns the wrong total when the list is empty. Work out why and propose the smallest fix.",
         "shows": "Walks through the edge cases before settling on a cause, then gives a minimal change."},
        {"prompt": "Plan the steps to migrate this app from SQLite to PostgreSQL without downtime.",
         "shows": "A reasoned, ordered plan that lists risks and how to check each step."},
        {"prompt": "Prove that the sum of two odd numbers is even.",
         "shows": "A step-by-step derivation, with the thinking shown before the final answer."},
    ],
    "chat": [
        {"prompt": "Summarise this README in five bullet points and list any setup steps I might miss.",
         "shows": "A short structured summary."},
        {"prompt": "Draft a polite email asking a teammate to review my pull request by Friday.",
         "shows": "A ready-to-send message in the tone you ask for."},
        {"prompt": "Explain how a hash map works as if I'm new to programming, with a tiny example.",
         "shows": "A plain-language explanation with a short code sample."},
    ],
    "vision": [
        {"prompt": "Describe this screenshot and list every UI element you can see. (attach an image)",
         "shows": "A structured description of the layout, text and controls."},
        {"prompt": "What does this chart say about the trend over the last quarter? (attach an image)",
         "shows": "A written reading of the chart with the main numbers called out."},
        {"prompt": "Turn this wireframe into HTML and CSS. (attach an image)",
         "shows": "A first-draft page that follows the layout in the picture."},
    ],
    "embedding": [
        {"prompt": "(Not used in chat.) POST /api/embed with {\"model\": \"<this model>\", \"input\": \"how do I reset my password?\"}",
         "shows": "Returns a vector. Comparing vectors finds the passages closest in meaning, which is how semantic search works."},
    ],
    "small": [
        {"prompt": "Rewrite this sentence more clearly: <paste text>",
         "shows": "A quick rewrite; fast even without a GPU."},
        {"prompt": "Classify this message as bug, feature request or question: <paste text>",
         "shows": "A one-word label, suitable for simple automation."},
        {"prompt": "Say hello in five languages.",
         "shows": "A quick smoke test that Ollama and ZEUS are connected."},
    ],
}

EXAMPLE_SET_OF = {
    "qwen3-coder": "agentic-coder", "devstral": "agentic-coder", "qwen2.5-coder": "agentic-coder", "gpt-oss": "agentic-coder",
    "deepseek-coder-v2": "coder", "codellama": "coder", "starcoder2": "coder", "granite-code": "coder",
    "deepseek-r1": "reasoning", "qwq": "reasoning", "phi4": "reasoning",
    "llama3.2-vision": "vision", "llava": "vision", "qwen2.5vl": "vision", "minicpm-v": "vision",
    "nomic-embed-text": "embedding", "mxbai-embed-large": "embedding", "bge-m3": "embedding", "all-minilm": "embedding",
    "smollm2": "small", "tinyllama": "small", "llama3.2": "small", "phi3": "small",
}

for _m_ in CATALOG:
    _m_["about"] = ABOUT.get(_m_["name"], _m_["description"])
    _m_["examples"] = EXAMPLE_SETS[EXAMPLE_SET_OF.get(_m_["name"], "chat")]


def purposes() -> dict:
    return PURPOSES


def normalize(name: str) -> str:
    """'qwen3' -> 'qwen3:latest'; keeps explicit tags and namespaces."""
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def tag_installed(tag: dict, family: str, installed: set[str]) -> bool:
    if tag["tag"] in installed or normalize(tag["tag"]) in installed:
        return True
    return bool(tag["latest"]) and f"{family}:latest" in installed
