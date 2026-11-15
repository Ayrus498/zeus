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


def purposes() -> dict:
    return PURPOSES


def normalize(name: str) -> str:
    """'qwen3' -> 'qwen3:latest'; keeps explicit tags and namespaces."""
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def tag_installed(tag: dict, family: str, installed: set[str]) -> bool:
    if tag["tag"] in installed or normalize(tag["tag"]) in installed:
        return True
    return bool(tag["latest"]) and f"{family}:latest" in installed
