const BY_LANG: Record<string, string> = {
  html: "index.html", htm: "index.html", css: "style.css", javascript: "script.js", js: "script.js", jsx: "App.jsx", typescript: "main.ts", ts: "main.ts", tsx: "App.tsx",
  python: "main.py", py: "main.py", c: "main.c", cpp: "main.cpp", "c++": "main.cpp", cs: "Program.cs", csharp: "Program.cs", java: "Main.java", go: "main.go",
  rust: "main.rs", rs: "main.rs", ruby: "main.rb", php: "index.php", bash: "script.sh", sh: "script.sh", shell: "script.sh", json: "data.json", yaml: "config.yaml",
  yml: "config.yaml", toml: "config.toml", markdown: "README.md", md: "README.md", sql: "query.sql", xml: "data.xml", dockerfile: "Dockerfile", makefile: "Makefile",
};

/** Suggest a file name for a code block from its language tag, falling back to sniffing the content. */
export function suggestFilename(lang: string | null, code: string): string {
  const l = (lang ?? "").toLowerCase();
  if (BY_LANG[l]) return BY_LANG[l];
  const head = code.trimStart().slice(0, 200).toLowerCase();
  if (head.startsWith("<!doctype html") || head.startsWith("<html")) return "index.html";
  if (head.startsWith("#!/bin/bash") || head.startsWith("#!/bin/sh")) return "script.sh";
  if (head.startsWith("#!/usr/bin/env python") || /^(import|from) \w+/.test(head)) return "main.py";
  if (head.startsWith("#include")) return head.includes("iostream") ? "main.cpp" : "main.c";
  return "snippet.txt";
}

/** The language of a markdown code block, from the `language-xxx` class that the highlighter puts on <code>. */
export function langOf(children: unknown): string | null {
  const kids = Array.isArray(children) ? children : [children];
  for (const k of kids) {
    const cls = (k as { props?: { className?: string } } | null)?.props?.className;
    const m = typeof cls === "string" ? /language-([\w+#-]+)/.exec(cls) : null;
    if (m) return m[1];
  }
  return null;
}
